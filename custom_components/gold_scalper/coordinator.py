"""Coordinator: de handelslus, draaiend in het Home Assistant-proces.

Volgorde per cyclus, en die volgorde is niet willekeurig:

1. Koers ophalen. Zonder verse prijs gebeurt er verder niets.
2. Nieuwe afgesloten candle? Dan de incrementele indicatoren bijwerken.
3. **Open posities beheren.** Dit gaat vóór het zoeken naar nieuwe signalen.
   Een bestaande positie beschermen is altijd urgenter dan een nieuwe openen.
4. Risicotoetsen.
5. Pas dan strategie evalueren en eventueel openen.
6. Administratie wegschrijven.

Alles draait binnen HA. Er is geen tweede proces, geen bridge, geen Windows.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .analysis.signals import Candles
from .broker.adapter import ExecutionVenue, VenueError, VenueQuote
from .broker.exits import ExitConfig, ExitManager
from .broker.oanda import OandaVenue
from .broker.simulator import SimulatorVenue
from .broker.paper import CONTRACT_SIZE, BrokerCosts, PaperBroker
from .broker.paper import Quote as PaperQuote
from .broker.risk import RiskLimits, RiskManager, TradingState
from .const import (
    CONF_ACCOUNT_ID, CONF_SIM_SEED, CONF_SIM_SPREAD, CONF_VENUE,
    DEFAULT_SIM_SEED, DEFAULT_SIM_SPREAD, DEFAULT_VENUE, VENUE_SIMULATOR, CONF_ENTRY_THRESHOLD, CONF_ENVIRONMENT, CONF_EQUITY_FLOOR_PCT,
    CONF_MAX_CONSECUTIVE_LOSSES, CONF_MAX_DAILY_LOSS_PCT, CONF_MAX_SPREAD,
    CONF_MAX_TRADES_PER_DAY, CONF_MAX_UNITS, CONF_MIN_EDGE_MULTIPLE, CONF_MODE,
    CONF_STARTING_BALANCE, CONF_SYMBOL, CONF_TIMEFRAME, CONF_TOKEN,
    CONF_TRADING_END_HOUR, CONF_TRADING_START_HOUR, CONF_UNITS, CONF_UPDATE_SECONDS,
    DATABASE_FILENAME, DEFAULT_ENVIRONMENT, DEFAULT_MAX_UNITS, DEFAULT_MODE,
    DEFAULT_STARTING_BALANCE, DEFAULT_SYMBOL, DEFAULT_TIMEFRAME, DEFAULT_UNITS,
    DEFAULT_UPDATE_SECONDS, DOMAIN, MIN_UPDATE_SECONDS, WARMUP_CANDLES,
)
from .lifecycle import DrainPolicy, LifecycleController
from .modes import LiveGate, ModeLockedError, TradingMode, require_live_unlocked
from .storage import performance
from .storage.database import MODE_LIVE, MODE_PAPER, TradeDatabase
from .storage.latency import LatencyBudget, LatencyTracker, install_buffered_signals
from .strategy.scalping import STRATEGY_VERSION, ScalpConfig, evaluate
from .strategy.streaming import StreamState

_LOGGER = logging.getLogger(__name__)


class GoldScalperCoordinator(DataUpdateCoordinator[dict]):
    """Houdt de handelslus, de administratie en alle bewaking bij elkaar."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        options = {**entry.data, **entry.options}
        self.entry = entry
        self.symbol: str = options.get(CONF_SYMBOL, DEFAULT_SYMBOL)
        self.timeframe: str = options.get(CONF_TIMEFRAME, DEFAULT_TIMEFRAME)
        self.mode = TradingMode(options.get(CONF_MODE, DEFAULT_MODE))
        self.units: float = options.get(CONF_UNITS, DEFAULT_UNITS)
        self.starting_balance: float = options.get(
            CONF_STARTING_BALANCE, DEFAULT_STARTING_BALANCE
        )

        if options.get(CONF_VENUE, DEFAULT_VENUE) == VENUE_SIMULATOR:
            self.venue: ExecutionVenue = SimulatorVenue(
                seed=options.get(CONF_SIM_SEED, DEFAULT_SIM_SEED),
                spread=options.get(CONF_SIM_SPREAD, DEFAULT_SIM_SPREAD),
                balance=self.starting_balance,
            )
            # Een simulatorrun is per definitie papierhandel; live zou hier
            # betekenisloos zijn en de poort blokkeert hem toch.
            self.mode = TradingMode.PAPER
        else:
            self.venue = OandaVenue(
                session=async_get_clientsession(hass),
                token=options[CONF_TOKEN],
                account_id=options[CONF_ACCOUNT_ID],
                environment=options.get(CONF_ENVIRONMENT, DEFAULT_ENVIRONMENT),
                # Live handel wordt pas ingeschakeld nadat de poort opengaat;
                # zie _refresh_gate. Bij het opstarten staat hij altijd dicht.
                trading_enabled=False,
                max_units=options.get(CONF_MAX_UNITS, DEFAULT_MAX_UNITS),
            )

        self.strategy_cfg = ScalpConfig(
            max_spread=options.get(CONF_MAX_SPREAD, 0.30),
            min_edge_multiple=options.get(CONF_MIN_EDGE_MULTIPLE, 2.0),
            entry_threshold=options.get(CONF_ENTRY_THRESHOLD, 0.45),
            trading_hours_utc=(
                options.get(CONF_TRADING_START_HOUR, 7),
                options.get(CONF_TRADING_END_HOUR, 20),
            ),
            commission_per_lot_per_side=0.0,  # OANDA rekent in de spread
            volume=self.units / CONTRACT_SIZE,
        )

        self.risk = RiskManager(
            RiskLimits(
                max_daily_loss_pct=options.get(CONF_MAX_DAILY_LOSS_PCT, 2.0),
                max_trades_per_day=options.get(CONF_MAX_TRADES_PER_DAY, 100),
                max_consecutive_losses=options.get(CONF_MAX_CONSECUTIVE_LOSSES, 5),
                equity_floor_pct=options.get(CONF_EQUITY_FLOOR_PCT, 80.0),
                max_volume=options.get(CONF_MAX_UNITS, DEFAULT_MAX_UNITS) / CONTRACT_SIZE,
            ),
            self.starting_balance,
        )

        self.lifecycle = LifecycleController(DrainPolicy.WAIT_THEN_CLOSE)
        self.exits = ExitManager(ExitConfig())
        self.latency = LatencyTracker()
        self.state = StreamState()
        self.gate = LiveGate().evaluate({}, {}, []).as_dict()

        self.db: TradeDatabase | None = None
        self.run_id: int | None = None
        self.paper: PaperBroker | None = None
        self._candles: Candles | None = None
        self._last_bar_ts: int = 0
        self._last_entry_ts: float = 0.0
        self._partial_taken: set[str] = set()
        self._last_quote: VenueQuote | None = None
        self._last_signal = None
        self._enabled: bool = False

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.symbol}",
            update_interval=timedelta(
                seconds=max(
                    MIN_UPDATE_SECONDS,
                    options.get(CONF_UPDATE_SECONDS, DEFAULT_UPDATE_SECONDS),
                )
            ),
        )

    # -- opstarten ---------------------------------------------------------- #

    async def async_setup(self) -> None:
        """Database openen, historie ophalen, afstemmen met de broker."""
        path = self.hass.config.path(DATABASE_FILENAME)
        self.db = TradeDatabase(path)
        await self.hass.async_add_executor_job(self.db.connect)
        install_buffered_signals(self.db)

        config = {
            "symbol": self.symbol, "timeframe": self.timeframe,
            "mode": self.mode.value, "units": self.units,
            "strategy": STRATEGY_VERSION,
            "venue": self.venue.name,
            # Wordt door LiveGate gelezen. Zonder dit merkteken zou een
            # geslaagde simulatie de poort kunnen openen.
            "simulated": getattr(self.venue, "is_simulated", False),
        }
        self.run_id = await self.hass.async_add_executor_job(
            self.db.start_run, self.mode.value, STRATEGY_VERSION,
            self.symbol, config, self.starting_balance, None,
        )

        if self.mode is not TradingMode.LIVE:
            self.paper = PaperBroker(
                self.db, self.run_id, self.symbol, self.starting_balance,
                BrokerCosts(commission_per_lot_per_side=0.0),
            )

        # Historie opwarmen. Zonder dit begint elke herstart met een blinde
        # periode van 60 candles - bij 1m een heel uur.
        try:
            self._candles = await self.venue.candles(
                self.symbol, self.timeframe, WARMUP_CANDLES
            )
            self.state = StreamState()
            await self.hass.async_add_executor_job(self.state.warm_up, self._candles)
            self._last_bar_ts = self._candles.timestamp[-1]
            _LOGGER.info(
                "Opgewarmd met %d candles voor %s op %s",
                len(self._candles), self.symbol, self.timeframe,
            )
        except VenueError as err:
            raise UpdateFailed(f"Kon historie niet ophalen: {err}") from err

        await self._reconcile()
        await self._refresh_gate()

    async def _reconcile(self) -> None:
        """Vergelijk de broker met onze database voordat er iets gebeurt."""
        try:
            broker_positions = await self.venue.positions(self.symbol)
        except VenueError as err:
            _LOGGER.warning("Kon posities niet ophalen bij afstemmen: %s", err)
            broker_positions = []

        open_trades = await self.hass.async_add_executor_job(
            self.db.open_trades, self.run_id
        )
        db_tickets = [int(t.mt5_ticket) for t in open_trades if t.mt5_ticket]

        result = await self.lifecycle.reconcile(
            [{"ticket": int(p.ticket), "volume": p.units, "side": p.side}
             for p in broker_positions],
            db_tickets,
        )
        if not result.consistent:
            self.risk.halt(result.message)

    async def _refresh_gate(self) -> None:
        """Herbereken of live handel vrijgegeven mag worden."""
        if self.run_id is None:
            return
        stats = await self.hass.async_add_executor_job(
            performance.compute_for_run, self.db, self.run_id
        )
        run = await self.hass.async_add_executor_job(self.db.get_run, self.run_id)
        trades = await self.hass.async_add_executor_job(self.db.closed_trades, self.run_id)
        daily = performance.daily_breakdown(trades)
        self.gate = LiveGate().evaluate(stats, run or {}, daily).as_dict()

        # De venue mag alleen handelen als álles klopt: live modus, poort open,
        # en de gebruiker heeft de schakelaar bewust omgezet.
        self.venue.supports_trading = bool(
            self.mode is TradingMode.LIVE and self.gate["unlocked"] and self._enabled
        )

    # -- bediening ---------------------------------------------------------- #

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def async_set_enabled(self, value: bool) -> None:
        self._enabled = value
        await self._refresh_gate()
        await self.async_request_refresh()

    async def async_prepare_shutdown(self) -> dict:
        """Wikkel af zodat HA veilig herstart kan worden."""
        result = await self.lifecycle.drain(self._open_positions, self._close_position)
        await self.async_request_refresh()
        return result.as_dict()

    async def async_close_all(self) -> None:
        for position in await self._open_positions():
            await self._close_position(position, "handmatig")
        await self.async_request_refresh()

    async def async_resume(self) -> None:
        """Hervat na een noodstop. Bewust handmatig."""
        self.risk.manual_resume()
        await self._reconcile()
        await self.async_request_refresh()

    # -- posities ----------------------------------------------------------- #

    async def _open_positions(self) -> list:
        if self.mode is TradingMode.LIVE:
            return await self.venue.positions(self.symbol)
        return self.paper.open_positions if self.paper else []

    async def _close_position(self, position, reason: str) -> None:
        if self.mode is TradingMode.LIVE:
            await self.venue.close(position.ticket)
            return
        if self.paper and self._last_quote:
            self.paper.close_position(position, self._paper_quote(self._last_quote), reason)

    def _paper_quote(self, quote: VenueQuote) -> PaperQuote:
        return PaperQuote(
            bid=quote.bid, ask=quote.ask, time=quote.time,
            atr=self.state.atr.value or 0.0,
        )

    # -- de lus ------------------------------------------------------------- #

    async def _async_update_data(self) -> dict:
        budget = LatencyBudget()
        budget.mark("start")

        try:
            quote = await self.venue.quote(self.symbol)
        except VenueError as err:
            raise UpdateFailed(f"Geen koers beschikbaar: {err}") from err
        self._last_quote = quote
        budget.mark("quote")

        now = datetime.now(timezone.utc)
        tick_age = (now - quote.time).total_seconds()

        # -- nieuwe candle? -------------------------------------------------- #
        await self._maybe_update_candles()
        budget.mark("candles")

        # -- open posities beheren, vóór alles anders ------------------------ #
        await self._manage_open_positions(quote, now)
        budget.mark("exits")

        # -- boekhouding ----------------------------------------------------- #
        if self.paper:
            self.paper.update_positions(self._paper_quote(quote))
            balance, equity = self.paper.balance, self.paper.equity(self._paper_quote(quote))
        else:
            try:
                snapshot = await self.venue.account()
                balance, equity = snapshot.balance, snapshot.equity
            except VenueError:
                balance = equity = self.starting_balance

        open_positions = await self._open_positions()

        # -- signaal --------------------------------------------------------- #
        signal = None
        if self._candles is not None and len(self._candles) >= 60:
            signal = await self.hass.async_add_executor_job(
                evaluate, self._candles, quote.bid, quote.ask, self.strategy_cfg,
                now.hour, len(open_positions),
                now.timestamp() - self._last_entry_ts,
            )
            self._last_signal = signal
        budget.mark("signal")

        # -- mag er gehandeld worden? ---------------------------------------- #
        reject_reason = None
        if signal is not None:
            if not self.lifecycle.accepts_new_positions:
                reject_reason = f"levenscyclus: {self.lifecycle.state.value}"
            elif not self._enabled:
                reject_reason = "handel staat uit"
            elif not signal.should_trade:
                reject_reason = signal.reject_reason
            else:
                allowed, why = self.risk.can_open(
                    now=now, balance=balance, equity=equity,
                    starting_balance=self.starting_balance,
                    open_positions=len(open_positions),
                    volume=self.units / CONTRACT_SIZE,
                    spread=quote.spread, last_tick_age=tick_age,
                )
                reject_reason = None if allowed else f"risico: {why}"

            if reject_reason is None and signal.should_trade:
                await self._open_position(signal, quote, now)

            await self.hass.async_add_executor_job(
                self.db.log_signal, self.run_id, signal.score, signal.confidence,
                "buy" if signal.direction > 0 else "sell" if signal.direction < 0 else "flat",
                None, quote.spread, reject_reason is None and signal.should_trade,
                reject_reason, signal.components,
            )

        await self.hass.async_add_executor_job(
            self.db.record_equity, self.run_id, balance, equity,
            len(open_positions), self.paper.cumulative_cost if self.paper else 0.0,
        )
        budget.mark("bookkeeping")
        self.latency.record(budget)

        stats = await self.hass.async_add_executor_job(
            performance.compute_for_run, self.db, self.run_id
        )

        return {
            "quote": quote,
            "price": quote.mid,
            "spread": quote.spread,
            "atr": self.state.atr.value,
            "signal": signal,
            "reject_reason": reject_reason,
            "balance": balance,
            "equity": equity,
            "open_positions": open_positions,
            "stats": stats,
            "gate": self.gate,
            "risk": self.risk.as_dict(),
            "lifecycle": self.lifecycle.as_dict(),
            "latency": self.latency.stats(),
            "mode": self.mode.value,
            "enabled": self._enabled,
        }

    async def _maybe_update_candles(self) -> None:
        """Haal nieuwe candles op als er een bar is afgesloten."""
        try:
            fresh = await self.venue.candles(self.symbol, self.timeframe, 3)
        except VenueError as err:
            _LOGGER.debug("Kon candles niet verversen: %s", err)
            return
        for i, ts in enumerate(fresh.timestamp):
            if ts <= self._last_bar_ts:
                continue
            self.state.push_candle(
                fresh.open[i], fresh.high[i], fresh.low[i], fresh.close[i], fresh.volume[i]
            )
            self._last_bar_ts = ts
            if self._candles is not None:
                for field in ("timestamp", "open", "high", "low", "close", "volume"):
                    getattr(self._candles, field).append(getattr(fresh, field)[i])
                    if len(self._candles.close) > WARMUP_CANDLES * 2:
                        getattr(self._candles, field).pop(0)

    async def _manage_open_positions(self, quote: VenueQuote, now: datetime) -> None:
        """Break-even, gedeeltelijk sluiten, trailing en tijdstops."""
        atr = self.state.atr.value or 0.0
        if atr <= 0:
            return
        cost = quote.spread + 0.04  # spread plus geschatte slippage beide zijden

        for position in await self._open_positions():
            ticket = str(getattr(position, "ticket", None) or getattr(position, "id", ""))
            opened = (
                position.open_time
                if getattr(position, "open_time", None)
                else datetime.fromisoformat(position.open_time)
                if isinstance(getattr(position, "open_time", None), str)
                else now
            )
            action = self.exits.evaluate(
                side=position.side,
                volume=getattr(position, "units", 0) or getattr(position, "volume", 0),
                open_price=position.open_price,
                current_stop=getattr(position, "stop_loss", None),
                bid=quote.bid, ask=quote.ask, atr=atr,
                opened_at=opened, now=now,
                round_trip_cost_per_oz=cost,
                partial_taken=ticket in self._partial_taken,
            )
            if action.is_noop:
                continue

            try:
                if action.kind == "close":
                    await self._close_position(position, action.reason[:60])
                elif action.kind == "modify_stop" and self.mode is TradingMode.LIVE:
                    await self.venue.modify_stop(ticket, action.new_stop)
                elif action.kind == "modify_stop":
                    position.stop_loss = action.new_stop
                elif action.kind == "partial_close":
                    units = (getattr(position, "units", 0) or 0) * action.close_fraction
                    if self.mode is TradingMode.LIVE and units > 0:
                        await self.venue.close(ticket, units)
                    self._partial_taken.add(ticket)
            except VenueError as err:
                _LOGGER.error("Exitactie %s mislukte: %s", action.kind, err)

    async def _open_position(self, signal, quote: VenueQuote, now: datetime) -> None:
        side = "buy" if signal.direction == 1 else "sell"
        try:
            if self.mode is TradingMode.LIVE:
                require_live_unlocked(self.mode, type("G", (), self.gate)())
                result = await self.venue.place_order(
                    self.symbol, side, self.units,
                    stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                    comment=STRATEGY_VERSION,
                )
                if not result.success:
                    _LOGGER.warning("Order geweigerd: %s", result.error)
                    return
            elif self.paper:
                self.paper.open_position(
                    side, self.units / CONTRACT_SIZE, self._paper_quote(quote),
                    signal.stop_loss, signal.take_profit,
                    signal.score, signal.confidence, None, signal.reason,
                )
            self.risk.record_open()
            self._last_entry_ts = now.timestamp()
        except (ModeLockedError, VenueError) as err:
            _LOGGER.error("Openen mislukt: %s", err)

    # -- afsluiten ---------------------------------------------------------- #

    async def async_shutdown_hook(self) -> None:
        """Bij het HA-stop-event: administratie veiligstellen."""
        flushes = []
        if self.db is not None:
            if hasattr(self.db, "flush_signals"):
                flushes.append(self.db.flush_signals)
            if self.run_id is not None:
                flushes.append(lambda: self.db.end_run(self.run_id))
            flushes.append(self.db.close)
        await self.lifecycle.emergency_shutdown(flushes)
