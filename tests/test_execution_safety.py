"""Bescherming rond echte orders.

Papermodus kent geen van deze storingen, en juist daarom moeten ze hier
afgevangen worden: het zijn de gevallen waar de bewijsfase je niets over heeft
geleerd omdat ze er niet in voorkwamen.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.broker.adapter import OrderResult, VenueError, VenuePosition
from gold_scalper.broker.execution_safety import (
    BrokerLimits, SafeExecutor, preflight,
)


class FakeVenue:
    """Venue die zich op commando kan misdragen."""

    name = "fake"

    def __init__(self, *, fill=True, attach_stop=True, order_raises=False,
                 stop_raises=False, close_raises=False):
        self._fill, self._attach_stop = fill, attach_stop
        self._order_raises, self._stop_raises = order_raises, stop_raises
        self._close_raises = close_raises
        self.positions_list: list[VenuePosition] = []
        self.orders_sent = 0
        self.closes = []
        self.stop_calls = []

    async def place_order(self, symbol, side, units, stop_loss=None,
                          take_profit=None, comment=""):
        self.orders_sent += 1
        if self._order_raises:
            # Order is wél uitgevoerd, maar het antwoord ging verloren.
            if self._fill:
                self.positions_list.append(VenuePosition(
                    ticket="T1", symbol=symbol, side=side, units=units,
                    open_price=3300.0, comment=comment,
                    stop_loss=stop_loss if self._attach_stop else None,
                ))
            raise VenueError("verbinding verbroken")
        if not self._fill:
            return OrderResult(success=False, error="geweigerd")
        self.positions_list.append(VenuePosition(
            ticket="T1", symbol=symbol, side=side, units=units,
            open_price=3300.0, comment=comment,
            stop_loss=stop_loss if self._attach_stop else None,
        ))
        return OrderResult(success=True, ticket="T1", fill_price=3300.0, units=units)

    async def positions(self, symbol=None):
        return list(self.positions_list)

    async def modify_stop(self, ticket, stop_loss):
        self.stop_calls.append((ticket, stop_loss))
        if self._stop_raises:
            raise VenueError("stop geweigerd")
        for p in self.positions_list:
            if p.ticket == ticket:
                p.stop_loss = stop_loss
        return OrderResult(success=True, ticket=ticket)

    async def close(self, ticket, units=None):
        if self._close_raises:
            raise VenueError("sluiten geweigerd")
        self.closes.append(ticket)
        self.positions_list = [p for p in self.positions_list if p.ticket != ticket]
        return OrderResult(success=True, ticket=ticket)


LIMITS = BrokerLimits(min_volume=0.01, max_volume=10, volume_step=0.01,
                      min_stop_distance=0.5, tick_size=0.01)


# ---------------- preflight ----------------

def test_volume_is_rounded_to_the_step():
    r = preflight("buy", 0.0137, 3300.0, 3299.0, None, LIMITS)
    assert r.volume == 0.01
    assert any("afgerond" in a for a in r.adjustments)


def test_volume_below_minimum_is_refused():
    r = preflight("buy", 0.001, 3300.0, 3299.0, None,
                  BrokerLimits(min_volume=0.01, volume_step=0.001))
    assert not r.ok
    assert any("minimum" in p for p in r.problems)


def test_stop_on_the_wrong_side_is_refused():
    r = preflight("buy", 1.0, 3300.0, 3301.0, None, LIMITS)
    assert not r.ok
    assert any("verkeerde kant" in p for p in r.problems)


def test_stop_too_close_is_refused_not_widened():
    """De stop verder wegzetten zou het risico vergroten zonder dat je het
    vroeg; dan liever geen trade."""
    r = preflight("buy", 1.0, 3300.0, 3299.9, None, LIMITS)
    assert not r.ok
    assert any("risico zou vergroten" in p or "risico vergroten" in p
               for p in r.problems)


def test_prices_are_snapped_to_the_tick():
    r = preflight("buy", 1.0, 3300.0, 3299.0037, None, LIMITS)
    assert r.stop_loss == 3299.0


# ---------------- onbeschermde positie ----------------

def test_order_without_stop_is_refused_outright():
    executor = SafeExecutor(FakeVenue(), LIMITS)
    with pytest.raises(VenueError, match="zonder stop-loss"):
        asyncio.run(executor.open_protected("XAU", "buy", 1.0, 3300.0, None))


def test_missing_stop_is_attached_afterwards():
    venue = FakeVenue(attach_stop=False)
    executor = SafeExecutor(venue, LIMITS)
    result, notes = asyncio.run(
        executor.open_protected("XAU", "buy", 1.0, 3300.0, 3299.0)
    )
    assert result.success
    assert venue.stop_calls == [("T1", 3299.0)]
    assert any("achteraf geplaatst" in n for n in notes)


def test_position_is_closed_when_stop_cannot_be_placed():
    """Het enige scenario met in principe onbegrensd verlies."""
    venue = FakeVenue(attach_stop=False, stop_raises=True)
    executor = SafeExecutor(venue, LIMITS)
    result, notes = asyncio.run(
        executor.open_protected("XAU", "buy", 1.0, 3300.0, 3299.0)
    )
    assert not result.success
    assert venue.closes == ["T1"]
    assert any("zonder stop" in n for n in notes)


def test_failure_to_close_is_escalated_loudly():
    venue = FakeVenue(attach_stop=False, stop_raises=True, close_raises=True)
    executor = SafeExecutor(venue, LIMITS)
    _, notes = asyncio.run(
        executor.open_protected("XAU", "buy", 1.0, 3300.0, 3299.0)
    )
    assert any("handmatig ingrijpen" in n.lower() for n in notes)


# ---------------- dubbele order ----------------

def test_lost_response_does_not_send_a_second_order():
    """Verbinding weg ná verzending: opnieuw sturen zou de positie verdubbelen."""
    venue = FakeVenue(order_raises=True)
    executor = SafeExecutor(venue, LIMITS)
    result, notes = asyncio.run(
        executor.open_protected("XAU", "buy", 1.0, 3300.0, 3299.0)
    )
    assert venue.orders_sent == 1
    assert result.success
    assert any("tóch uitgevoerd" in n for n in notes)


def test_genuinely_failed_order_still_raises():
    """Niet uitgevoerd én geen positie gevonden: dan hoort de fout door te komen."""
    venue = FakeVenue(order_raises=True, fill=False)
    executor = SafeExecutor(venue, LIMITS)
    with pytest.raises(VenueError):
        asyncio.run(executor.open_protected("XAU", "buy", 1.0, 3300.0, 3299.0))


# ---------------- periodieke controle ----------------

def test_audit_flags_positions_without_a_stop():
    venue = FakeVenue()
    venue.positions_list.append(VenuePosition(
        ticket="T9", symbol="XAU", side="buy", units=1.0,
        open_price=3300.0, stop_loss=None,
    ))
    problems = asyncio.run(SafeExecutor(venue, LIMITS).audit_positions("XAU"))
    assert len(problems) == 1
    assert "géén stop-loss" in problems[0]


def test_audit_is_quiet_when_everything_is_protected():
    venue = FakeVenue()
    venue.positions_list.append(VenuePosition(
        ticket="T9", symbol="XAU", side="buy", units=1.0,
        open_price=3300.0, stop_loss=3299.0,
    ))
    assert asyncio.run(SafeExecutor(venue, LIMITS).audit_positions("XAU")) == []


# ---------------- doel bij de broker ----------------

class VenueWithoutTarget(FakeVenue):
    """Vult de order maar zet het doel niet, zoals IG doet als het niveau te
    dicht bij de markt ligt."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.target_calls = []

    async def place_order(self, symbol, side, units, stop_loss=None,
                          take_profit=None, comment=""):
        # Doel bewust weglaten.
        return await super().place_order(
            symbol, side, units, stop_loss=stop_loss, comment=comment
        )

    async def modify_target(self, ticket, take_profit):
        self.target_calls.append((ticket, take_profit))
        for p in self.positions_list:
            if p.ticket == ticket:
                p.take_profit = take_profit
        return OrderResult(success=True, ticket=ticket)


def test_missing_target_is_placed_afterwards():
    """Zonder deze controle blijft je winst onbeschermd als HA uitvalt."""
    venue = VenueWithoutTarget()
    executor = SafeExecutor(venue, LIMITS)
    result, notes = asyncio.run(executor.open_protected(
        "XAU", "buy", 1.0, 3300.0, stop_loss=3299.0, take_profit=3305.0
    ))
    assert result.success
    assert venue.target_calls == [("T1", 3305.0)]
    assert any("Doel achteraf" in n for n in notes)


def test_a_missing_target_does_not_block_the_trade():
    """Anders dan bij de stop: geen doel is hinderlijk, geen stop is
    gevaarlijk. De trade mag doorgaan."""
    class Stubborn(VenueWithoutTarget):
        async def modify_target(self, ticket, take_profit):
            raise VenueError("te dicht bij de markt")

    venue = Stubborn()
    result, notes = asyncio.run(
        SafeExecutor(venue, LIMITS).open_protected(
            "XAU", "buy", 1.0, 3300.0, stop_loss=3299.0, take_profit=3305.0
        )
    )
    assert result.success, "een ontbrekend doel mag de trade niet blokkeren"
    assert any("niet beschermd" in n for n in notes)


def test_target_present_needs_no_action():
    """Zet de venue het doel meteen goed, dan hoeft er niets te gebeuren."""
    venue = VenueWithoutTarget()

    async def _with_target(symbol, side, units, stop_loss=None,
                           take_profit=None, comment=""):
        position = VenuePosition(
            ticket="T1", symbol=symbol, side=side, units=units,
            open_price=3300.0, stop_loss=stop_loss, take_profit=take_profit,
            comment=comment,
        )
        venue.positions_list.append(position)
        return OrderResult(success=True, ticket="T1", fill_price=3300.0)

    venue.place_order = _with_target
    result, notes = asyncio.run(
        SafeExecutor(venue, LIMITS).open_protected(
            "XAU", "buy", 1.0, 3300.0, stop_loss=3299.0, take_profit=3305.0
        )
    )
    assert result.success
    assert venue.target_calls == []


def test_a_venue_without_modify_target_does_not_crash():
    """Blind aanroepen laat de hele orderafhandeling crashen op een adapter die
    de methode niet heeft - een veel groter probleem dan een ontbrekend doel."""
    venue = FakeVenue(attach_stop=True)
    # FakeVenue kent modify_target niet.
    assert not hasattr(venue, "modify_target")
    result, notes = asyncio.run(
        SafeExecutor(venue, LIMITS).open_protected(
            "XAU", "buy", 1.0, 3300.0, stop_loss=3299.0, take_profit=3305.0
        )
    )
    assert result.success
