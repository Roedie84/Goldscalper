"""Knoppen voor de bediening die je met spoed nodig kunt hebben."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, REPORT_FILENAME, REPORT_URL
from .coordinator import GoldScalperCoordinator
from .entity import GoldScalperEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: GoldScalperCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        CloseAllButton(coordinator, entry),
        PrepareShutdownButton(coordinator, entry),
        ResumeButton(coordinator, entry),
        ReportButton(coordinator, entry),
    ])


class CloseAllButton(GoldScalperEntity, ButtonEntity):
    _attr_name = "Alles sluiten"
    _attr_icon = "mdi:close-octagon"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_close_all"

    async def async_press(self) -> None:
        await self.coordinator.async_close_all()


class PrepareShutdownButton(GoldScalperEntity, ButtonEntity):
    _attr_name = "Afwikkelen voor herstart"
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_prepare_shutdown"

    async def async_press(self) -> None:
        await self.coordinator.async_prepare_shutdown()


class ResumeButton(GoldScalperEntity, ButtonEntity):
    _attr_name = "Hervatten na noodstop"
    _attr_icon = "mdi:restart-alert"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_resume"

    async def async_press(self) -> None:
        # De uitkomst niet weggooien: bij een geweigerde hervatting gebeurt er
        # anders zichtbaar niets, en dan blijf je drukken.
        if not await self.coordinator.async_resume():
            raise HomeAssistantError(
                "Hervatten geweigerd: de daglimiet is vandaag al te vaak "
                "opnieuw gezet. Verder hervatten zou van de limiet een "
                "suggestie maken.\n\n"
                "Wacht tot morgen - de teller reset om middernacht - of roep "
                "de actie gold_scalper.reset_day aan als je de limiet raakte "
                "door een instelling die je inmiddels hebt gecorrigeerd."
            )


class ReportButton(GoldScalperEntity, ButtonEntity):
    """Schrijft het keuringsrapport naar www/ zodat HA het kan serveren.

    Alles in de www-map is bereikbaar op /local/. Dat is de enige manier om een
    eigen HTML-bestand in de Home Assistant-UI te tonen zonder extra add-on of
    losse webserver.
    """

    _attr_name = "Keuringsrapport maken"
    _attr_icon = "mdi:file-document-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_report"
        self._last_written: str | None = None

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "url": REPORT_URL,
            "last_written": self._last_written,
            "hint": (
                "Open het rapport op /local/gold_scalper_rapport.html, of voeg het "
                "als webpage-kaart aan je dashboard toe."
            ),
        }

    async def async_press(self) -> None:
        from .dashboard.report import write_report

        path = self.hass.config.path(REPORT_FILENAME)
        written = await self.hass.async_add_executor_job(
            write_report, self.coordinator.db, self.coordinator.run_id,
            path, self.coordinator.gate, dt_util.DEFAULT_TIME_ZONE,
        )
        self._last_written = dt_util.utcnow().isoformat(timespec="seconds")
        _LOGGER.info("Keuringsrapport op %s, bereikbaar via %s", written, REPORT_URL)
        self.async_write_ha_state()
