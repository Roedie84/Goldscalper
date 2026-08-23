"""Config flow voor Gold Scalper."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector, NumberSelectorConfig, NumberSelectorMode,
    SelectSelector, SelectSelectorConfig, SelectSelectorMode,
    TextSelector, TextSelectorConfig, TextSelectorType,
)

from .broker.adapter import VenueError
from .broker.oanda import OandaVenue
from .const import (
    CONF_ACCOUNT_ID, CONF_SIM_SEED, CONF_SIM_SPREAD, CONF_VENUE,
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
    return NumberSelector(NumberSelectorConfig(
        min=minimum, max=maximum, step=step, unit_of_measurement=unit,
        mode=NumberSelectorMode.SLIDER if slider else NumberSelectorMode.BOX,
    ))


class GoldScalperConfigFlow(ConfigFlow, domain=DOMAIN):
    """Verbinden met de broker en het instrument kiezen."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Kies de databron. De simulator vraagt niets en werkt meteen."""
        if user_input is not None:
            if user_input[CONF_VENUE] == VENUE_SIMULATOR:
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

    async def async_step_simulator(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Simulator: geen account, geen token, geen netwerk."""
        if user_input is not None:
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
                return self.async_create_entry(
                    title=f"{user_input[CONF_SYMBOL]} ({user_input[CONF_ENVIRONMENT]})",
                    data={**user_input, CONF_VENUE: VENUE_OANDA},
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

        schema = vol.Schema({
            vol.Required(CONF_MODE, default=default(CONF_MODE, DEFAULT_MODE)):
                SelectSelector(SelectSelectorConfig(
                    options=[m.value for m in TradingMode],
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
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
