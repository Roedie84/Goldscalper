"""Constanten voor Gold Scalper."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "gold_scalper"
MANUFACTURER: Final = "Gold Scalper"

PLATFORMS: Final = ["sensor", "binary_sensor", "switch", "button"]

# -- configuratie ----------------------------------------------------------- #

CONF_VENUE: Final = "venue"
CONF_TOKEN: Final = "token"
CONF_ACCOUNT_ID: Final = "account_id"
CONF_ENVIRONMENT: Final = "environment"
CONF_SYMBOL: Final = "symbol"
CONF_TIMEFRAME: Final = "timeframe"
CONF_MODE: Final = "mode"
CONF_UPDATE_SECONDS: Final = "update_seconds"

CONF_UNITS: Final = "units"
CONF_MAX_UNITS: Final = "max_units"
CONF_MAX_SPREAD: Final = "max_spread"
CONF_MIN_EDGE_MULTIPLE: Final = "min_edge_multiple"
CONF_ENTRY_THRESHOLD: Final = "entry_threshold"
CONF_REGIME_SWITCHING: Final = "regime_switching"
CONF_TRADING_START_HOUR: Final = "trading_start_hour"
CONF_TRADING_END_HOUR: Final = "trading_end_hour"

CONF_MAX_DAILY_LOSS_PCT: Final = "max_daily_loss_pct"
CONF_MAX_TRADES_PER_DAY: Final = "max_trades_per_day"
CONF_MAX_CONSECUTIVE_LOSSES: Final = "max_consecutive_losses"
CONF_EQUITY_FLOOR_PCT: Final = "equity_floor_pct"

CONF_SHOW_PANEL: Final = "show_panel"
CONF_DRAIN_POLICY: Final = "drain_policy"
CONF_STARTING_BALANCE: Final = "starting_balance"

# -- standaardwaarden ------------------------------------------------------- #

VENUE_SIMULATOR: Final = "simulator"
VENUE_PUBLIC: Final = "public_data"
VENUE_OANDA: Final = "oanda"
VENUE_STOOQ: Final = "stooq"
VENUES: Final = [VENUE_PUBLIC, VENUE_STOOQ, VENUE_SIMULATOR, VENUE_OANDA]
STOOQ_SYMBOLS: Final = ["xauusd", "xaueur"]
STOOQ_TIMEFRAMES: Final = ["1d", "1w"]

CONF_ASSUMED_SPREAD: Final = "assumed_spread"
#: Nul betekent: transactiekosten uitgeschakeld. Handig om de machinerie te
#: zien draaien, maar het resultaat is dan fictief en de poort blijft dicht.
DEFAULT_ASSUMED_SPREAD: Final = 0.0
PUBLIC_SYMBOLS: Final = ["GC=F", "XAUUSD=X"]

CONF_SIM_SEED: Final = "sim_seed"
CONF_SIM_SPREAD: Final = "sim_spread"
DEFAULT_SIM_SEED: Final = 20260823
DEFAULT_SIM_SPREAD: Final = 0.20

#: Simulator als standaard: je kunt de integratie zo installeren en zien of
#: alles werkt, zonder je ergens aan te melden.
#: Echte goudkoersen met papierhandel: geen account nodig, wél echte data.
DEFAULT_VENUE: Final = VENUE_PUBLIC
DEFAULT_ENVIRONMENT: Final = "practice"
DEFAULT_SYMBOL: Final = "XAU_USD"
DEFAULT_TIMEFRAME: Final = "1m"
DEFAULT_MODE: Final = "paper"

#: Minimaal 10 seconden. Sneller pollen levert bij een REST-API vooral
#: rate limits op, en de strategie mikt op signalen die minuten geldig blijven.
DEFAULT_UPDATE_SECONDS: Final = 20
MIN_UPDATE_SECONDS: Final = 10

DEFAULT_UNITS: Final = 1.0        # ounces
DEFAULT_MAX_UNITS: Final = 5.0
DEFAULT_STARTING_BALANCE: Final = 10_000.0

TIMEFRAMES: Final = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]

#: Aantal candles dat wordt opgehaald bij het opwarmen.
WARMUP_CANDLES: Final = 400

DATABASE_FILENAME: Final = "gold_scalper.db"
#: Bewust in www/: alles daarin serveert Home Assistant op /local/, wat de
#: enige manier is om een eigen HTML-bestand in de UI te tonen zonder extra
#: add-on of webserver.
REPORT_FILENAME: Final = "www/gold_scalper_rapport.html"
REPORT_URL: Final = "/local/gold_scalper_rapport.html"

# -- services --------------------------------------------------------------- #

SERVICE_PREPARE_SHUTDOWN: Final = "prepare_shutdown"
SERVICE_CLOSE_ALL: Final = "close_all"
SERVICE_RESUME: Final = "resume"
SERVICE_GENERATE_REPORT: Final = "generate_report"
SERVICE_NEW_RUN: Final = "new_run"

DISCLAIMER: Final = (
    "Technische indicatoranalyse, geen financieel advies. "
    "Papermodus simuleert geen requotes of spreadverbreding rond nieuws."
)
