"""Gold Scalper: XAU/USD-analyse en handel, volledig binnen Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_SHOW_PANEL, DOMAIN, PLATFORMS, REPORT_FILENAME, SERVICE_CLOSE_ALL,
    SERVICE_GENERATE_REPORT, SERVICE_PREPARE_SHUTDOWN, SERVICE_RESUME,
)
from .coordinator import GoldScalperCoordinator
from .http import async_register_frontend, async_unregister_frontend

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = GoldScalperCoordinator(hass, entry)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Zijbalk-item en rapportadres. Gebeurt automatisch: het dashboard hoort
    # er te zijn zonder dat je eerst een knop indrukt of YAML plakt.
    options = {**entry.data, **entry.options}
    await async_register_frontend(hass, options.get(CONF_SHOW_PANEL, True))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    async def _on_stop(event) -> None:
        await coordinator.async_shutdown_hook()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)
    )
    _register_services(hass)
    return True


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_CLOSE_ALL):
        return

    def _coordinators() -> list[GoldScalperCoordinator]:
        return list(hass.data.get(DOMAIN, {}).values())

    async def prepare_shutdown(call: ServiceCall) -> None:
        for coordinator in _coordinators():
            result = await coordinator.async_prepare_shutdown()
            _LOGGER.info("Afwikkelen: %s", result.get("message"))

    async def close_all(call: ServiceCall) -> None:
        for coordinator in _coordinators():
            await coordinator.async_close_all()

    async def resume(call: ServiceCall) -> None:
        for coordinator in _coordinators():
            await coordinator.async_resume()

    async def generate_report(call: ServiceCall) -> None:
        from .dashboard.report import write_report

        for coordinator in _coordinators():
            path = hass.config.path(call.data.get("path") or REPORT_FILENAME)
            written = await hass.async_add_executor_job(
                write_report, coordinator.db, coordinator.run_id, path, coordinator.gate
            )
            _LOGGER.info("Keuringsrapport geschreven naar %s", written)

    hass.services.async_register(DOMAIN, SERVICE_PREPARE_SHUTDOWN, prepare_shutdown)
    hass.services.async_register(DOMAIN, SERVICE_CLOSE_ALL, close_all)
    hass.services.async_register(DOMAIN, SERVICE_RESUME, resume)
    hass.services.async_register(DOMAIN, SERVICE_GENERATE_REPORT, generate_report)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: GoldScalperCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown_hook()
        await async_unregister_frontend(hass)
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
