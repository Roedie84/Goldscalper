"""Hoofdschakelaar."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GoldScalperCoordinator
from .entity import GoldScalperEntity
from .modes import TradingMode


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: GoldScalperCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TradingSwitch(coordinator, entry)])


class TradingSwitch(GoldScalperEntity, SwitchEntity):
    """Zet de handelslus aan of uit.

    Uit betekent: wel analyseren en vastleggen, niet openen. Lopende posities
    worden nog steeds beheerd - de exits blijven werken, want een positie
    zonder toezicht laten staan is gevaarlijker dan hem afmaken.
    """

    _attr_name = "Handel actief"
    _attr_icon = "mdi:play-pause"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_trading_enabled"

    @property
    def is_on(self) -> bool:
        return self.coordinator.enabled

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        gate = data.get("gate") or {}
        live = self.coordinator.mode is TradingMode.LIVE
        return {
            "mode": self.coordinator.mode.value,
            "uses_real_money": live and bool(gate.get("unlocked")) and self.coordinator.enabled,
            "note": (
                "In papermodus kost dit niets. In live modus opent dit echte posities "
                "zodra de bewijsfase geslaagd is."
            ),
        }

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_enabled(False)
