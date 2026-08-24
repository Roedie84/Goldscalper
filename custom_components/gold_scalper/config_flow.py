"""Config flow voor Gold Scalper."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE, ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector, NumberSelectorConfig, NumberSelectorMode,
    SelectSelector, SelectSelectorConfig, SelectSelectorMode,
    TextSelector, TextSelectorConfig, TextSelectorType,
)

from .broker.adapter import VenueError
from .broker.ig_capital import CapitalVenue, IgVenue
from .broker.oanda import OandaVenue
from .broker.public_data import PublicDataVenue
from .broker.stooq import StooqVenue
from .const import (
    CONF_ACCOUNT_ID, CONF_ASSUMED_SPREAD, CONF_ENFORCE_TRADING_HOURS,
    CONF_REGIME_SWITCHING, CONF_SHOW_PANEL, DEFAULT_ASSUMED_SPREAD,
    PUBLIC_SYMBOLS, VENUE_PUBLIC, VENUE_STOOQ, STOOQ_SYMBOLS, STOOQ_TIMEFRAMES,
    CONF_API_KEY, CONF_EPIC, CONF_IDENTIFIER, CONF_PASSWORD, DEFAULT_EPIC,
    TRADING_VENUES, VENUE_CAPITAL, VENUE_IG,
    CONF_SIM_SEED, CONF_SIM_SPREAD, CONF_VENUE,
    DEFAULT_SIM_SEED, DEFAULT_SIM_SPREAD, DEFAULT_VENUE, VENUES,
    VENUE_OANDA, VENUE_SIMULATOR, CONF_ENTRY_THRESHOLD, CONF_ENVIRONMENT, CONF_EQUITY_FLOOR_PCT,
    CONF_MAX_CONSECUTIVE_LOSSES, CONF_MAX_DAILY_LOSS_PCT, CONF_MAX_SPREAD,
    CONF_MAX_TRADES_PER_DAY, CONF_MAX_UNITS, CONF_MIN_EDGE_MULTIPLE, CONF_MODE,
    CONF_STARTING_BALANCE, CONF_SYMBOL, CONF_TIMEFRAME, CONF_TOKEN,
    CONF_TRADING_END_HOUR, CONF_TRADING_START_HOUR, CONF_UNITS, CONF_UPDATE_SECONDS,
    DEFAULT_ENVIRONMENT, DEFAULT_MAX_UNITS, DEFAULT_MODE, DEFAULT_STARTING_BALANCE,
    DEFAULT_SYMBOL, DEFAULT_TIMEFRAME, DEFAULT_UNITS, DEFAULT_UPDATE_SECONDS,
    DOMAIN, MIN_UPDATE_SECONDS, TIMEFRAMES,
)
from .modes import TradingMode

_LOGGER = logging.getLogger(__name__)


def _number(minimum, maximum, step, unit=None, slider=False):
    """Bouw een NumberSelector.

    ``unit_of_measurement`` wordt weggelaten in plaats van op ``None`` gezet.
    Home Assistant valideert dat veld als ``str``, dus een expliciete ``None``
    maakt de hele configuratiepagina ongeldig - met als zichtbaar gevolg
    "Config-flow kon niet geladen worden: 400: Bad Request", zonder verdere
    aanwijzing welk veld de dader is.
    """
    config: dict = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": NumberSelectorMode.SLIDER if slider else NumberSelectorMode.BOX,
    }
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(NumberSelectorConfig(**config))


class GoldScalperConfigFlow(ConfigFlow, domain=DOMAIN):
    """Verbinden met de broker en het instrument kiezen."""

    VERSION = 1

    _reconfigure_venue: str | None = None
    _broker: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Kies de databron. De simulator vraagt niets en werkt meteen."""
        if user_input is not None:
            venue = user_input[CONF_VENUE]
            if venue == VENUE_PUBLIC:
                return await self.async_step_public()
            if venue == VENUE_STOOQ:
                return await self.async_step_stooq()
            if venue in (VENUE_IG, VENUE_CAPITAL):
                self._broker = venue
                return await self.async_step_broker()
            if venue == VENUE_SIMULATOR:
                return await self.async_step_simulator()
            return await self.async_step_oanda()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_VENUE, default=DEFAULT_VENUE): SelectSelector(
                    SelectSelectorConfig(
                        options=VENUES, mode=SelectSelectorMode.LIST,
                        translation_key="venue",
                    )
                ),
            }),
        )

    async def async_step_public(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Echte goudkoersen, papierhandel, geen account."""
        errors: dict[str, str] = {}
        detail = ""

        if user_input is not None:
            venue = PublicDataVenue(
                session=async_get_clientsession(self.hass),
                symbol=user_input[CONF_SYMBOL],
                assumed_spread=user_input[CONF_ASSUMED_SPREAD],
            )
            # Eén echte call, zodat je hier faalt en niet pas over een kwartier.
            try:
                candles = await venue.candles(
                    user_input[CONF_SYMBOL], user_input[CONF_TIMEFRAME], 120
                )
                if len(candles) < 60:
                    errors["base"] = "insufficient_history"
                    detail = (
                        f"Slechts {len(candles)} candles ontvangen; minimaal 60 nodig. "
                        "Buiten handelsuren levert de bron soms te weinig."
                    )
            except VenueError as err:
                # De werkelijke reden tonen in plaats van 'kan niet bereiken'.
                # Die melding dekt vijf verschillende oorzaken en laat je raden
                # welke het is.
                _LOGGER.warning("Publieke databron faalde: %s", err)
                errors["base"] = "fetch_failed"
                detail = str(err)

            if not errors:
                if self.source != SOURCE_RECONFIGURE:
                    await self.async_set_unique_id(f"public_{user_input[CONF_SYMBOL]}")
                    self._abort_if_unique_id_configured()
                data = {**user_input,
                        CONF_VENUE: VENUE_PUBLIC,
                        CONF_MODE: "paper"}
                if self.source == SOURCE_RECONFIGURE:
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(), data_updates=data
                    )
                return self.async_create_entry(
                    title=f"{user_input[CONF_SYMBOL]} (marktdata)", data=data,
                )

        return self.async_show_form(
            step_id="public",
            data_schema=vol.Schema({
                vol.Required(CONF_SYMBOL, default="GC=F"): SelectSelector(
                    SelectSelectorConfig(options=PUBLIC_SYMBOLS,
                                         mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(CONF_TIMEFRAME, default=DEFAULT_TIMEFRAME): SelectSelector(
                    SelectSelectorConfig(options=["1m", "5m", "15m", "30m", "1h", "1d"],
                                         mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(
                    CONF_ASSUMED_SPREAD, default=DEFAULT_ASSUMED_SPREAD
                ): NumberSelector(
                    NumberSelectorConfig(min=0.0, max=1.0, step=0.01,
                                         unit_of_measurement="USD",
                                         mode=NumberSelectorMode.SLIDER)
                ),
            }),
            errors=errors,
            description_placeholders={
                "note": (
                    "Echte goudkoersen van Yahoo Finance, uitvoering volledig op "
                    "papier. Publieke bronnen leveren geen bied- en laatprijs, dus "
                    "de spread is een aanname. Op nul zetten schakelt de "
                    "transactiekosten uit; het resultaat is dan fictief en live "
                    "handel blijft vergrendeld."
                    + (f"\n\nFoutdetails: {detail}" if detail else "")
                ),
                "detail": detail or "-",
            },
        )

    async def async_step_broker(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """IG of Capital.com: API-sleutel plus inloggegevens."""
        errors: dict[str, str] = {}
        detail = ""
        broker = getattr(self, "_broker", VENUE_CAPITAL)
        factory = IgVenue if broker == VENUE_IG else CapitalVenue

        if user_input is not None:
            venue = factory(
                session=async_get_clientsession(self.hass),
                api_key=user_input[CONF_API_KEY].strip(),
                identifier=user_input[CONF_IDENTIFIER].strip(),
                password=user_input[CONF_PASSWORD],
                environment=user_input[CONF_ENVIRONMENT],
                epic=user_input[CONF_EPIC].strip(),
                trading_enabled=False,
            )
            # Eén echte verbinding: inloggen én data ophalen. Liever hier falen
            # dan pas over twintig seconden in de logs.
            try:
                await venue.account()
                candles = await venue.candles(
                    user_input[CONF_EPIC], user_input[CONF_TIMEFRAME], 120
                )
                if len(candles) < 60:
                    errors["base"] = "insufficient_history"
                    detail = (
                        f"Slechts {len(candles)} candles ontvangen; minimaal 60 "
                        "nodig. Buiten handelsuren levert de broker soms te "
                        "weinig, en op een demo-account kan het weekquotum voor "
                        "historische koersen op zijn. Een hoger tijdsframe "
                        "kost minder datapunten."
                    )
            except VenueError as err:
                _LOGGER.warning("%s-verbinding faalde: %s", broker, err)
                errors["base"] = "fetch_failed"
                detail = str(err)

                # Als de verbinding staat maar het instrument niet klopt, is
                # de nuttigste hulp een lijst van wat dit account wél kent.
                # Epics zijn niet te raden en verschillen per account.
                if "epic" in str(err).lower() or "instrument" in str(err).lower():
                    try:
                        found = await venue.search_markets("gold")
                    except VenueError:
                        found = []
                    goud = [
                        m for m in found
                        if m.get("epic") and "GOLD" in str(m["epic"]).upper()
                    ][:8]
                    if goud:
                        detail += "\n\nGevonden bij jouw account:\n" + "\n".join(
                            f"  {m['epic']}  -  {m.get('name') or ''} "
                            f"({m.get('status') or '?'})"
                            for m in goud
                        )

            if not errors:
                data = {**user_input, CONF_VENUE: broker, CONF_MODE: "paper"}
                if self.source == SOURCE_RECONFIGURE:
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(), data_updates=data
                    )
                await self.async_set_unique_id(
                    f"{broker}_{user_input[CONF_EPIC]}_{user_input[CONF_ENVIRONMENT]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{user_input[CONF_EPIC]} ({broker} {user_input[CONF_ENVIRONMENT]})",
                    data=data,
                )

        hint = (
            "IG: genereer een sleutel in je accountinstellingen. Het wachtwoord "
            "hieronder is je gewone accountwachtwoord. Let op: een demo-account "
            "moet hetzelfde e-mailadres gebruiken als je live account."
            if broker == VENUE_IG else
            "Capital.com: zet eerst 2FA aan, ga dan naar Instellingen, API "
            "integraties, Generate API key. LET OP: bij het aanmaken stel je een "
            "apart API-sleutelwachtwoord in. Vul hieronder DAT wachtwoord in, niet "
            "je inlogwachtwoord - anders krijg je een 401 die eruitziet als "
            "verkeerde inloggegevens."
        )
        return self.async_show_form(
            step_id="broker",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_IDENTIFIER): str,
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_ENVIRONMENT, default="demo"): SelectSelector(
                    SelectSelectorConfig(options=["demo", "live"],
                                         mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(CONF_EPIC, default=DEFAULT_EPIC): str,
                vol.Required(CONF_TIMEFRAME, default=DEFAULT_TIMEFRAME): SelectSelector(
                    SelectSelectorConfig(options=TIMEFRAMES,
                                         mode=SelectSelectorMode.DROPDOWN)
                ),
            }),
            errors=errors,
            description_placeholders={
                "note": hint + (f"\n\nFoutdetails: {detail}" if detail else "")
            },
        )

    async def async_step_stooq(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Stooq: CSV zonder sleutel, maar alleen daggegevens."""
        errors: dict[str, str] = {}
        detail = ""

        if user_input is not None:
            venue = StooqVenue(
                session=async_get_clientsession(self.hass),
                symbol=user_input[CONF_SYMBOL],
                assumed_spread=user_input[CONF_ASSUMED_SPREAD],
            )
            try:
                candles = await venue.candles(
                    user_input[CONF_SYMBOL], user_input[CONF_TIMEFRAME], 120
                )
                if len(candles) < 60:
                    errors["base"] = "insufficient_history"
                    detail = f"Slechts {len(candles)} candles; minimaal 60 nodig."
            except VenueError as err:
                _LOGGER.warning("Stooq faalde: %s", err)
                errors["base"] = "fetch_failed"
                detail = str(err)

            if not errors:
                data = {**user_input, CONF_VENUE: VENUE_STOOQ, CONF_MODE: "paper"}
                if self.source == SOURCE_RECONFIGURE:
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(), data_updates=data
                    )
                await self.async_set_unique_id(f"stooq_{user_input[CONF_SYMBOL]}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{user_input[CONF_SYMBOL].upper()} (Stooq)", data=data,
                )

        return self.async_show_form(
            step_id="stooq",
            data_schema=vol.Schema({
                vol.Required(CONF_SYMBOL, default="xauusd"): SelectSelector(
                    SelectSelectorConfig(options=STOOQ_SYMBOLS,
                                         mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(CONF_TIMEFRAME, default="1d"): SelectSelector(
                    SelectSelectorConfig(options=STOOQ_TIMEFRAMES,
                                         mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(
                    CONF_ASSUMED_SPREAD, default=DEFAULT_ASSUMED_SPREAD
                ): NumberSelector(
                    NumberSelectorConfig(min=0.0, max=2.0, step=0.01,
                                         unit_of_measurement="USD",
                                         mode=NumberSelectorMode.SLIDER)
                ),
            }),
            errors=errors,
            description_placeholders={
                "note": (
                    "Stooq levert CSV zonder sleutel of toestemmingspagina, maar "
                    "alleen daggegevens. Ongeschikt voor scalping, bruikbaar als "
                    "Yahoo je regio blokkeert."
                    + (f"\n\nFoutdetails: {detail}" if detail else "")
                ),
            },
        )

    async def async_step_simulator(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Simulator: geen account, geen token, geen netwerk."""
        if user_input is not None:
            if self.source != SOURCE_RECONFIGURE:
                await self.async_set_unique_id(f"simulator_{user_input[CONF_SYMBOL]}")
                self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"{user_input[CONF_SYMBOL]} (simulator)",
                data={**user_input, CONF_VENUE: VENUE_SIMULATOR, CONF_MODE: "paper"},
            )

        return self.async_show_form(
            step_id="simulator",
            data_schema=vol.Schema({
                vol.Required(CONF_SYMBOL, default=DEFAULT_SYMBOL): str,
                vol.Required(CONF_TIMEFRAME, default=DEFAULT_TIMEFRAME): SelectSelector(
                    SelectSelectorConfig(options=TIMEFRAMES, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(CONF_SIM_SPREAD, default=DEFAULT_SIM_SPREAD): NumberSelector(
                    NumberSelectorConfig(min=0.02, max=1.0, step=0.01,
                                         unit_of_measurement="USD",
                                         mode=NumberSelectorMode.SLIDER)
                ),
                vol.Required(CONF_SIM_SEED, default=DEFAULT_SIM_SEED): NumberSelector(
                    NumberSelectorConfig(min=1, max=99999999, step=1,
                                         mode=NumberSelectorMode.BOX)
                ),
            }),
            description_placeholders={
                "note": (
                    "Synthetische koersen. Geschikt om te controleren of alles werkt, "
                    "ongeschikt om de strategie te beoordelen. Live handel blijft "
                    "vergrendeld voor simulatorruns."
                )
            },
        )

    async def async_step_oanda(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            account = user_input[CONF_ACCOUNT_ID].strip()
            if self.source != SOURCE_RECONFIGURE:
                await self.async_set_unique_id(f"{account}_{user_input[CONF_SYMBOL]}")
                self._abort_if_unique_id_configured()

            venue = OandaVenue(
                session=async_get_clientsession(self.hass),
                token=user_input[CONF_TOKEN].strip(),
                account_id=account,
                environment=user_input[CONF_ENVIRONMENT],
                trading_enabled=False,
            )
            # Eén echte call. Liever hier falen dan een entry die daarna
            # elke twintig seconden stilletjes stukloopt.
            try:
                await venue.account()
                candles = await venue.candles(
                    user_input[CONF_SYMBOL], user_input[CONF_TIMEFRAME], 120
                )
                if len(candles) < 60:
                    errors["base"] = "insufficient_history"
            except VenueError as err:
                message = str(err).lower()
                if "token" in message:
                    errors[CONF_TOKEN] = "invalid_token"
                elif "account" in message:
                    errors[CONF_ACCOUNT_ID] = "invalid_account"
                else:
                    errors["base"] = "cannot_connect"
                _LOGGER.debug("Validatie faalde: %s", err)

            if not errors:
                data = {**user_input, CONF_VENUE: VENUE_OANDA}
                if self.source == SOURCE_RECONFIGURE:
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(), data_updates=data
                    )
                return self.async_create_entry(
                    title=f"{user_input[CONF_SYMBOL]} ({user_input[CONF_ENVIRONMENT]})",
                    data=data,
                )

        schema = vol.Schema({
            vol.Required(CONF_TOKEN): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_ACCOUNT_ID): str,
            vol.Required(CONF_ENVIRONMENT, default=DEFAULT_ENVIRONMENT): SelectSelector(
                SelectSelectorConfig(
                    options=["practice", "live"], mode=SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Required(CONF_SYMBOL, default=DEFAULT_SYMBOL): str,
            vol.Required(CONF_TIMEFRAME, default=DEFAULT_TIMEFRAME): SelectSelector(
                SelectSelectorConfig(options=TIMEFRAMES, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_MODE, default=DEFAULT_MODE): SelectSelector(
                SelectSelectorConfig(
                    options=[m.value for m in TradingMode],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        return self.async_show_form(step_id="oanda", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Van databron wisselen zonder de integratie te verwijderen.

        Zonder deze stap moet je bij elke wissel opnieuw beginnen, en daarbij
        raak je je tradedatabase niet kwijt maar wel je entry-instellingen.
        """
        if user_input is not None:
            venue = user_input[CONF_VENUE]
            self._reconfigure_venue = venue
            if venue == VENUE_PUBLIC:
                return await self.async_step_public()
            if venue == VENUE_STOOQ:
                return await self.async_step_stooq()
            if venue in (VENUE_IG, VENUE_CAPITAL):
                self._broker = venue
                return await self.async_step_broker()
            if venue == VENUE_SIMULATOR:
                return await self.async_step_simulator()
            return await self.async_step_oanda()

        entry = self._get_reconfigure_entry()
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_VENUE, default=entry.data.get(CONF_VENUE, DEFAULT_VENUE)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=VENUES, mode=SelectSelectorMode.LIST,
                        translation_key="venue",
                    )
                ),
            }),
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return GoldScalperOptionsFlow()


class GoldScalperOptionsFlow(OptionsFlow):
    """Strategie- en risico-instellingen bijstellen zonder herinstalleren."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input[CONF_TRADING_START_HOUR] >= user_input[CONF_TRADING_END_HOUR]:
                errors[CONF_TRADING_START_HOUR] = "hours_inverted"
            if user_input[CONF_UNITS] > user_input[CONF_MAX_UNITS]:
                errors[CONF_UNITS] = "units_above_cap"
            if not errors:
                return self.async_create_entry(data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}

        def default(key, fallback):
            return current.get(key, fallback)

        # Alleen modi tonen die deze databron werkelijk aankan. Simulator en
        # publieke marktdata kunnen niet uitvoeren; 'live' aanbieden zou de
        # indruk wekken dat je iets aanzet terwijl er niets verandert.
        # Backtest wordt bewust niet aangeboden: die modus is nog niet
        # geïmplementeerd en gedraagt zich identiek aan paper. Een keuze tonen
        # die niets verandert, wekt de indruk dat er iets anders gebeurt.
        venue = current.get(CONF_VENUE, DEFAULT_VENUE)
        if venue in TRADING_VENUES:
            available_modes = [TradingMode.PAPER.value, TradingMode.LIVE.value]
        else:
            available_modes = [TradingMode.PAPER.value]
        current_mode = default(CONF_MODE, DEFAULT_MODE)
        if current_mode not in available_modes:
            current_mode = TradingMode.PAPER.value

        schema = vol.Schema({
            vol.Required(CONF_MODE, default=current_mode):
                SelectSelector(SelectSelectorConfig(
                    options=available_modes,
                    mode=SelectSelectorMode.DROPDOWN)),
            vol.Required(CONF_UPDATE_SECONDS,
                         default=default(CONF_UPDATE_SECONDS, DEFAULT_UPDATE_SECONDS)):
                _number(MIN_UPDATE_SECONDS, 3600, 5, "s"),
            vol.Required(CONF_UNITS, default=default(CONF_UNITS, DEFAULT_UNITS)):
                _number(0.1, 100, 0.1, "oz"),
            vol.Required(CONF_MAX_UNITS, default=default(CONF_MAX_UNITS, DEFAULT_MAX_UNITS)):
                _number(0.1, 1000, 0.1, "oz"),
            vol.Required(CONF_STARTING_BALANCE,
                         default=default(CONF_STARTING_BALANCE, DEFAULT_STARTING_BALANCE)):
                _number(100, 1_000_000, 100),

            vol.Required(CONF_MAX_SPREAD, default=default(CONF_MAX_SPREAD, 0.30)):
                _number(0.01, 2.0, 0.01, "USD"),
            vol.Required(CONF_MIN_EDGE_MULTIPLE,
                         default=default(CONF_MIN_EDGE_MULTIPLE, 2.0)):
                _number(1.0, 6.0, 0.1, slider=True),
            vol.Required(CONF_ENTRY_THRESHOLD, default=default(CONF_ENTRY_THRESHOLD, 0.45)):
                _number(0.1, 0.95, 0.05, slider=True),
            # Standaard uit: handelen wanneer de markt open is. Zie de
            # toelichting bij ScalpConfig.enforce_trading_hours.
            vol.Required(
                CONF_ENFORCE_TRADING_HOURS,
                default=default(CONF_ENFORCE_TRADING_HOURS, False),
            ): BooleanSelector(),
            vol.Required(CONF_TRADING_START_HOUR,
                         default=default(CONF_TRADING_START_HOUR, 7)):
                _number(0, 23, 1, "u", slider=True),
            vol.Required(CONF_TRADING_END_HOUR, default=default(CONF_TRADING_END_HOUR, 20)):
                _number(1, 24, 1, "u", slider=True),

            vol.Required(CONF_MAX_DAILY_LOSS_PCT,
                         default=default(CONF_MAX_DAILY_LOSS_PCT, 2.0)):
                _number(0.5, 20.0, 0.5, "%", slider=True),
            vol.Required(CONF_EQUITY_FLOOR_PCT,
                         default=default(CONF_EQUITY_FLOOR_PCT, 80.0)):
                _number(50, 99, 1, "%", slider=True),
            vol.Required(CONF_MAX_TRADES_PER_DAY,
                         default=default(CONF_MAX_TRADES_PER_DAY, 100)):
                _number(1, 2000, 1),
            vol.Required(CONF_MAX_CONSECUTIVE_LOSSES,
                         default=default(CONF_MAX_CONSECUTIVE_LOSSES, 5)):
                _number(2, 20, 1, slider=True),

            # Het rapportpaneel is zonder authenticatie leesbaar voor iedereen
            # die je Home Assistant kan bereiken. Er staan geen tokens of
            # inloggegevens in, maar wel je handelsresultaten.
            # Zie de toelichting bij ScalpConfig.regime_switching: optellen van
            # trend en mean reversion laat ze elkaar opheffen.
            vol.Required(
                CONF_REGIME_SWITCHING,
                default=default(CONF_REGIME_SWITCHING, True),
            ): BooleanSelector(),

            vol.Required(CONF_SHOW_PANEL, default=default(CONF_SHOW_PANEL, True)):
                BooleanSelector(),
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
