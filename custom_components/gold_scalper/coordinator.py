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

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .analysis.signals import Candles
from .broker.adapter import ExecutionVenue, VenueError, VenueQuote
from .broker.execution_safety import BrokerLimits, SafeExecutor
from .broker.reconcile_audit import compare_positions
from .broker.schedule import SPOT_GOLD, cross_check, minutes_until_close
from .broker.exits import ExitConfig, ExitManager
from .broker.ig_capital import CapitalVenue, IgVenue
from .broker.oanda import OandaVenue
from .broker.public_data import PublicDataVenue
from .broker.stooq import StooqVenue
from .broker.simulator import SimulatorVenue
from .broker.paper import CONTRACT_SIZE, BrokerCosts, PaperBroker
from .broker.paper import Quote as PaperQuote
from .broker.risk import RiskLimits, RiskManager, TradingState
from .const import (
    CONF_ACCOUNT_ID, CONF_API_KEY, CONF_ASSUMED_SPREAD, CONF_BUILD_FROM_QUOTES,
    CONF_NOTIFY_CRITICAL, CONF_NOTIFY_HOURLY, CONF_NOTIFY_SERVICE,
    CONF_CLOSE_BUFFER_MINUTES, CONF_USE_SCHEDULE,
    CONF_NOTIFY_SKIP_QUIET, CONF_PYRAMID_ENABLED, CONF_PYRAMID_MAX_ADDITIONS,
    CONF_PYRAMID_TRIGGER_ATR, CONF_RISK_BASED_SIZING, CONF_RISK_PER_TRADE_PCT,
    CONF_SCALE_WITH_CONFIDENCE, CONF_STOP_LOSS_ATR, CONF_STOP_LOSS_USD,
    CONF_TAKE_PROFIT_ATR, CONF_TAKE_PROFIT_USD, NOTIFY_NONE,
    CONF_ENFORCE_TRADING_HOURS,
    CONF_EPIC, CONF_IDENTIFIER, CONF_PASSWORD, DEFAULT_EPIC, VENUE_CAPITAL, VENUE_IG,
    CONF_REGIME_SWITCHING, DEFAULT_ASSUMED_SPREAD, VENUE_PUBLIC,
    VENUE_STOOQ,
    CONF_SIM_SEED, CONF_SIM_SPREAD, CONF_VENUE,
    DEFAULT_SIM_SEED, DEFAULT_SIM_SPREAD, DEFAULT_VENUE, VENUE_SIMULATOR, CONF_ENTRY_THRESHOLD, CONF_ENVIRONMENT, CONF_EQUITY_FLOOR_PCT,
    CONF_MAX_CONSECUTIVE_LOSSES, CONF_MAX_DAILY_LOSS_PCT, CONF_MAX_RESUMES_PER_DAY, CONF_MAX_SPREAD, CONF_MAX_SPREAD_ATR,
    CONF_MAX_TRADES_PER_DAY, CONF_MAX_UNITS, CONF_MIN_EDGE_MULTIPLE, CONF_MODE,
    CONF_STARTING_BALANCE, CONF_SYMBOL, CONF_TIMEFRAME, CONF_TOKEN,
    CONF_TRADING_END_HOUR, CONF_TRADING_START_HOUR, CONF_UNITS, CONF_UPDATE_SECONDS,
    DATABASE_FILENAME, DEFAULT_ENVIRONMENT, DEFAULT_MAX_UNITS, DEFAULT_MODE,
    DEFAULT_STARTING_BALANCE, DEFAULT_SYMBOL, DEFAULT_TIMEFRAME, DEFAULT_UNITS,
    DEFAULT_UPDATE_SECONDS, DOMAIN, MIN_UPDATE_SECONDS, MIN_WARMUP_CANDLES,
    WARMUP_CANDLES,
)
from .learning.analysis import evaluate_threshold, measure_execution, regime_performance
from .learning.postmortem import analyse_losses
from .lifecycle import DrainPolicy, LifecycleController
from .notify import Notifier, NotifierConfig
from .status import build_status
from .modes import LiveGate, ModeLockedError, TradingMode, require_live_unlocked
from .storage import performance
from .storage.periods import build_periods
from .storage.database import MODE_LIVE, MODE_PAPER, Trade, TradeDatabase
from .storage.state import RuntimeState, StateStore
from .storage.latency import LatencyBudget, LatencyTracker, install_buffered_signals
from .strategy.scalping import STRATEGY_VERSION, ScalpConfig, evaluate
from .learning.robustness import evaluate_robustness
from .strategy.aggregator import QuoteAggregator
from .strategy.pyramid import PyramidConfig, consider_addition
from .strategy.sizing import SizingConfig, position_size
from .strategy.streaming import StreamState

_LOGGER = logging.getLogger(__name__)


def _as_datetime(value, fallback: datetime) -> datetime:
    """Zet open_time om naar een datetime, ongeacht de vorm.

    De paper-broker bewaart ISO-strings in de database; de venue-adapter geeft
    datetimes terug. Beide komen in dezelfde lus binnen, dus de omzetting hoort
    op één plek te staan in plaats van in een reeks ternaire expressies waar
    makkelijk een tak fout gaat.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return fallback
    return fallback


def _as_int(value, fallback: int) -> int:
    """Config-flow-waarden komen als float binnen; coërceer op de grens."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


@dataclass(slots=True)
class _TicketOnly:
    """Minimale positieverwijzing voor het afsluiten van een verdwenen trade.

    Alleen het ticketnummer is nodig; de rest staat al in de database. Een
    klasse in plaats van een dict-truc, zodat de attribuutnaam vastligt.
    """

    ticket: str


class GoldScalperCoordinator(DataUpdateCoordinator[dict]):
    """Houdt de handelslus, de administratie en alle bewaking bij elkaar."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        options = {**entry.data, **entry.options}
        self.entry = entry
        self.symbol: str = options.get(CONF_SYMBOL, DEFAULT_SYMBOL)
        self.timeframe: str = options.get(CONF_TIMEFRAME, DEFAULT_TIMEFRAME)
        #: Wat de gebruiker koos. Kan afwijken van ``self.mode`` als de
        #: databron niet kan uitvoeren - zie ``mode_override_reason``.
        requested = options.get(CONF_MODE, DEFAULT_MODE)
        # Bestaande entries kunnen 'backtest' bevatten uit een eerdere versie.
        if requested == TradingMode.BACKTEST.value:
            requested = TradingMode.PAPER.value
        self.requested_mode = TradingMode(requested)
        self.mode = self.requested_mode
        self.mode_override_reason: str | None = None
        self.units: float = options.get(CONF_UNITS, DEFAULT_UNITS)
        self.starting_balance: float = options.get(
            CONF_STARTING_BALANCE, DEFAULT_STARTING_BALANCE
        )

        venue_name = options.get(CONF_VENUE, DEFAULT_VENUE)

        # Brokers gebruiken hun eigen instrumentcode (bij IG een 'epic'), en
        # die staat in een apart veld. Bij herconfigureren blijft het oude
        # symbool uit de vorige databron in de entry staan, en dat werd hier
        # gebruikt - waarna elke aanroep de venue om 'XAU_USD' vroeg in plaats
        # van om 'CS.D.CFDGOLD.CFDGC.IP'. De broker antwoordde dan met een
        # 404 die niets over het instrument zei.
        if venue_name in (VENUE_IG, VENUE_CAPITAL):
            epic = options.get(CONF_EPIC)
            if epic:
                self.symbol = epic
        # Databronnen zonder uitvoering kunnen alleen papierhandel doen. Dat
        # stilzwijgend afdwingen is gevaarlijk: iemand die 'live' koos, denkt
        # dan dat het live staat. Daarom wordt de reden vastgelegd en via de
        # modus-sensor getoond.
        if (venue_name in (VENUE_PUBLIC, VENUE_STOOQ, VENUE_SIMULATOR)
                and self.mode is not TradingMode.PAPER):
            self.mode_override_reason = (
                f"Databron '{venue_name}' kan niet uitvoeren, dus modus "
                f"'{self.requested_mode.value}' is genegeerd en er wordt op "
                "papier gehandeld."
            )
            _LOGGER.warning(self.mode_override_reason)
        if venue_name == VENUE_PUBLIC:
            self.venue: ExecutionVenue = PublicDataVenue(
                session=async_get_clientsession(hass),
                symbol=self.symbol,
                assumed_spread=options.get(
                    CONF_ASSUMED_SPREAD, DEFAULT_ASSUMED_SPREAD
                ),
            )
            self.mode = TradingMode.PAPER
        elif venue_name in (VENUE_IG, VENUE_CAPITAL):
            factory = IgVenue if venue_name == VENUE_IG else CapitalVenue
            self.venue = factory(
                session=async_get_clientsession(hass),
                api_key=options[CONF_API_KEY],
                identifier=options[CONF_IDENTIFIER],
                password=options[CONF_PASSWORD],
                environment=options.get(CONF_ENVIRONMENT, "demo"),
                epic=options.get(CONF_EPIC, DEFAULT_EPIC),
                # Pas ingeschakeld nadat de poort opengaat; zie _refresh_gate.
                trading_enabled=False,
                max_units=options.get(CONF_MAX_UNITS, DEFAULT_MAX_UNITS),
            )
        elif venue_name == VENUE_STOOQ:
            self.venue = StooqVenue(
                session=async_get_clientsession(hass),
                symbol=self.symbol,
                assumed_spread=options.get(CONF_ASSUMED_SPREAD, DEFAULT_ASSUMED_SPREAD),
            )
            self.mode = TradingMode.PAPER
        elif venue_name == VENUE_SIMULATOR:
            self.venue = SimulatorVenue(
                seed=_as_int(options.get(CONF_SIM_SEED), DEFAULT_SIM_SEED),
                spread=options.get(CONF_SIM_SPREAD, DEFAULT_SIM_SPREAD),
                balance=self.starting_balance,
            )
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
            max_spread=options.get(CONF_MAX_SPREAD, 3.00),
            max_spread_atr_ratio=options.get(CONF_MAX_SPREAD_ATR, 0.35),
            min_edge_multiple=options.get(CONF_MIN_EDGE_MULTIPLE, 2.0),
            entry_threshold=options.get(CONF_ENTRY_THRESHOLD, 0.45),
            take_profit_atr=options.get(CONF_TAKE_PROFIT_ATR, 1.5),
            stop_loss_atr=options.get(CONF_STOP_LOSS_ATR, 1.0),
            take_profit_usd=options.get(CONF_TAKE_PROFIT_USD, 0.0),
            stop_loss_usd=options.get(CONF_STOP_LOSS_USD, 0.0),
            regime_switching=options.get(CONF_REGIME_SWITCHING, True),
            enforce_trading_hours=options.get(CONF_ENFORCE_TRADING_HOURS, False),
            # Alleen een broker levert een echte bied/laat-spread; publieke
            # bronnen en de simulator geven een aanname.
            real_spread=getattr(self.venue, "has_real_spread", True),
            trading_hours_utc=(
                _as_int(options.get(CONF_TRADING_START_HOUR), 7),
                _as_int(options.get(CONF_TRADING_END_HOUR), 20),
            ),
            commission_per_lot_per_side=0.0,  # OANDA rekent in de spread
            volume=self.units / CONTRACT_SIZE,
        )

        self.risk = RiskManager(
            RiskLimits(
                max_daily_loss_pct=options.get(CONF_MAX_DAILY_LOSS_PCT, 2.0),
                max_trades_per_day=_as_int(options.get(CONF_MAX_TRADES_PER_DAY), 100),
                max_consecutive_losses=_as_int(
                    options.get(CONF_MAX_CONSECUTIVE_LOSSES), 5
                ),
                equity_floor_pct=options.get(CONF_EQUITY_FLOOR_PCT, 80.0),
                max_volume=options.get(CONF_MAX_UNITS, DEFAULT_MAX_UNITS) / CONTRACT_SIZE,
                max_resumes_per_day=_as_int(
                    options.get(CONF_MAX_RESUMES_PER_DAY), 2
                ),
            ),
            self.starting_balance,
        )

        # Beschermingslaag rond echte orders. Papermodus kent de storingen die
        # hij afvangt niet, dus de bewijsfase leert je daar niets over.
        self.executor = SafeExecutor(
            self.venue,
            BrokerLimits(
                max_volume=options.get(CONF_MAX_UNITS, DEFAULT_MAX_UNITS),
                min_stop_distance=options.get("min_stop_distance", 0.0),
            ),
        )
        #: Cyclusteller voor de periodieke positiecontrole.
        self._audit_counter = 0
        #: Posities zoals ze deze cyclus bij de broker stonden. Aan het begin
        #: van elke cyclus gewist, en na elke order verversd.
        self._positions_cache: list | None = None

        self.sizing = SizingConfig(
            fixed_units=self.units,
            risk_based=options.get(CONF_RISK_BASED_SIZING, False),
            risk_per_trade_pct=options.get(CONF_RISK_PER_TRADE_PCT, 0.5),
            scale_with_confidence=options.get(CONF_SCALE_WITH_CONFIDENCE, False),
            max_units=options.get(CONF_MAX_UNITS, DEFAULT_MAX_UNITS),
        )
        self.pyramid = PyramidConfig(
            enabled=options.get(CONF_PYRAMID_ENABLED, False),
            trigger_atr=options.get(CONF_PYRAMID_TRIGGER_ATR, 1.0),
            max_additions=_as_int(options.get(CONF_PYRAMID_MAX_ADDITIONS), 2),
        )
        #: Per ticket: hoeveel toevoegingen en op welke prijs de laatste.
        self._pyramid_state: dict[str, dict] = {}
        #: Per ticket de uiterste mee- en tegenbeweging sinds de instap.
        #:
        #: Bij brokertrades werden die niet bijgehouden, terwijl de
        #: verliesanalyse erop filtert. Gevolg: die analyse sloeg elke
        #: demotrade over en meldde "0 verliezende trades" naast een
        #: performance die er wél telde - dood in precies de modus die ertoe
        #: doet.
        self._excursions: dict[str, dict] = {}
        self.robustness: dict = {}
        self.periods: dict = {}
        self.backtest: dict = {}
        self.audit: dict = {}
        self._use_schedule: bool = options.get(CONF_USE_SCHEDULE, True)
        self._close_buffer: int = _as_int(
            options.get(CONF_CLOSE_BUFFER_MINUTES), 10
        )
        self.schedule_note: str | None = None
        self.last_sizing: dict = {}

        service = options.get(CONF_NOTIFY_SERVICE, NOTIFY_NONE)
        self.notifier = Notifier(hass, NotifierConfig(
            service=None if service in (NOTIFY_NONE, "", None) else service,
            hourly=options.get(CONF_NOTIFY_HOURLY, True),
            critical=options.get(CONF_NOTIFY_CRITICAL, True),
            skip_quiet_hours=options.get(CONF_NOTIFY_SKIP_QUIET, True),
        ))
        #: Vorige risicostand, om een overgang naar noodstop te herkennen.
        self._previous_risk_state: str | None = None
        #: Opeenvolgende cycli die langer duurden dan het pollinterval.
        self._slow_cycles = 0
        #: Posities zoals ze deze cyclus bij de broker stonden. Aan het begin
        #: van elke cyclus gewist, en na elke order verversd.
        self._positions_cache: list | None = None

        self.sizing = SizingConfig(
            fixed_units=self.units,
            risk_based=options.get(CONF_RISK_BASED_SIZING, False),
            risk_per_trade_pct=options.get(CONF_RISK_PER_TRADE_PCT, 0.5),
            scale_with_confidence=options.get(CONF_SCALE_WITH_CONFIDENCE, False),
            max_units=options.get(CONF_MAX_UNITS, DEFAULT_MAX_UNITS),
        )
        self.pyramid = PyramidConfig(
            enabled=options.get(CONF_PYRAMID_ENABLED, False),
            trigger_atr=options.get(CONF_PYRAMID_TRIGGER_ATR, 1.0),
            max_additions=_as_int(options.get(CONF_PYRAMID_MAX_ADDITIONS), 2),
        )
        #: Per ticket: hoeveel toevoegingen en op welke prijs de laatste.
        self._pyramid_state: dict[str, dict] = {}
        #: Per ticket de uiterste mee- en tegenbeweging sinds de instap.
        #:
        #: Bij brokertrades werden die niet bijgehouden, terwijl de
        #: verliesanalyse erop filtert. Gevolg: die analyse sloeg elke
        #: demotrade over en meldde "0 verliezende trades" naast een
        #: performance die er wél telde - dood in precies de modus die ertoe
        #: doet.
        self._excursions: dict[str, dict] = {}
        self.robustness: dict = {}
        self.periods: dict = {}
        self.backtest: dict = {}
        self.audit: dict = {}
        self.last_sizing: dict = {}

        service = options.get(CONF_NOTIFY_SERVICE, NOTIFY_NONE)
        self.notifier = Notifier(hass, NotifierConfig(
            service=None if service in (NOTIFY_NONE, "", None) else service,
            hourly=options.get(CONF_NOTIFY_HOURLY, True),
            critical=options.get(CONF_NOTIFY_CRITICAL, True),
            skip_quiet_hours=options.get(CONF_NOTIFY_SKIP_QUIET, True),
        ))
        #: Vorige risicostand, om een overgang naar noodstop te herkennen.
        self._previous_risk_state: str | None = None
        #: Opeenvolgende cycli die langer duurden dan het pollinterval.
        self._slow_cycles = 0
        self.executor_notes: list[str] = []

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
        #: Tickets waarvan al een deel is afgeroomd.
        #:
        #: Deze stond alleen in het geheugen. Na een herstart was hij leeg,
        #: waardoor dezelfde positie opnieuw voor de helft gesloten werd - en
        #: bij herhaling tot niets. Gaat nu mee in de bewaarde toestand.
        self._partial_taken: set[str] = set()
        self._last_quote: VenueQuote | None = None
        self._last_signal = None
        #: Aantal gesloten trades bij de laatste poortberekening. De poort
        #: herberekenen is duur (meerdere queries), dus dat gebeurt alleen als
        #: er werkelijk iets veranderd is.
        self._gate_trade_count: int = -1
        #: Wat er uit de eigen historie geleerd is.
        self.execution_facts: dict = {}
        self.proposals: list[dict] = []
        self.regime_stats: dict = {}
        self.postmortem: dict = {}
        #: Waarom er een nieuwe run begon, en welke standaardwaarden er
        #: stilzwijgend zijn overgenomen. Beide horen zichtbaar te zijn.
        self.run_changed_because: list[str] = []
        self.adopted_defaults: list[str] = []
        #: Aantal candles dat sinds de vorige positiecontrole is afgesloten.
        self._bars_since_last_check: int = 1
        self._new_bars_this_cycle: int = 0
        #: Bars zelf opbouwen in plaats van historie opvragen.
        self._build_from_quotes: bool = options.get(CONF_BUILD_FROM_QUOTES, False)
        self._aggregator: QuoteAggregator | None = None
        self._enabled: bool = False
        self._store = StateStore(hass, entry.entry_id)
        self._state = RuntimeState()

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.symbol}",
            update_interval=timedelta(
                seconds=max(
                    MIN_UPDATE_SECONDS,
                    _as_int(options.get(CONF_UPDATE_SECONDS), DEFAULT_UPDATE_SECONDS),
                )
            ),
        )

    # -- opstarten ---------------------------------------------------------- #

    async def async_setup(self) -> None:
        """Toestand herstellen, database openen, historie ophalen, afstemmen."""
        # Eerst de bewaarde toestand: een noodstop uit een vorige sessie moet
        # gelden vóórdat er ook maar één cyclus draait.
        self._state = await self._store.async_load()
        self._enabled = self._state.enabled
        if self._state.halted:
            self.risk.halt(self._state.halt_reason or "noodstop uit vorige sessie")
        self.risk.state.resumes_today = self._state.resumes_today
        self._partial_taken = set(self._state.partial_taken or [])
        self.risk.state.consecutive_losses = self._state.consecutive_losses
        if self._state.day and self._state.day_start_balance is not None:
            from datetime import date as _date
            try:
                self.risk.state.day = _date.fromisoformat(self._state.day)
                self.risk.state.day_start_balance = self._state.day_start_balance
                self.risk.state.trades_today = self._state.trades_today
            except ValueError:
                _LOGGER.debug("Bewaarde handelsdag onleesbaar; opnieuw beginnen")

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
            "assumed_spread": getattr(self.venue, "assumed_spread", None),
            "costs_disabled": getattr(self.venue, "costs_disabled", False),
        }

        material = self._fingerprint_material(config)
        config["fingerprint_material"] = material
        fingerprint = self._hash_material(material)

        existing = await self.hass.async_add_executor_job(
            self.db.find_matching_run, fingerprint
        )
        if not existing:
            # Geen exacte match: kijk of er een recente run is die alleen
            # verschilt door een gewijzigde standaardwaarde. Jouw bewijsfase
            # hoort niet op nul te springen omdat ík een default aanpas.
            existing = await self._adoptable_run(material, fingerprint)
        if existing:
            # Zelfde opzet: de lopende bewijsfase voortzetten. Elke herstart een
            # nieuwe run beginnen maakte de eis van dertig dagen onhaalbaar - één
            # Home Assistant-update zette de teller op nul.
            self.run_id = int(existing["id"])
            self.starting_balance = float(existing["starting_balance"])
            _LOGGER.info(
                "Bewijsfase voortgezet: run %s, gestart %s",
                self.run_id, existing["started_at"],
            )
        else:
            self.run_id = await self.hass.async_add_executor_job(
                self.db.start_run, self.mode.value, STRATEGY_VERSION,
                self.symbol, config, self.starting_balance, None, fingerprint,
            )
            _LOGGER.info(
                "Nieuwe bewijsfase gestart (run %s): de opzet is gewijzigd. "
                "Eerdere runs blijven bewaard en staan onderaan het rapport.",
                self.run_id,
            )

        if not self.mode.places_orders:
            # Slippage volgt de aangenomen spread: staat die op nul, dan is de
            # hele kostenkant uitgeschakeld en moet dat consistent zijn.
            spread = getattr(self.venue, "assumed_spread", 0.0)
            self.paper = PaperBroker(
                self.db, self.run_id, self.symbol, self.starting_balance,
                BrokerCosts(
                    commission_per_lot_per_side=0.0,
                    base_slippage=0.0 if spread <= 0 else 0.02,
                    volatility_slippage_factor=0.0 if spread <= 0 else 0.05,
                    size_slippage_per_lot=0.0,
                ),
            )

        # Historie opwarmen. Zonder dit begint elke herstart met een blinde
        # periode van 60 candles - bij 1m een heel uur.
        if self._build_from_quotes:
            self._aggregator = QuoteAggregator.from_dict(
                self._state.bars or {}, self.timeframe
            )
            self._candles = await self._warmup_from_quotes()
            if self._candles is not None:
                self.state = StreamState()
                await self.hass.async_add_executor_job(
                    self.state.warm_up, self._candles
                )
                self._last_bar_ts = self._candles.timestamp[-1]
            _LOGGER.info(
                "Opwarmen uit live koersen: %d bars beschikbaar",
                self._aggregator.bar_count,
            )
            await self._reconcile()
            await self._refresh_gate()
            return

        try:
            self._candles = await self._fetch_warmup()
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

    async def _warmup_from_quotes(self) -> Candles | None:
        """Bouw bars op uit de koersen die toch al binnenkomen.

        Geeft None zolang er te weinig bars zijn. De integratie draait dan
        gewoon door en meldt via de statussensor hoe lang het nog duurt; falen
        zou hier onterecht zijn, want er is niets mis.
        """
        if self._aggregator is None:
            return None
        if self._aggregator.bar_count < MIN_WARMUP_CANDLES:
            return None
        return self._aggregator.candles(WARMUP_CANDLES * 2)

    async def _fetch_warmup(self) -> Candles:
        """Haal historie op, en vraag minder als de broker weigert.

        Hoeveel candles een broker teruggeeft hangt af van het instrument, het
        tijdsframe, de omgeving en soms een weekquotum. IG antwoordt op een te
        grote aanvraag met ``error.price-history.io-error``, wat niet verklapt
        dat het aantal het probleem is.

        Een vast getal is daarom altijd ergens fout. Beter beginnen bij wat je
        wilt en afbouwen tot wat je krijgt: liever een kortere historie dan een
        integratie die niet opstart.
        """
        # Klein beginnen en alleen opschalen als het lukt. Andersom - groot
        # beginnen en afbouwen - verbruikt bij elke mislukte poging opnieuw
        # datapunten uit het quotum van de broker, en juist de eerste poging is
        # dan de duurste.
        attempts = [MIN_WARMUP_CANDLES, 120, 250, WARMUP_CANDLES]
        best: Candles | None = None
        last_error: Exception | None = None

        for count in attempts:
            try:
                candles = await self.venue.candles(
                    self.symbol, self.timeframe, count
                )
            except VenueError as err:
                last_error = err
                if "allowance" in str(err) or "quotum" in str(err).lower():
                    # Verder proberen verbruikt alleen meer van een quotum dat
                    # al op is.
                    _LOGGER.error("Datalimiet van de broker bereikt: %s", err)
                    break
                _LOGGER.debug("Opwarmen met %d candles faalde: %s", count, err)
                continue

            if len(candles) >= MIN_WARMUP_CANDLES:
                best = candles
                if len(candles) < count:
                    # De broker gaf minder dan gevraagd: meer vragen heeft geen
                    # zin en kost alleen datapunten.
                    break
                continue

            last_error = VenueError(
                f"Slechts {len(candles)} candles ontvangen bij een aanvraag van "
                f"{count}; minimaal {MIN_WARMUP_CANDLES} nodig."
            )

        if best is not None:
            if len(best) < WARMUP_CANDLES:
                _LOGGER.warning(
                    "Opgewarmd met %d candles in plaats van %d. "
                    "Langetermijnindicatoren zijn met minder historie minder "
                    "betrouwbaar.", len(best), WARMUP_CANDLES,
                )
            return best

        raise VenueError(
            f"Kon geen bruikbare historie ophalen voor {self.symbol} op "
            f"{self.timeframe}. Laatste fout: {last_error}. Probeer een hoger "
            "tijdsframe; dat kost minder datapunten en reikt verder terug."
        )

    async def _relearn(self, trades: list) -> None:
        """Werk bij wat er uit de eigen historie te leren valt.

        Metingen worden toegepast: die vervangen een aanname door een feit.
        Parametervoorstellen worden alleen getoond - een bot die zijn eigen
        drempel bijstelt na een slechte week, past zich aan de ruis van die
        week aan en wordt daarmee instabieler in plaats van beter.
        """
        assumed = getattr(
            self.paper.costs, "base_slippage", 0.02
        ) if self.paper else 0.02

        facts = await self.hass.async_add_executor_job(
            measure_execution, trades, assumed
        )
        self.execution_facts = facts.as_dict()

        # De gemeten slippage wordt gebruikt voor de *verwachting* in de
        # kostenpoort, maar niet teruggezet in het kostenmodel van de
        # papersimulatie.
        #
        # Dat laatste deed ik wel, en het was een terugkoppelingslus: de meting
        # bevat al de volatiliteitscomponent (ATR x factor), en die werd er als
        # nieuwe basis opnieuw bovenop gelegd. Elke ronde telde hij dubbel. Na
        # veertig trades stond de slippage op het zesvoudige en was een
        # winstgevende reeks omgeslagen in een verlies van 224 - allemaal
        # boekhouding, geen markt.
        #
        # Dit is precies de val waar deze module tegen waarschuwt: een systeem
        # dat leert van zijn eigen uitvoer in plaats van van de werkelijkheid.
        if facts.measured_slippage is not None:
            self.strategy_cfg.expected_slippage = facts.measured_slippage
            if self.paper is not None:
                modelled = self.paper.costs.base_slippage + (
                    (self.state.atr.value or 0.0)
                    * self.paper.costs.volatility_slippage_factor
                )
                if modelled > 0 and facts.measured_slippage > modelled * 2:
                    _LOGGER.warning(
                        "Gemeten slippage %.3f is meer dan het dubbele van wat het "
                        "model voorspelt (%.3f). Controleer of de kostenboeking "
                        "klopt voordat je hier conclusies aan verbindt.",
                        facts.measured_slippage, modelled,
                    )

        proposal = await self.hass.async_add_executor_job(
            evaluate_threshold, trades,
            self.strategy_cfg.entry_threshold, [0.30, 0.35, 0.40, 0.50, 0.55, 0.60],
        )
        self.proposals = [proposal.as_dict()] if proposal else []
        if proposal and proposal.accept:
            _LOGGER.info(
                "Voorstel: instapdrempel van %s naar %s. %s",
                proposal.current, proposal.suggested, proposal.reasoning,
            )

        self.regime_stats = await self.hass.async_add_executor_job(
            regime_performance, trades
        )

        # Houdt het resultaat stand over de tijd, of komt het uit één periode?
        # De bewijsfase telt trades; dit toetst of ze iets betekenen.
        robust = await self.hass.async_add_executor_job(
            evaluate_robustness, trades
        )
        self.robustness = robust.as_dict()

        from homeassistant.util import dt as dt_util

        self.periods = build_periods(trades, dt_util.DEFAULT_TIME_ZONE).as_dict()

        # Verliezen ordenen naar oorzaak. Niet om omstandigheden te vermijden -
        # dat filtert de winnaars mee weg - maar om te zien of ze aan het
        # exitontwerp liggen of aan de markt.
        post = await self.hass.async_add_executor_job(
            analyse_losses, trades,
            # De doel- en stopmultipliers horen bij de strategie, niet bij de
            # exitmanager: die laatste beheert alleen wat er ná de instap
            # gebeurt (break-even, trailing, tijdslimiet).
            self.strategy_cfg.take_profit_atr,
            self.strategy_cfg.stop_loss_atr,
            self.state.atr.value,
        )
        self.postmortem = post.as_dict()
        if post.patterns and post.patterns[0].actionable and post.fixable_share >= 0.25:
            _LOGGER.info("Verliesanalyse: %s", post.conclusion)

    async def _adoptable_run(self, material: dict, fingerprint: str):
        """Zoek een lopende run die alleen verschilt in wat jij niet koos.

        De vingerafdruk hoort te reageren op jóuw keuzes, niet op mijn
        releases. Wordt een standaardwaarde in een nieuwe versie aangepast en
        heb jij die instelling nooit zelf gezet, dan is dat geen wijziging van
        de strategie door jou - en dan mag de teller niet op nul.

        Verschilt er iets dat je wél zelf hebt ingesteld, dan begint er terecht
        een nieuwe run en wordt in het logboek genoemd wát er verschilde.
        """
        recent = await self.hass.async_add_executor_job(self.db.list_runs, 10)
        options_set = set(self.entry.options)

        for run in recent:
            if run.get("ended_at") or run["id"] == 0:
                continue
            try:
                stored = json.loads(run.get("config_json") or "{}")
            except (TypeError, ValueError):
                continue
            previous = stored.get("fingerprint_material")
            if not previous:
                continue

            differences = {
                key for key in set(previous) | set(material)
                if previous.get(key) != material.get(key)
            }
            if not differences:
                continue

            # Onderscheid: heeft de gebruiker deze instelling zelf gezet?
            user_chosen = {
                key for key in differences
                if self._OPTION_FOR.get(key) in options_set
            }
            structural = differences & {"venue", "symbol", "timeframe", "simulated"}

            if user_chosen or structural:
                _LOGGER.info(
                    "Nieuwe bewijsfase: %s gewijzigd. Eerdere runs blijven in "
                    "het rapport staan.", ", ".join(sorted(user_chosen | structural)),
                )
                self.run_changed_because = sorted(user_chosen | structural)
                return None

            # Alleen standaardwaarden verschillen; run voortzetten en de
            # vingerafdruk bijwerken zodat het de volgende keer meteen matcht.
            _LOGGER.info(
                "Bewijsfase voortgezet ondanks gewijzigde standaardwaarden (%s). "
                "Die heb je niet zelf ingesteld, dus dit telt niet als een "
                "wijziging van de strategie.", ", ".join(sorted(differences)),
            )
            self.adopted_defaults = sorted(differences)
            await self.hass.async_add_executor_job(
                self.db.update_run_fingerprint, run["id"], fingerprint, material
            )
            return run
        return None

    #: Van vingerafdrukveld naar de optienaam waarmee je het zelf instelt.
    _OPTION_FOR = {
        "mode": "mode",
        "entry_threshold": "entry_threshold",
        "regime_switching": "regime_switching",
        "min_edge_multiple": "min_edge_multiple",
        "max_spread": "max_spread",
        "max_spread_atr_ratio": "max_spread_atr_ratio",
        "take_profit": "take_profit_usd",
        "stop_loss": "stop_loss_usd",
        "enforce_hours": "enforce_trading_hours",
        "trading_hours": "trading_start_hour",
        "units": "units",
        "assumed_spread": "assumed_spread",
    }

    async def _notify(self, stats: dict) -> None:
        """Stuur meldingen bij een toestandsovergang of op het hele uur.

        Op de *overgang* melden en niet op de toestand: een noodstop duurt tot
        je hem opheft, en zonder dit onderscheid zou elke cyclus dezelfde
        waarschuwing versturen.
        """
        if not self.notifier.enabled:
            return

        payload = {
            "stats": stats,
            "status": build_status({**(self.data or {}), "stats": stats}),
        }

        risk_state = self.risk.state.state.value
        if risk_state == "halted" and self._previous_risk_state != "halted":
            used = self.risk.state.resumes_today
            allowed = self.risk.limits.max_resumes_per_day
            await self.notifier.alert(
                "halt",
                "Gold Scalper: NOODSTOP",
                f"{self.risk.state.halt_reason}\n\n"
                f"Handel ligt stil tot je hervat. Vandaag {used} van {allowed} "
                "hervattingen gebruikt.",
            )
        elif risk_state != "halted" and self._previous_risk_state == "halted":
            self.notifier.clear("halt")
            await self.notifier.alert(
                "resumed", "Gold Scalper: hervat",
                "De noodstop is opgeheven; er wordt weer gehandeld.",
                critical=False,
            )
        self._previous_risk_state = risk_state

        if self.lifecycle.state.value == "diverged":
            await self.notifier.alert(
                "diverged", "Gold Scalper: posities kloppen niet",
                "Database en broker zijn het oneens over open posities. "
                "Handel is geblokkeerd tot dit is opgelost.",
            )
        else:
            self.notifier.clear("diverged")

        if self.executor_notes and any(
            "zonder stop" in note.lower() for note in self.executor_notes
        ):
            await self.notifier.alert(
                "unprotected", "Gold Scalper: positie zonder stop",
                "\n".join(self.executor_notes[:3]),
            )

        if self.notifier.hourly_due():
            await self.notifier.send_hourly(payload)

    def _fingerprint_material(self, config: dict) -> dict:
        """Hash van alles wat het handelsgedrag bepaalt.

        Wijzigt hier iets, dan zijn de resultaten niet meer vergelijkbaar en
        hoort er een nieuwe run te beginnen. Wijzigt er niets - een herstart,
        een update, een gewijzigde risicolimiet - dan loopt de bewijsfase door.

        Strategieparameters zitten er bewust in: je kunt een strategie niet
        bewijzen terwijl je hem verandert. Risicolimieten zitten er bewust
        níet in; die begrenzen de schade maar veranderen de signalen niet.
        """
        return {
            "venue": config["venue"],
            "symbol": config["symbol"],
            "timeframe": config["timeframe"],
            "strategy": config["strategy"],
            "simulated": config["simulated"],
            "assumed_spread": config["assumed_spread"],
            "units": config["units"],
            "entry_threshold": self.strategy_cfg.entry_threshold,
            "regime_switching": self.strategy_cfg.regime_switching,
            "min_edge_multiple": self.strategy_cfg.min_edge_multiple,
            "enforce_hours": self.strategy_cfg.enforce_trading_hours,
            "real_spread": self.strategy_cfg.real_spread,
            "trading_hours": (
                list(self.strategy_cfg.trading_hours_utc)
                if self.strategy_cfg.enforce_trading_hours else None
            ),
            "max_spread": self.strategy_cfg.max_spread,
            "max_spread_atr_ratio": self.strategy_cfg.max_spread_atr_ratio,
            "take_profit": (
                self.strategy_cfg.take_profit_usd
                or self.strategy_cfg.take_profit_atr
            ),
            "stop_loss": (
                self.strategy_cfg.stop_loss_usd or self.strategy_cfg.stop_loss_atr
            ),
            # De modus hoort erbij: papertrades hebben gemodelleerde kosten,
            # demotrades gemeten. Die in één bewijsfase mengen zou de hele
            # uitkomst waardeloos maken - juist het verschil tussen die twee
            # is wat je wilt meten.
            "mode": self.mode.value,
        }

    @staticmethod
    def _hash_material(material: dict) -> str:
        blob = json.dumps(material, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

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
        # Tickets als tekst vergelijken: IG gebruikt sleutels als
        # 'DIAAAAYCJETQ7A8', alleen MetaTrader en OANDA leveren getallen.
        db_tickets = [str(t.broker_ticket) for t in open_trades if t.broker_ticket]

        result = await self.lifecycle.reconcile(
            [{"ticket": str(p.ticket), "volume": p.units, "side": p.side}
             for p in broker_positions],
            db_tickets,
        )
        if not result.consistent:
            self.risk.halt(result.message)

    async def _refresh_gate(self) -> None:
        """Herbereken of live handel vrijgegeven mag worden."""
        if self.run_id is None:
            return
        trades = await self.hass.async_add_executor_job(
            self.db.closed_trades, self.run_id
        )
        stats = await self.hass.async_add_executor_job(
            performance.compute_for_run, self.db, self.run_id, trades
        )
        run = await self.hass.async_add_executor_job(self.db.get_run, self.run_id)
        daily = performance.daily_breakdown(trades)
        self.gate = LiveGate().evaluate(
            stats, run or {}, daily, self.robustness
        ).as_dict()

        # De venue mag alleen handelen als álles klopt: live modus, poort open,
        # en de gebruiker heeft de schakelaar bewust omgezet.
        self.venue.supports_trading = bool(
            self._enabled and (
                # Demo: orders sturen zodra de gebruiker het aanzet. Er staat
                # geen geld op het spel en het doel is juist meten.
                self.mode is TradingMode.DEMO
                # Live: alleen als de bewijsfase geslaagd is.
                or (self.mode is TradingMode.LIVE and self.gate["unlocked"])
            )
        )

    # -- bediening ---------------------------------------------------------- #

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def async_set_enabled(self, value: bool) -> None:
        self._enabled = value
        await self._persist()
        await self._refresh_gate()
        await self.async_request_refresh()

    async def _persist(self) -> None:
        """Sla de toestand op die een herstart moet overleven."""
        self._state.enabled = self._enabled
        self._state.halted = self.risk.state.state is TradingState.HALTED
        self._state.halt_reason = self.risk.state.halt_reason
        self._state.consecutive_losses = self.risk.state.consecutive_losses
        self._state.day = self.risk.state.day.isoformat()
        self._state.day_start_balance = self.risk.state.day_start_balance
        self._state.trades_today = self.risk.state.trades_today
        self._state.resumes_today = self.risk.state.resumes_today
        self._state.partial_taken = sorted(self._partial_taken)
        self._state.run_id = self.run_id
        if self._aggregator is not None:
            self._state.bars = self._aggregator.to_dict()
        await self._store.async_save(self._state)

    async def async_prepare_shutdown(self) -> dict:
        """Wikkel af zodat HA veilig herstart kan worden."""
        result = await self.lifecycle.drain(self._open_positions, self._close_position)
        await self.async_request_refresh()
        return result.as_dict()

    async def async_close_all(self) -> None:
        for position in await self._open_positions():
            await self._close_position(position, "handmatig")
        await self.async_request_refresh()

    async def async_reset_day(self) -> str:
        """Begin de handelsdag opnieuw, zonder op middernacht te wachten."""
        balance = self.starting_balance
        if self.paper is not None:
            balance = self.paper.balance
        elif self.mode.places_orders:
            try:
                snapshot = await self.venue.account()
                balance = snapshot.equity
            except VenueError as err:
                _LOGGER.warning(
                    "Kon het saldo niet ophalen; er wordt gerekend met de "
                    "startbalans: %s", err,
                )

        bericht = self.risk.reset_day(balance)
        await self._persist()
        await self.async_request_refresh()
        return bericht

    async def async_resume(self) -> bool:
        """Hervat na een noodstop. Bewust handmatig.

        Geeft het huidige saldo mee zodat de daglimiet vanaf nu telt; anders
        zou de volgende cyclus dezelfde overschrijding zien en meteen weer
        stoppen.
        """
        balance = self.starting_balance
        if self.paper is not None:
            balance = self.paper.balance
        elif self.mode.places_orders:
            try:
                snapshot = await self.venue.account()
                balance = snapshot.equity
            except VenueError as err:
                _LOGGER.warning(
                    "Kon het saldo niet ophalen voor het nieuwe dagijkpunt: %s. "
                    "Er wordt gerekend met de startbalans.", err,
                )

        allowed, message = self.risk.manual_resume(balance)
        if not allowed:
            _LOGGER.warning("Hervatten geweigerd: %s", message)
            return False

        await self._persist()
        await self._reconcile()
        await self.async_request_refresh()
        return True

    # -- posities ----------------------------------------------------------- #

    async def _open_positions(self, refresh: bool = False) -> list:
        """Open posities, uit de bron die ze werkelijk houdt.

        Binnen één cyclus wordt het antwoord hergebruikt. Zodra de posities bij
        de broker staan in plaats van in de papersimulatie, kost elke aanroep
        een netwerkverzoek van rond de honderd milliseconde - en de lus deed er
        vier per cyclus. Dat verviervoudigde de cyclustijd en leverde bij tien
        seconden verversen vierentwintig verzoeken per minuut op, precies waar
        rate limits vandaan komen.

        ``refresh`` forceert een verse ophaling; nodig na het openen of sluiten
        van een positie, want dan is het antwoord verouderd.

        In demomodus staan de posities bij de broker, niet in de
        papersimulatie. Hier op LIVE toetsen in plaats van op places_orders
        liet de strategie in demomodus altijd nul posities zien, waardoor de
        limiet van één positie nooit aansloeg: er kwam elke cyclus een nieuwe
        bij, allemaal dezelfde kant op. Vier gestapelde longs in een dalende
        markt.
        """
        if not self.mode.places_orders:
            # De papersimulatie houdt ze in het geheugen; cachen heeft geen zin.
            return self.paper.open_positions if self.paper else []

        if not refresh and self._positions_cache is not None:
            return self._positions_cache

        self._positions_cache = await self.venue.positions(self.symbol)
        return self._positions_cache

    async def _close_position(self, position, reason: str) -> None:
        if self.mode.places_orders:
            await self.venue.close(position.ticket)
            self._positions_cache = None
            if self._last_quote is not None:
                await self._record_broker_close(
                    position, self._last_quote, reason,
                    datetime.now(timezone.utc),
                )
            return
        if self.paper and self._last_quote:
            self.paper.close_position(position, self._paper_quote(self._last_quote), reason)

    def _paper_quote(self, quote: VenueQuote) -> PaperQuote:
        """Vertaal een venue-quote naar een paper-quote met de uitersten erbij.

        De high en low komen uit de candles die sinds de vorige cyclus zijn
        afgesloten. Zonder die twee toetst de simulatie stops alleen op het
        pollmoment en mist zo ongeveer 12% van de stops - allemaal in je
        voordeel, wat de bewijsfase waardeloos maakt.
        """
        high = low = None
        if self._candles is not None and self._candles.high:
            bars = max(1, self._bars_since_last_check)
            high = max(self._candles.high[-bars:])
            low = min(self._candles.low[-bars:])
            # De actuele koers hoort er ook bij: hij kan buiten de laatste
            # afgesloten candle liggen.
            high = max(high, quote.ask)
            low = min(low, quote.bid)
        return PaperQuote(
            bid=quote.bid, ask=quote.ask, time=quote.time,
            atr=self.state.atr.value or 0.0, high=high, low=low,
        )

    # -- de lus ------------------------------------------------------------- #

    async def _async_update_data(self) -> dict:
        budget = LatencyBudget()
        budget.mark("start")
        self._positions_cache = None

        try:
            quote = await self.venue.quote(self.symbol)
        except VenueError as err:
            # Bij een gesloten markt zonder eerdere koers is er niets mis; dan
            # wachten tot de handel opent in plaats van blijven falen. HA zou
            # anders elke cyclus een foutmelding loggen voor een situatie die
            # zichzelf oplost.
            if "gesloten" in str(err).lower() and self._last_quote is not None:
                quote = self._last_quote
                _LOGGER.debug("Markt gesloten; laatst bekende koers aangehouden")
            else:
                raise UpdateFailed(f"Geen koers beschikbaar: {err}") from err
        self._last_quote = quote
        budget.mark("quote")

        now = datetime.now(timezone.utc)
        tick_age = (now - quote.time).total_seconds()

        # -- bars bijwerken --------------------------------------------------- #
        if self._build_from_quotes:
            await self._update_from_quote(quote)
        else:
            await self._maybe_update_candles()
        self._bars_since_last_check = max(1, self._new_bars_this_cycle)
        budget.mark("candles")

        open_positions_precheck = bool(await self._open_positions())

        # Posities die de broker zélf sloot - op de server-side stop of het
        # doel - verdwijnen zonder dat wij er iets van merken. Zonder deze
        # afstemming blijft de rij eeuwig open in de database en telt hij
        # nergens in mee, want de bewijsfase kijkt naar gesloten trades.
        if self.mode.places_orders and quote.tradeable:
            await self._settle_vanished_positions(quote, now)

        # -- open posities beheren, vóór alles anders ------------------------ #
        # Bij een gesloten markt niet ingrijpen: een stop verplaatsen of een
        # positie sluiten op een koers van uren geleden is erger dan wachten.
        if quote.tradeable:
            await self._manage_open_positions(quote, now)
        budget.mark("exits")

        # -- rooster als tweede bron ------------------------------------------ #
        #
        # Niet alleen op het veld van de broker vertrouwen. Klopt dat veld niet,
        # dan handelt de bot op verouderde koersen zonder dat iets het merkt.
        # Bij onenigheid wint 'gesloten': een gemiste kans kost niets, handelen
        # op een koers van uren geleden kan alles kosten.
        tradeable = quote.tradeable
        self.schedule_note = None
        if self._use_schedule:
            tradeable, note = cross_check(quote.tradeable, SPOT_GOLD, now)
            if note:
                self.schedule_note = note
                _LOGGER.warning("Handelstijden: %s", note)

        # -- periodieke controle op onbeschermde posities --------------------- #
        # Een stop kan verdwijnen doordat een wijziging half doorkwam of doordat
        # de broker hem introk. Zonder controle merk je dat pas als het geld weg
        # is. Elke tiende cyclus volstaat; vaker belast de broker-API onnodig.
        # Niet vergelijken bij een gesloten markt: er kan niets bewegen, dus
        # elk verschil is er een van vóór de sluiting. Wel blijven controleren
        # zou alleen ruis opleveren in het weekend.
        if self.mode.places_orders and open_positions_precheck and quote.tradeable:
            self._audit_counter += 1
            if self._audit_counter >= 10:
                self._audit_counter = 0
                await self._audit_against_broker()

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
        # Zelfgebouwde bars onderschatten de uitersten; zonder correctie is de
        # kostenpoort te streng en mis je kansen.
        if self._build_from_quotes and self._aggregator is not None:
            self.strategy_cfg.atr_correction = self._aggregator.correction

        signal = None
        if self._candles is not None and len(self._candles) >= 60:
            # Richting van de lopende positie meegeven, zodat een geweigerd
            # signaal uitgesplitst kan worden naar 'zelfde richting' of
            # 'tegengesteld'. Zonder dat verschil zie je alleen dat er niets
            # gebeurde, niet of je systeem ondertussen van mening veranderde.
            side = 0
            if open_positions:
                first = open_positions[0]
                side = 1 if getattr(first, "side", "buy") == "buy" else -1

            signal = await self.hass.async_add_executor_job(
                evaluate, self._candles, quote.bid, quote.ask, self.strategy_cfg,
                now.hour, len(open_positions),
                now.timestamp() - self._last_entry_ts, side,
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
                    market_open=tradeable,
                    atr=self.state.atr.value,
                )
                reject_reason = None if allowed else f"risico: {why}"

                # Kort voor sluiting geen nieuwe posities. Een trade met een
                # tijdslimiet van vijf minuten die om 22:58 opengaat, wordt door
                # de sluiting overvallen: je zit dan tot de volgende sessie
                # vast, en die opent met een gat waar geen stop tussen zit.
                if allowed and self._use_schedule and self._close_buffer > 0:
                    resterend = minutes_until_close(SPOT_GOLD, now)
                    if resterend is not None and resterend <= self._close_buffer:
                        allowed = False
                        reject_reason = (
                            f"nog {resterend:.0f} minuten tot sluiting; een "
                            "nieuwe positie zou door de sluiting worden "
                            "overvallen"
                        )

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

        # Poort bijwerken zodra er trades bij zijn gekomen. Zonder dit blijft
        # de uitkomst staan zoals hij bij het opstarten was, en meldt hij na
        # duizend trades nog steeds "0 trades in de bewijsfase" - precies het
        # getal waar de hele bewijsfase op steunt. Alleen bij verandering,
        # want de berekening kost meerdere queries.
        trade_count = stats.get("trades", 0)
        if trade_count != self._gate_trade_count:
            # Eén keer ophalen en tweemaal gebruiken: de poort en de leerlaag
            # hebben dezelfde tradelijst nodig, en die tabel inlezen is de
            # duurste stap in deze cyclus.
            closed = await self.hass.async_add_executor_job(
                self.db.closed_trades, self.run_id
            )
            await self._refresh_gate()
            await self._relearn(closed)
            self._gate_trade_count = trade_count
            stats = await self.hass.async_add_executor_job(
                performance.compute_for_run, self.db, self.run_id, closed
            )

        # Elke cyclus bewaren, niet alleen bij afsluiten: een noodstop die
        # halverwege afgaat mag niet verloren gaan als HA daarna hardhandig
        # stopt.
        # Een cyclus die langer duurt dan het pollinterval betekent dat de
        # volgende al had moeten beginnen. Eén keer is ruis; herhaling niet.
        cycle_ms = budget.total_ms() or 0.0
        if cycle_ms > self.update_interval.total_seconds() * 1000:
            self._slow_cycles += 1
            if self._slow_cycles in (5, 25, 100):
                _LOGGER.warning(
                    "%d cycli duurden langer dan het verversingsinterval "
                    "(laatste %.0f ms tegen %.0f ms interval). Overweeg een "
                    "ruimer interval; sneller pollen levert bij bars van %s "
                    "toch geen nieuwe signalen op.",
                    self._slow_cycles, cycle_ms,
                    self.update_interval.total_seconds() * 1000, self.timeframe,
                )
        else:
            self._slow_cycles = 0

        await self._persist()
        await self._notify(stats)

        columns = ("timestamp", "open", "high", "low", "close", "volume")
        candle_lengths = (
            {len(getattr(self._candles, f)) for f in columns}
            if self._candles is not None else set()
        )

        return {
            "quote": quote,
            "price": quote.mid,
            "candles": len(self._candles) if self._candles else 0,
            "market_open": quote.tradeable,
            "quote_age_seconds": round(tick_age, 1),
            "candles_consistent": len(candle_lengths) <= 1,
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
            "executor_notes": self.executor_notes,
            "warmup": (
                self._aggregator.progress(MIN_WARMUP_CANDLES)
                if self._aggregator is not None else None
            ),
            "build_from_quotes": self._build_from_quotes,
            "sizing": self.last_sizing,
            "periods": self.periods,
            "backtest": self.backtest,
            "audit": self.audit,
            "schedule_note": self.schedule_note,
            "run_changed_because": self.run_changed_because,
            "adopted_defaults": self.adopted_defaults,
            "learning": {
                "execution": self.execution_facts,
                "proposals": self.proposals,
                "regimes": self.regime_stats,
                "losses": self.postmortem,
                "robustness": self.robustness,
            },
            "mode": self.mode.value,
            "requested_mode": self.requested_mode.value,
            "mode_override_reason": self.mode_override_reason,
            "enabled": self._enabled,
        }

    def _append_candle(self, fresh: Candles, index: int) -> None:
        """Voeg één candle toe en kap de reeks af.

        De zes kolommen worden hier als één geheel behandeld. De vorige versie
        deed het afkappen binnen de lus over de kolommen en toetste daarbij
        steeds op de lengte van ``close``. Gevolg: alleen ``close`` werd
        afgekapt en de andere vijf groeiden door, zodat de kolommen na verloop
        van tijd honderden posities uit de pas liepen. Elke indicator die
        ``close`` met ``high`` of ``low`` combineert - ATR, Bollinger,
        Stochastic, MFI - rekende vanaf dat moment op verschoven data, zonder
        dat er iets zichtbaar misging.

        Daarom staat de afkapstap nu ná het toevoegen van álle kolommen, en
        wordt de uitkomst gecontroleerd.
        """
        if self._candles is None:
            return

        columns = ("timestamp", "open", "high", "low", "close", "volume")
        for field in columns:
            getattr(self._candles, field).append(getattr(fresh, field)[index])

        limit = WARMUP_CANDLES * 2
        overflow = len(self._candles.close) - limit
        if overflow > 0:
            for field in columns:
                del getattr(self._candles, field)[:overflow]

        lengths = {len(getattr(self._candles, field)) for field in columns}
        if len(lengths) != 1:
            # Mag niet kunnen na bovenstaande, maar als het toch gebeurt is
            # stil doorrekenen op scheve data erger dan opnieuw beginnen.
            _LOGGER.error(
                "OHLCV-kolommen liepen uit de pas (%s); historie wordt opnieuw "
                "opgehaald bij de volgende cyclus", lengths,
            )
            self._candles = None
            self._last_bar_ts = 0

    #: Lengte van een bar per tijdsframe, in seconden.
    _BAR_SECONDS = {
        "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800,
    }

    def _bar_due(self) -> bool:
        """Kan er sinds de vorige bar een nieuwe zijn afgesloten?

        Zonder deze controle vroeg elke cyclus candles op, ook als er niets
        nieuws kon zijn: bij een verversing van twintig seconden op M5 is dat
        98% verspilling. Brokers rekenen historische koersen per datapunt af,
        en IG's demo-quotum was daardoor binnen een dag op.
        """
        if self._last_bar_ts <= 0:
            return True
        length = self._BAR_SECONDS.get(self.timeframe, 60)
        elapsed = datetime.now(timezone.utc).timestamp() - self._last_bar_ts
        # Marge van een paar seconden: brokers publiceren een bar niet altijd
        # exact op het hele moment.
        return elapsed >= (length + 3)

    async def _update_from_quote(self, quote: VenueQuote) -> None:
        """Voeg de koers toe aan de zelfgebouwde reeks."""
        self._new_bars_this_cycle = 0
        if self._aggregator is None:
            self._aggregator = QuoteAggregator(self.timeframe)

        # Op de mid werken: bid of ask zou elke indicator een halve spread
        # laten schuiven.
        # Bij een gesloten markt niets toevoegen: een weekend levert anders
        # honderden bars met dezelfde prijs op, en die drukken de ATR naar nul.
        closed = self._aggregator.add(quote.mid, quote.time, quote.tradeable)
        if not closed:
            return

        self._new_bars_this_cycle = 1
        if self._aggregator.bar_count < MIN_WARMUP_CANDLES:
            return

        fresh = self._aggregator.candles(WARMUP_CANDLES * 2)
        if self._candles is None:
            self._candles = fresh
            self.state = StreamState()
            await self.hass.async_add_executor_job(self.state.warm_up, fresh)
            _LOGGER.info(
                "Opwarmen voltooid: %d zelfgebouwde bars", len(fresh)
            )
        else:
            index = len(fresh) - 1
            self.state.push_candle(
                fresh.open[index], fresh.high[index], fresh.low[index],
                fresh.close[index], fresh.volume[index],
            )
            self._candles = fresh
        self._last_bar_ts = fresh.timestamp[-1]

    async def _maybe_update_candles(self) -> None:
        """Haal nieuwe candles op als er een bar afgesloten kan zijn."""
        self._new_bars_this_cycle = 0
        if self._candles is not None and not self._bar_due():
            return
        try:
            fresh = await self.venue.candles(self.symbol, self.timeframe, 3)
        except VenueError as err:
            _LOGGER.debug("Kon candles niet verversen: %s", err)
            return

        # Historie kwijt na een integriteitsprobleem: opnieuw opwarmen.
        if self._candles is None:
            try:
                self._candles = await self._fetch_warmup()
                self.state = StreamState()
                await self.hass.async_add_executor_job(
                    self.state.warm_up, self._candles
                )
                self._last_bar_ts = self._candles.timestamp[-1]
                _LOGGER.info("Historie opnieuw opgewarmd met %d candles",
                             len(self._candles))
            except VenueError as err:
                _LOGGER.warning("Opnieuw opwarmen mislukt: %s", err)
            return

        for i, ts in enumerate(fresh.timestamp):
            if ts <= self._last_bar_ts:
                continue
            self.state.push_candle(
                fresh.open[i], fresh.high[i], fresh.low[i],
                fresh.close[i], fresh.volume[i],
            )
            self._last_bar_ts = ts
            self._new_bars_this_cycle += 1
            self._append_candle(fresh, i)
            if self._candles is None:
                break

    async def _manage_open_positions(self, quote: VenueQuote, now: datetime) -> None:
        """Break-even, gedeeltelijk sluiten, trailing en tijdstops."""
        atr = self.state.atr.value or 0.0
        if atr <= 0:
            return
        cost = quote.spread + 0.04  # spread plus geschatte slippage beide zijden

        for position in await self._open_positions():
            ticket = str(getattr(position, "ticket", None) or getattr(position, "id", ""))
            opened = _as_datetime(getattr(position, "open_time", None), now)
            # Paper-trades bewaren volume in lots, venue-posities in ounces.
            size = getattr(position, "units", None)
            if size is None:
                size = (getattr(position, "volume", 0) or 0) * CONTRACT_SIZE
            action = self.exits.evaluate(
                side=position.side,
                volume=size,
                open_price=position.open_price,
                current_stop=getattr(position, "stop_loss", None),
                bid=quote.bid, ask=quote.ask, atr=atr,
                opened_at=opened, now=now,
                round_trip_cost_per_oz=cost,
                partial_taken=ticket in self._partial_taken,
            )
            # Uitersten bijhouden zolang de positie leeft, vóór de
            # noop-controle: bij 'hold' gebeurt er verder niets, en dat is
            # juist het grootste deel van de tijd. Achteraf zijn ze niet meer
            # te achterhalen.
            self._track_excursion(position, quote, ticket)

            # Pyramiden vóór de exitacties: bijkopen bij bevestiging is een
            # aparte beslissing van de vraag of je moet sluiten.
            if self.pyramid.enabled and action.kind in ("hold", "modify_stop"):
                await self._consider_pyramid(position, quote, ticket)

            if action.is_noop:
                continue

            try:
                if action.kind == "close":
                    await self._close_position(position, action.reason[:60])
                elif action.kind == "modify_stop" and self.mode.places_orders:
                    # Het doel meegeven: het PUT-endpoint vervangt beide
                    # niveaus, dus zonder deze waarde wist elke stopverplaatsing
                    # je take-profit. Meegeven scheelt bovendien een extra
                    # verzoek om hem eerst op te halen.
                    await self.venue.modify_stop(
                        ticket, action.new_stop,
                        take_profit=getattr(position, "take_profit", None),
                    )
                elif action.kind == "modify_stop":
                    position.stop_loss = action.new_stop
                elif action.kind == "partial_close":
                    units = (getattr(position, "units", 0) or 0) * action.close_fraction
                    if self.mode.places_orders and units > 0:
                        await self.venue.close(ticket, units)
                        self._positions_cache = None
                        # Vastleggen: anders wordt de winst wél genomen maar
                        # verschijnt hij nergens in je resultaten, en telt hij
                        # niet mee in de bewijsfase.
                        await self._record_partial(
                            ticket, units, action.reason,
                            datetime.now(timezone.utc),
                        )
                    self._partial_taken.add(ticket)
            except VenueError as err:
                _LOGGER.error("Exitactie %s mislukte: %s", action.kind, err)

    async def _open_position(self, signal, quote: VenueQuote, now: datetime) -> None:
        side = "buy" if signal.direction == 1 else "sell"
        try:
            # Grootte bepalen vóór de order. Bij risicogestuurde schaling
            # volgt hij uit de stopafstand, zodat elke trade hetzelfde bedrag
            # riskeert ongeacht de volatiliteit.
            equity = self.paper.equity if self.paper else self.starting_balance
            entry_price = quote.ask if side == "buy" else quote.bid
            sized = position_size(
                self.sizing, equity, entry_price, signal.stop_loss,
                signal.score, self.strategy_cfg.entry_threshold,
            )
            units = sized.units
            self.last_sizing = sized.as_dict()
            _LOGGER.debug("Ordergrootte: %s", sized.reason)

            if self.mode.places_orders:
                # De poort geldt alleen voor echt geld. Op demo is er niets te
                # beschermen behalve de kwaliteit van je meting, en juist die
                # meting is het doel.
                # Het dict rechtstreeks doorgeven; de controle kent beide
                # vormen. Er hoeft geen klasse uit gefabriceerd te worden.
                require_live_unlocked(self.mode, self.gate)
                # Via de veiligheidslaag: garandeert een stop, voorkomt een
                # tweede order na een verbroken verbinding.
                result, notes = await self.executor.open_protected(
                    self.symbol, side, units, quote.mid,
                    stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                )
                self.executor_notes = notes[-10:]
                # Na het plaatsen is de gecachte lijst verouderd.
                self._positions_cache = None
                for note in notes:
                    _LOGGER.info("Uitvoering: %s", note)
                if not result.success:
                    _LOGGER.warning("Order niet geplaatst: %s", result.error)
                    return

                # Vastleggen in de database. Zonder dit verdwijnen orders die
                # naar de broker gaan uit je eigen administratie: geen
                # resultaat, geen kosten, geen verliesanalyse, en een
                # bewijsfase die nooit vordert. Dat maakte de demomodus
                # zinloos, want juist het meten was het doel.
                await self._record_broker_open(result, signal, quote, side, now)
            elif self.paper:
                self.paper.open_position(
                    side, units / CONTRACT_SIZE, self._paper_quote(quote),
                    signal.stop_loss, signal.take_profit,
                    signal.score, signal.confidence, None, signal.reason,
                )
            self.risk.record_open()
            self._last_entry_ts = now.timestamp()
        except (ModeLockedError, VenueError) as err:
            _LOGGER.error("Openen mislukt: %s", err)

    async def _settle_vanished_positions(
        self, quote: VenueQuote, now: datetime
    ) -> None:
        """Sluit trades af die bij de broker niet meer bestaan."""
        try:
            live = await self._open_positions()
        except VenueError as err:
            _LOGGER.debug("Kon posities niet nakijken: %s", err)
            return

        live_tickets = {str(getattr(p, "ticket", "")) for p in live}
        open_trades = await self.hass.async_add_executor_job(
            self.db.open_trades, self.run_id
        )

        for trade in open_trades:
            if not trade.broker_ticket:
                continue
            if str(trade.broker_ticket) in live_tickets:
                continue

            # De reden is niet met zekerheid vast te stellen; de broker vertelt
            # niet waarom hij sloot. Afleiden uit waar de koers staat ten
            # opzichte van stop en doel is het beste dat mogelijk is, en dat
            # wordt als zodanig gemarkeerd.
            reason = "broker_gesloten"
            long = trade.side == "buy"
            price = quote.bid if long else quote.ask
            level: float | None = None

            if trade.stop_loss and (
                (long and price <= trade.stop_loss)
                or (not long and price >= trade.stop_loss)
            ):
                reason, level = "stop_loss", trade.stop_loss
            elif trade.take_profit and (
                (long and price >= trade.take_profit)
                or (not long and price <= trade.take_profit)
            ):
                reason, level = "take_profit", trade.take_profit

            # Afrekenen op het niveau waarop de broker sloot, niet op de koers
            # van het moment waarop wij het ontdekken.
            #
            # Die twee lopen uiteen: de lus draait elke twintig seconden, en in
            # die tijd zakt de koers verder door. Op de latere koers afrekenen
            # boekt dat extra stuk als "kosten", waardoor de kostprijs per trade
            # opliep tot ruim het dubbele van de spread - een meetfout die
            # eruitziet als slippage.
            settle = quote
            if level is not None:
                half = quote.spread / 2.0
                settle = VenueQuote(
                    bid=level if long else level - 2 * half,
                    ask=level + 2 * half if long else level,
                    time=quote.time,
                    tradeable=quote.tradeable,
                )

            await self._record_broker_close(
                _TicketOnly(str(trade.broker_ticket)), settle, reason, now
            )

    def _track_excursion(self, position, quote: VenueQuote, ticket: str) -> None:
        """Werk de uiterste mee- en tegenbeweging van een open positie bij."""
        entry = float(getattr(position, "open_price", 0) or 0)
        if entry <= 0:
            return
        long = getattr(position, "side", "buy") == "buy"
        direction = 1.0 if long else -1.0
        # Op de prijs waarop je zou uitstappen, niet op de mid: dat is de
        # beweging die je werkelijk had kunnen realiseren.
        exit_price = quote.bid if long else quote.ask
        excursion = (exit_price - entry) * direction

        record = self._excursions.setdefault(ticket, {"mfe": 0.0, "mae": 0.0})
        record["mfe"] = max(record["mfe"], excursion)
        record["mae"] = min(record["mae"], excursion)

    async def _consider_pyramid(self, position, quote: VenueQuote, ticket: str) -> None:
        """Koop bij als de markt de richting bevestigt.

        Het spiegelbeeld van middelen: bij middelen vergroot je een positie die
        ongelijk krijgt, hier alleen een die gelijk krijgt. De stop schuift bij
        elke toevoeging mee, zodat het totale risico niet groeit.
        """
        state = self._pyramid_state.setdefault(
            ticket, {"additions": 0, "last_price": None, "original": None}
        )
        units_now = float(getattr(position, "units", 0) or 0)
        if state["original"] is None:
            state["original"] = units_now
        if units_now <= 0:
            return

        long = getattr(position, "side", "buy") == "buy"
        price = quote.bid if long else quote.ask
        costs = self.strategy_cfg.expected_slippage * 2 + quote.spread

        decision = consider_addition(
            self.pyramid,
            side=getattr(position, "side", "buy"),
            entry_price=float(getattr(position, "open_price", price)),
            current_price=price,
            current_stop=getattr(position, "stop_loss", None),
            original_units=state["original"],
            total_units=units_now,
            additions_done=state["additions"],
            last_addition_price=state["last_price"],
            atr=self.state.atr.value or 0.0,
            round_trip_cost_per_oz=costs,
        )
        if not decision.add:
            return

        try:
            # Eerst de stop verplaatsen, dan pas bijkopen. Andersom sta je
            # kortstondig met een grotere positie achter een te ruime stop, en
            # precies dan kan de verbinding wegvallen.
            await self.venue.modify_stop(
                ticket, decision.new_stop,
                take_profit=getattr(position, "take_profit", None),
            )
            result, notes = await self.executor.open_protected(
                self.symbol,
                getattr(position, "side", "buy"),
                decision.units,
                quote.mid,
                stop_loss=decision.new_stop,
                take_profit=getattr(position, "take_profit", None),
            )
        except VenueError as err:
            _LOGGER.warning("Bijkopen mislukte: %s", err)
            return

        if not result.success:
            _LOGGER.warning("Bijkopen niet geplaatst: %s", result.error)
            return

        state["additions"] += 1
        state["last_price"] = price
        self._positions_cache = None
        _LOGGER.info("Bijgekocht: %s", decision.reason)

    async def _record_partial(
        self, ticket: str, units: float, reason: str, now: datetime
    ) -> None:
        """Boek een gedeeltelijke sluiting als aparte gesloten trade.

        De resterende positie blijft open met de overgebleven omvang. Zo telt
        de genomen winst mee in het resultaat en in de bewijsfase, en blijft
        het spoor van wat er gebeurd is intact.
        """
        if self._last_quote is None:
            return

        open_trades = await self.hass.async_add_executor_job(
            self.db.open_trades, self.run_id
        )
        trade = next(
            (t for t in open_trades if str(t.broker_ticket) == str(ticket)), None
        )
        if trade is None:
            _LOGGER.debug("Deelsluiting van %s niet in de database gevonden", ticket)
            return

        quote = self._last_quote
        long = trade.side == "buy"
        exit_price = quote.bid if long else quote.ask
        direction = 1.0 if long else -1.0
        closed_lots = units / CONTRACT_SIZE

        # Het gesloten deel als eigen rij, met de rest van de gegevens van de
        # oorspronkelijke trade.
        part = Trade(
            run_id=self.run_id, mode=self.mode.value, symbol=trade.symbol,
            side=trade.side, volume=closed_lots,
            open_time=trade.open_time, open_price=trade.open_price,
            open_mid=trade.open_mid, open_spread=trade.open_spread,
            open_slippage=trade.open_slippage,
            close_time=now.isoformat(), close_price=exit_price,
            close_mid=quote.mid, close_spread=quote.spread,
            close_reason="partial_close",
            stop_loss=trade.stop_loss, take_profit=trade.take_profit,
            signal_score=trade.signal_score, regime=trade.regime,
            broker_ticket=f"{ticket}-deel",
            mfe=(self._excursions.get(str(ticket), {}).get("mfe", 0.0)) * units,
            mae=(self._excursions.get(str(ticket), {}).get("mae", 0.0)) * units,
            gross_pnl=round((quote.mid - trade.open_mid) * direction * units, 4),
            net_pnl=round((exit_price - trade.open_price) * direction * units, 4),
        )
        part.total_cost = round((part.gross_pnl or 0) - (part.net_pnl or 0), 4)
        part.duration_seconds = int(
            (now - _as_datetime(trade.open_time, now)).total_seconds()
        )
        await self.hass.async_add_executor_job(self.db.insert_trade, part)

        # De oorspronkelijke rij krimpt tot wat er nog openstaat.
        trade.volume = max(0.0, trade.volume - closed_lots)
        await self.hass.async_add_executor_job(self.db.update_trade, trade)

        self.risk.record_close(part.net_pnl or 0.0)
        _LOGGER.info(
            "Deel genomen: %.2f oz, netto %.2f. %s",
            units, part.net_pnl or 0.0, reason,
        )

    async def _record_broker_open(
        self, result, signal, quote: VenueQuote, side: str, now: datetime
    ) -> None:
        """Leg een order bij de broker vast als open trade."""
        fill = result.fill_price or (quote.ask if side == "buy" else quote.bid)
        mid = quote.mid
        # Slippage meten in plaats van modelleren: dat is het hele punt van
        # handelen op een demo-account.
        expected = quote.ask if side == "buy" else quote.bid
        slippage = abs(fill - expected)

        trade = Trade(
            run_id=self.run_id,
            mode=self.mode.value,
            symbol=self.symbol,
            side=side,
            volume=(result.units or self.units) / CONTRACT_SIZE,
            open_time=now.isoformat(),
            open_price=fill,
            open_mid=mid,
            open_spread=quote.spread,
            open_slippage=round(slippage, 5),
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            signal_score=signal.score,
            regime=(signal.components or {}).get("regime"),
            broker_ticket=str(result.ticket) if result.ticket else None,
        )
        await self.hass.async_add_executor_job(self.db.insert_trade, trade)
        _LOGGER.info(
            "Trade vastgelegd: %s %s @ %.2f (ticket %s, slippage %.3f)",
            side, self.symbol, fill, result.ticket, slippage,
        )

    async def _record_broker_close(
        self, position, quote: VenueQuote, reason: str, now: datetime
    ) -> None:
        """Werk de open trade bij tot een gesloten trade.

        Zonder dit blijft de rij eeuwig open staan en telt hij nergens in mee:
        de bewijsfase kijkt naar gesloten trades.
        """
        ticket = str(getattr(position, "ticket", "") or "")
        if not ticket:
            return

        open_trades = await self.hass.async_add_executor_job(
            self.db.open_trades, self.run_id
        )
        trade = next(
            (t for t in open_trades if str(t.broker_ticket) == ticket), None
        )
        if trade is None:
            _LOGGER.debug(
                "Positie %s gesloten maar niet in de database gevonden", ticket
            )
            return

        long = trade.side == "buy"
        exit_price = quote.bid if long else quote.ask
        direction = 1.0 if long else -1.0
        units = trade.volume * CONTRACT_SIZE

        trade.close_time = now.isoformat()
        trade.close_price = exit_price
        trade.close_mid = quote.mid
        trade.close_spread = quote.spread
        trade.close_reason = reason
        trade.duration_seconds = int(
            (now - _as_datetime(trade.open_time, now)).total_seconds()
        )
        # Bruto op de mids: dat is de beweging die de strategie ving.
        # Uitersten meenemen. Zonder deze twee kan de verliesanalyse niet
        # vaststellen of een verlies aan het ontwerp lag of aan de markt.
        excursion = self._excursions.pop(ticket, None)
        if excursion:
            trade.mfe = round(excursion["mfe"] * units, 4)
            trade.mae = round(excursion["mae"] * units, 4)

        trade.gross_pnl = round(
            (quote.mid - trade.open_mid) * direction * units, 4
        )
        # Netto op de werkelijke prijzen: daar zit de spread en de slippage in.
        trade.net_pnl = round(
            (exit_price - trade.open_price) * direction * units, 4
        )
        trade.total_cost = round(trade.gross_pnl - trade.net_pnl, 4)

        await self.hass.async_add_executor_job(self.db.update_trade, trade)
        self.risk.record_close(trade.net_pnl)
        _LOGGER.info(
            "Trade gesloten: %s, bruto %.2f, kosten %.2f, netto %.2f",
            reason, trade.gross_pnl, trade.total_cost, trade.net_pnl,
        )

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
