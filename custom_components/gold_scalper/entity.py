"""Gedeelde basis voor alle entiteiten."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import GoldScalperCoordinator


class GoldScalperEntity(CoordinatorEntity[GoldScalperCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: GoldScalperCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Gold Scalper {coordinator.symbol}",
            manufacturer=MANUFACTURER,
            model=f"{coordinator.venue.name} · {coordinator.mode.value}",
        )
