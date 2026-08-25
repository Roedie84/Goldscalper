"""Diagnostics-export. Token en account-ID worden geredigeerd."""

from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCOUNT_ID, CONF_API_KEY, CONF_IDENTIFIER, CONF_PASSWORD, CONF_TOKEN,
    DOMAIN,
)
from .coordinator import GoldScalperCoordinator
from .storage import performance

#: Alles wat toegang geeft. Bewust ruim: het is beter een onschuldig veld te
#: redigeren dan er één te missen.
#:
#: Deze lijst liep achter op de code. Toen de IG- en Capital.com-adapters
#: erbij kwamen, kwamen hun api_key, identifier en password in elke
#: diagnostiekexport terecht - en die exports worden nu juist gedeeld om hulp
#: te vragen. Er staat daarom een test op die controleert dat elk
#: configuratieveld dat een geheim kan bevatten hier ook genoemd wordt.
REDACT = {
    CONF_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_API_KEY,
    CONF_PASSWORD,
    CONF_IDENTIFIER,
    # Losse namen, voor het geval een adapter ze anders noemt.
    "api_key", "apikey", "password", "identifier", "token",
    "secret", "client_secret", "access_token", "refresh_token",
    "login", "username", "account_id", "accountId",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    coordinator: GoldScalperCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}

    stats = {}
    daily = []
    if coordinator.db is not None and coordinator.run_id is not None:
        stats = await hass.async_add_executor_job(
            performance.compute_for_run, coordinator.db, coordinator.run_id
        )
        trades = await hass.async_add_executor_job(
            coordinator.db.closed_trades, coordinator.run_id
        )
        daily = performance.daily_breakdown(trades)

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), REDACT),
            "options": async_redact_data(dict(entry.options), REDACT),
        },
        "venue": coordinator.venue.describe(),
        "symbol": coordinator.symbol,
        "timeframe": coordinator.timeframe,
        "mode": coordinator.mode.value,
        "enabled": coordinator.enabled,
        "run_id": coordinator.run_id,
        "last_update_success": coordinator.last_update_success,
        "market": {
            "price": data.get("price"),
            "spread": data.get("spread"),
            "atr": data.get("atr"),
        },
        "performance": stats,
        "daily": daily[-30:],
        "gate": data.get("gate"),
        "risk": data.get("risk"),
        "lifecycle": data.get("lifecycle"),
        "latency": data.get("latency"),
        "candles": {
            "loaded": len(coordinator._candles) if coordinator._candles else 0,
            "indicator_bars": coordinator.state.bars,
            # Deze twee horen gelijk op te lopen op de warmup na. Lopen ze
            # uiteen, dan is er onderweg historie kwijtgeraakt of afgekapt.
            "columns": {
                field: len(getattr(coordinator._candles, field))
                for field in ("timestamp", "open", "high", "low", "close", "volume")
            } if coordinator._candles else {},
            "consistent": data.get("candles_consistent"),
        },
    }
