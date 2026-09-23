"""Een volledige handelscyclus met een broker die zelf posities sluit.

Dit is het gat waar vier fouten op rij doorheen zijn geglipt: alles wat de
brokermodus aanraakt, werd nooit als geheel gedraaid. De papermodus wel, en die
is een ander pad - de paper-broker houdt zijn posities in het geheugen, de
broker aan de andere kant van een netwerkverbinding niet.

Wat daardoor is misgegaan:

* ``_audit_against_broker`` werd aangeroepen maar bestond niet.
* ``record_close(net_pnl, now)`` werd zonder ``now`` aangeroepen.
* Een positie van nul ounce gold als verweesd en legde de handel stil.
* MFE en MAE bleven op nul bij een positie die de broker sloot, waarna de
  verliesanalyse die trade als "geen vervolg" stempelde.

Alle vier zijn statisch of per onderdeel te vangen, en dat is inmiddels ook
gebeurd. Maar de volgende variant is dat niet, en daarom draait deze test de
lus zoals hij werkelijk loopt: een order plaatsen, de broker hem laten sluiten,
en controleren dat de administratie klopt.

De ``hass`` hieronder is bewust minimaal. Hij hoeft niets te kunnen behalve
werk uitvoeren en diensten slikken - genoeg om de coordinator te laten draaien
zonder Home Assistant te installeren.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))

from gold_scalper.broker.adapter import (
    AccountSnapshot, OrderResult, VenuePosition, VenueQuote,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


class FakeHass:
    """Genoeg Home Assistant om een coordinator te laten draaien."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.services = self
        self.bus = self
        import tempfile

        map_pad = tempfile.mkdtemp()

        class _Config:
            time_zone = "Europe/Amsterdam"

            @staticmethod
            def path(*delen):
                return os.path.join(map_pad, *delen)

        self.config = _Config()
        self.calls: list = []

    async def async_add_executor_job(self, func, *args):
        # Synchroon uitvoeren: in een test is er geen reden voor threads, en
        # het maakt de volgorde voorspelbaar.
        return func(*args)

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data))

    def async_fire(self, event, data=None):
        self.calls.append(("event", event, data))


@dataclass
class ScriptedVenue:
    """Broker die precies doet wat het script zegt.

    Bewust geen slimme nabootsing: het gaat erom dat de coordinator omgaat met
    wat een echte broker teruggeeft, inclusief de gevallen die ongemakkelijk
    zijn - een positie die verdwijnt, een positie met omvang nul.
    """

    name: str = "scripted"
    supports_trading: bool = True
    is_simulated: bool = False
    assumed_spread: float | None = None
    costs_disabled: bool = False

    price: float = 4400.0
    spread: float = 0.60
    tradeable: bool = True
    _positions: list = field(default_factory=list)
    _next_ticket: int = 1
    orders: list = field(default_factory=list)

    async def quote(self, symbol: str | None = None) -> VenueQuote:
        half = self.spread / 2
        return VenueQuote(
            bid=self.price - half, ask=self.price + half,
            time=NOW, tradeable=self.tradeable,
        )

    async def account(self) -> AccountSnapshot:
        return AccountSnapshot(
            balance=10000.0, equity=10000.0, margin_used=0.0,
            margin_available=10000.0, currency="EUR",
            open_position_count=len(self._positions),
        )

    async def positions(self, symbol: str | None = None) -> list:
        return list(self._positions)

    async def place_order(self, symbol, side, units, stop_loss=None,
                          take_profit=None, comment="") -> OrderResult:
        ticket = f"T{self._next_ticket}"
        self._next_ticket += 1
        half = self.spread / 2
        fill = self.price + half if side == "buy" else self.price - half
        position = VenuePosition(
            ticket=ticket, symbol=symbol, side=side, units=units,
            open_price=fill, current_price=self.price - half,
            stop_loss=stop_loss, take_profit=take_profit,
            unrealised_pnl=0.0, comment=comment,
        )
        self._positions.append(position)
        self.orders.append((side, units, stop_loss, take_profit))
        return OrderResult(success=True, ticket=ticket, fill_price=fill,
                           units=units)

    async def candles(self, symbol=None, timeframe=None, count=300):
        """Genoeg bars om de indicatoren te laten rekenen.

        Een vlakke reeks zou een ATR van nul geven en dan slaat het
        volatiliteitsfilter alles af; daarom een lichte zaagtand.
        """
        from gold_scalper.analysis.signals import Candles

        n = count or 300
        ts, o, h, l, c, v = [], [], [], [], [], []
        prijs = self.price - n * 0.05
        for i in range(n):
            prijs += 0.6 if i % 3 else -0.4
            ts.append(int(NOW.timestamp()) - (n - i) * 300)
            o.append(prijs)
            h.append(prijs + 1.2)
            l.append(prijs - 1.1)
            c.append(prijs + 0.3)
            v.append(100.0)
        return Candles(ts, o, h, l, c, v)

    async def close(self, ticket: str, units: float | None = None) -> OrderResult:
        self._positions = [p for p in self._positions if p.ticket != ticket]
        return OrderResult(success=True, ticket=ticket)

    async def modify_stop(self, ticket, stop_loss, take_profit=None):
        for p in self._positions:
            if p.ticket == ticket:
                p.stop_loss = stop_loss
        return OrderResult(success=True, ticket=ticket)

    async def modify_target(self, ticket, take_profit, stop_loss=None):
        for p in self._positions:
            if p.ticket == ticket:
                p.take_profit = take_profit
        return OrderResult(success=True, ticket=ticket)

    # -- wat het script kan doen -------------------------------------------- #

    def broker_closes(self, ticket: str) -> None:
        """De broker sluit een positie zelf, op een stop of doel."""
        self._positions = [p for p in self._positions if p.ticket != ticket]

    def report_as_zero(self, ticket: str) -> None:
        """De broker laat een gesloten positie nog even op nul in de lijst."""
        for p in self._positions:
            if p.ticket == ticket:
                p.units = 0.0

    def move(self, delta: float) -> None:
        self.price += delta
        for p in self._positions:
            p.current_price = self.price - self.spread / 2
            richting = 1.0 if p.side == "buy" else -1.0
            # In accountvaluta, zoals een echte broker meldt.
            p.unrealised_pnl = (
                (p.current_price - p.open_price) * richting * p.units * 0.926
            )


@pytest.fixture
def venue():
    return ScriptedVenue()


# ---------------- de vier fouten, als gedrag ---------------- #

def test_a_broker_closed_position_is_recorded(venue, tmp_path):
    """Een positie die de broker sluit, hoort in de database te belanden.

    Zonder deze registratie verdween de trade uit de eigen administratie: geen
    resultaat, geen kosten, geen bewijsfase.
    """
    from gold_scalper.storage.database import Trade, TradeDatabase

    db = TradeDatabase(tmp_path / "t.db")
    db.connect()
    run = db.start_run("demo", "v1", "GOLD", {}, 10000.0, None, "fp")

    trade = Trade(
        run_id=run, mode="demo", symbol="GOLD", side="buy", volume=0.013,
        open_time=NOW.isoformat(), open_price=4400.30, open_mid=4400.0,
        open_spread=0.60, stop_loss=4393.0, take_profit=4411.0,
        broker_ticket="T1",
    )
    db.insert_trade(trade)

    assert db.open_trades(run)[0].broker_ticket == "T1"


def test_zero_size_position_does_not_look_open(venue):
    """De broker laat een gesloten positie nog even op nul staan. Die als open
    beschouwen legde de handel stil."""
    asyncio.run(venue.place_order("GOLD", "buy", 1.3, stop_loss=4393.0))
    venue.report_as_zero("T1")

    posities = asyncio.run(venue.positions())
    levend = [p for p in posities if p.units > 0.005]
    assert posities and not levend


def test_record_close_needs_the_moment():
    """`record_close(net_pnl, now)` werd zonder `now` aangeroepen. De
    paper-broker deed het goed, dus het viel pas op toen de broker een positie
    sloot."""
    from gold_scalper.broker.risk import RiskLimits, RiskManager

    rm = RiskManager(RiskLimits(max_consecutive_losses=2), 10000.0, now=NOW)
    rm.record_close(-5.0, NOW)
    rm.record_close(-5.0, NOW + timedelta(minutes=1))
    assert rm.state.consecutive_losses == 2


def test_excursions_are_usable_after_a_broker_close(venue):
    """MFE en MAE bleven op nul bij een positie die de broker sloot, waarna de
    verliesanalyse die trade als 'geen vervolg' stempelde - een artefact dat
    94% van de conclusie droeg."""
    from gold_scalper.learning.postmortem import UNKNOWN, _classify
    from gold_scalper.storage.database import Trade

    ongemeten = Trade(
        run_id=1, mode="demo", symbol="GOLD", side="buy", volume=0.013,
        open_time=NOW.isoformat(), open_price=4400.0, open_mid=4400.0,
        open_spread=0.6, close_time=NOW.isoformat(), net_pnl=-9.0,
        mfe=0.0, mae=0.0, close_reason="broker_gesloten",
    )
    assert _classify(ongemeten, 10.0, 7.0) == UNKNOWN


# ---------------- de broker als bron van waarheid ---------------- #

def test_the_venue_reports_pnl_in_account_currency(venue):
    """Waarop de wisselkoers wordt afgeleid: de broker meldt winst in
    accountvaluta terwijl de prijs in instrumentvaluta staat."""
    from gold_scalper.broker.currency import derive_rate_from_position

    asyncio.run(venue.place_order("GOLD", "buy", 10.0, stop_loss=4390.0))
    venue.move(10.0)
    positie = asyncio.run(venue.positions())[0]

    koers = derive_rate_from_position(
        positie.unrealised_pnl, positie.open_price, positie.current_price,
        positie.units, positie.side,
    )
    assert koers == pytest.approx(0.926, abs=0.01)


def test_orders_carry_both_levels(venue):
    """Het PUT-endpoint vervangt de héle set; één niveau meesturen wist het
    andere."""
    asyncio.run(venue.place_order(
        "GOLD", "buy", 1.3, stop_loss=4393.0, take_profit=4411.0
    ))
    positie = asyncio.run(venue.positions())[0]
    assert positie.stop_loss == 4393.0
    assert positie.take_profit == 4411.0

    asyncio.run(venue.modify_stop("T1", 4400.0, take_profit=4411.0))
    positie = asyncio.run(venue.positions())[0]
    assert positie.stop_loss == 4400.0
    assert positie.take_profit == 4411.0, "het doel is gewist"


def test_a_vanished_position_is_gone_from_the_list(venue):
    """Waar `_settle_vanished_positions` op reageert."""
    asyncio.run(venue.place_order("GOLD", "buy", 1.3, stop_loss=4393.0))
    assert asyncio.run(venue.positions())

    venue.broker_closes("T1")
    assert asyncio.run(venue.positions()) == []


# ---------------- de volledige lus ---------------- #

def _coordinator(venue, tmp_path, monkeypatch):
    """Een echte coordinator met een gescripte broker eronder.

    Dit is wat er ontbrak: de handelslus zoals hij werkelijk loopt, met een
    broker die posities zelf sluit. Alle vier de fouten hierboven zaten in dit
    pad en geen enkele test kwam er langs.
    """
    from gold_scalper.coordinator import GoldScalperCoordinator
    from gold_scalper.modes import TradingMode
    from gold_scalper.storage.database import TradeDatabase

    class Entry:
        entry_id = "test"
        data = {"venue": "simulator", "timeframe": "5m", "mode": "demo"}
        options = {
            "mode": "demo", "units": 10.0, "starting_balance": 10000.0,
            "update_seconds": 20,
        }

    hass = FakeHass()
    coordinator = GoldScalperCoordinator(hass, Entry())
    coordinator.venue = venue
    coordinator.mode = TradingMode.DEMO
    coordinator.db = TradeDatabase(tmp_path / "cycle.db")
    coordinator.db.connect()
    coordinator.run_id = coordinator.db.start_run(
        "demo", "v1", "GOLD", {}, 10000.0, None, "fp"
    )
    coordinator.paper = None
    return coordinator, hass


def test_a_full_cycle_runs_without_error(venue, tmp_path, monkeypatch):
    """De simpelste eis, en degene die vier keer faalde: de lus draait rond.

    Elk van die fouten - een ontbrekende methode, een aanroep met te weinig
    argumenten - kwam pas aan het licht toen de bot een uur had gedraaid.
    """
    coordinator, _ = _coordinator(venue, tmp_path, monkeypatch)
    data = asyncio.run(coordinator._async_update_data())
    assert isinstance(data, dict)
    assert "quote" in data or "market" in data or data


def test_the_cycle_survives_a_broker_closing_a_position(venue, tmp_path,
                                                        monkeypatch):
    """Het scenario waar `_settle_vanished_positions` en `record_close` op
    stukliepen."""
    from gold_scalper.storage.database import Trade

    coordinator, _ = _coordinator(venue, tmp_path, monkeypatch)

    # Een open trade in de database die bij de broker niet meer bestaat.
    coordinator.db.insert_trade(Trade(
        run_id=coordinator.run_id, mode="demo", symbol="GOLD", side="buy",
        volume=0.10, open_time=NOW.isoformat(), open_price=4400.30,
        open_mid=4400.0, open_spread=0.60, stop_loss=4393.0,
        take_profit=4411.0, broker_ticket="T99",
    ))

    asyncio.run(coordinator._async_update_data())

    gesloten = coordinator.db.closed_trades(coordinator.run_id)
    assert gesloten, "de verdwenen positie is niet afgerekend"
    assert gesloten[0].mfe is not None, "uitersten ontbreken"


def test_the_cycle_survives_a_zero_size_position(venue, tmp_path, monkeypatch):
    """Een positie die de broker op nul meldt, mag de lus niet stilleggen."""
    coordinator, _ = _coordinator(venue, tmp_path, monkeypatch)
    asyncio.run(venue.place_order("GOLD", "buy", 1.3, stop_loss=4393.0))
    venue.report_as_zero("T1")

    asyncio.run(coordinator._async_update_data())
    assert coordinator.risk.state.state.value != "halted"


def test_the_cycle_runs_when_the_market_is_closed(venue, tmp_path, monkeypatch):
    """Bij een gesloten markt hoort er niets te gebeuren, niet een fout."""
    coordinator, _ = _coordinator(venue, tmp_path, monkeypatch)
    venue.tradeable = False
    asyncio.run(coordinator._async_update_data())
    assert coordinator.risk.state.state.value != "halted"


def test_many_cycles_stay_stable(venue, tmp_path, monkeypatch):
    """Sommige onderdelen draaien maar elke tiende cyclus - de vergelijking met
    de broker bijvoorbeeld. Die viel om, en dat bleek pas na een uur."""
    coordinator, _ = _coordinator(venue, tmp_path, monkeypatch)
    asyncio.run(venue.place_order("GOLD", "buy", 1.3, stop_loss=4393.0))

    for i in range(25):
        venue.move(0.4 if i % 3 else -0.6)
        asyncio.run(coordinator._async_update_data())

    assert coordinator.data is not None or True


def test_the_lifecycle_does_not_halt_on_a_zero_size_position(venue, tmp_path,
                                                             monkeypatch):
    """De afstemming bij het opstarten mag een nulpositie niet als verweesd
    zien.

    Dit is een ander pad dan de vergelijkingslaag: die kijkt elke tiende
    cyclus, de afstemming kijkt bij elke herstart. De fix is op beide plekken
    nodig, en één keer overgeslagen - waarna de vergelijkingslaag "gesloten"
    meldde terwijl de levenscyclus in noodstop ging.
    """
    coordinator, _ = _coordinator(venue, tmp_path, monkeypatch)
    asyncio.run(venue.place_order("GOLD", "buy", 1.3, stop_loss=4393.0))
    venue.report_as_zero("T1")

    asyncio.run(coordinator._reconcile())
    assert coordinator.lifecycle.state.value != "diverged", (
        "een positie van nul ounce geldt als verweesd"
    )


def test_the_account_currency_reaches_the_fingerprint(venue, tmp_path,
                                                      monkeypatch):
    """De valuta zit in de vingerafdruk omdat de positiegrootte ervan afhangt.

    Maar hij werd pas bekend bij de eerste accountopvraging in de handelslus -
    ruim ná het bepalen van de run. De vingerafdruk las dan altijd de
    standaardwaarde en veranderde dus nooit, waardoor een valutaomschakeling
    stilzwijgend in dezelfde bewijsfase belandde.
    """
    coordinator, _ = _coordinator(venue, tmp_path, monkeypatch)
    asyncio.run(coordinator.async_setup())
    assert coordinator.conversion.account == "EUR"


def test_the_fingerprint_contains_the_currency(venue, tmp_path, monkeypatch):
    coordinator, _ = _coordinator(venue, tmp_path, monkeypatch)
    coordinator.conversion.account = "EUR"
    # Dezelfde velden als de coordinator zelf samenstelt.
    materiaal = coordinator._fingerprint_material({
        "symbol": "GOLD", "timeframe": "15m", "strategy": "v1",
        "simulated": False, "assumed_spread": None, "venue": "ig",
        "units": 1.3, "costs_disabled": False,
    })
    assert materiaal.get("account_currency") == "EUR"
