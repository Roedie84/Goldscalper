"""Tests voor netjes stoppen en veilig opstarten."""
import os, sys, asyncio
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.lifecycle import (
    DrainPolicy, LifecycleController, LifecycleState,
)


class FakePos:
    def __init__(self, ticket, closes_after=0):
        self.id = ticket
        self.ticket = ticket
        self._closes_after = closes_after


class Book:
    """Simuleert open posities die eventueel vanzelf sluiten."""
    def __init__(self, positions, auto_close_after_polls=None):
        self.positions = list(positions)
        self.closed = []
        self.polls = 0
        self._auto = auto_close_after_polls

    async def get_open(self):
        self.polls += 1
        if self._auto is not None and self.polls >= self._auto:
            self.positions = []
        return self.positions

    async def close(self, position, reason):
        self.closed.append((position.ticket, reason))
        self.positions = [p for p in self.positions if p.ticket != position.ticket]


# ---------------- reconciliatie ----------------

def test_reconcile_clean_start():
    c = LifecycleController()
    r = asyncio.run(c.reconcile([], []))
    assert r.consistent and c.state is LifecycleState.RUNNING


def test_reconcile_matching_positions():
    c = LifecycleController()
    r = asyncio.run(c.reconcile([{"ticket": 1}, {"ticket": 2}], ["1", "2"]))
    assert r.consistent and c.accepts_new_positions


def test_unknown_broker_position_blocks_trading():
    """Een positie die de broker kent maar de database niet, wordt door
    niemand bewaakt. Dat mag nooit stilzwijgend doorgaan."""
    c = LifecycleController()
    r = asyncio.run(c.reconcile([{"ticket": 99, "volume": 0.5}], []))
    assert not r.consistent
    assert c.state is LifecycleState.DIVERGED
    assert not c.accepts_new_positions
    assert r.orphaned_at_broker[0]["ticket"] == 99


def test_position_closed_while_ha_was_down_is_recoverable():
    """Stop geraakt terwijl HA uit stond: administratie bijwerken, niet blokkeren."""
    c = LifecycleController()
    r = asyncio.run(c.reconcile([], ["7"]))
    assert r.consistent
    assert r.missing_at_broker == ["7"]
    assert c.state is LifecycleState.RUNNING


# ---------------- drain ----------------

def test_drain_with_no_positions_is_immediately_safe():
    c = LifecycleController()
    b = Book([])
    r = asyncio.run(c.drain(b.get_open, b.close))
    assert r.completed and c.safe_to_restart


def test_drain_close_now_closes_everything():
    c = LifecycleController(drain_policy=DrainPolicy.CLOSE_NOW)
    b = Book([FakePos(1), FakePos(2)])
    r = asyncio.run(c.drain(b.get_open, b.close))
    assert r.completed and len(b.closed) == 2
    assert c.state is LifecycleState.SAFE_TO_RESTART


def test_drain_waits_for_natural_exit():
    c = LifecycleController(drain_policy=DrainPolicy.WAIT_THEN_CLOSE)
    b = Book([FakePos(1)], auto_close_after_polls=3)
    r = asyncio.run(c.drain(b.get_open, b.close, poll_interval=0.01, timeout=5))
    assert r.completed and b.closed == []  # vanzelf gesloten, niet geforceerd


def test_drain_force_closes_after_timeout():
    c = LifecycleController(drain_policy=DrainPolicy.WAIT_THEN_CLOSE)
    b = Book([FakePos(1)])
    r = asyncio.run(c.drain(b.get_open, b.close, poll_interval=0.01, timeout=0.05))
    assert r.completed and b.closed[0][1] == "drain_timeout"


def test_wait_for_exit_never_forces_and_reports_honestly():
    c = LifecycleController(drain_policy=DrainPolicy.WAIT_FOR_EXIT)
    b = Book([FakePos(1)])
    r = asyncio.run(c.drain(b.get_open, b.close, poll_interval=0.01, timeout=0.05))
    assert not r.completed
    assert not c.safe_to_restart
    assert b.closed == []
    assert "niet geforceerd" in r.message or "handmatig" in r.message


def test_no_new_positions_while_draining():
    c = LifecycleController(drain_policy=DrainPolicy.CLOSE_NOW)
    asyncio.run(c.reconcile([], []))
    assert c.accepts_new_positions
    b = Book([FakePos(1)])
    asyncio.run(c.drain(b.get_open, b.close))
    assert not c.accepts_new_positions


# ---------------- afsluiten ----------------

def test_emergency_shutdown_flushes_and_stops():
    c = LifecycleController()
    flushed = []
    asyncio.run(c.emergency_shutdown([lambda: flushed.append("db")]))
    assert flushed == ["db"] and c.state is LifecycleState.STOPPED


def test_failing_flush_does_not_block_shutdown():
    c = LifecycleController()
    def boom(): raise RuntimeError("db weg")
    asyncio.run(c.emergency_shutdown([boom]))
    assert c.state is LifecycleState.STOPPED


def test_ig_style_tickets_are_handled():
    """IG gebruikt sleutels als 'DIAAAAYCJETQ7A8'; alleen MetaTrader en OANDA
    leveren gehele getallen. Ervan uitgaan dat een ticket numeriek is liet de
    integratie omvallen zodra er een echte positie bij IG openstond."""
    c = LifecycleController()
    r = asyncio.run(c.reconcile(
        [{"ticket": "DIAAAAYCJETQ7A8", "volume": 1.0, "side": "buy"}],
        ["DIAAAAYCJETQ7A8"],
    ))
    assert r.consistent
    assert c.state is LifecycleState.RUNNING


def test_mixed_ticket_types_still_match():
    """Een getal uit de database naast een string van de broker mag niet als
    'onbekende positie' gelden."""
    c = LifecycleController()
    r = asyncio.run(c.reconcile([{"ticket": 12345}], ["12345"]))
    assert r.consistent
    assert not r.orphaned_at_broker
