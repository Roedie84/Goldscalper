"""Binaire sensoren: de dingen waar je een automatisering op hangt."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass, BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GoldScalperCoordinator
from .entity import GoldScalperEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: GoldScalperCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        SafeToRestart(coordinator, entry),
        LiveUnlocked(coordinator, entry),
        RiskHalted(coordinator, entry),
        MarketTradeable(coordinator, entry),
        DataIntegrity(coordinator, entry),
    ])


class SafeToRestart(GoldScalperEntity, BinarySensorEntity):
    """Aan als Home Assistant herstart kan worden zonder open posities."""

    _attr_name = "Veilig herstarten"
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_safe_to_restart"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not data:
            return None
        no_positions = not (data.get("open_positions") or [])
        return no_positions or self.coordinator.lifecycle.safe_to_restart

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "open_positions": len(data.get("open_positions") or []),
            "lifecycle_state": (data.get("lifecycle") or {}).get("state"),
            "hint": (
                "Roep gold_scalper.prepare_shutdown aan vóór een update, en wacht "
                "tot deze sensor aan staat."
            ),
        }


class LiveUnlocked(GoldScalperEntity, BinarySensorEntity):
    """Aan als de bewijsfase geslaagd is en live handel vrijgegeven mag worden."""

    _attr_name = "Live vrijgegeven"
    _attr_icon = "mdi:lock-open-check"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_live_unlocked"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return None if not data else bool((data.get("gate") or {}).get("unlocked"))

    @property
    def extra_state_attributes(self) -> dict:
        gate = (self.coordinator.data or {}).get("gate") or {}
        return {
            "checks": gate.get("checks", {}),
            "blocking_reasons": gate.get("blocking_reasons", []),
            "summary": gate.get("summary"),
        }


class RiskHalted(GoldScalperEntity, BinarySensorEntity):
    """Aan bij een noodstop. Vereist handmatig hervatten."""

    _attr_name = "Noodstop"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:hand-back-left"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_halted"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not data:
            return None
        return (data.get("risk") or {}).get("state") == "halted"

    @property
    def extra_state_attributes(self) -> dict:
        risk = (self.coordinator.data or {}).get("risk") or {}
        return {
            "reason": risk.get("halt_reason"),
            "recent_triggers": risk.get("recent_triggers", []),
            "hint": "Roep gold_scalper.resume aan nadat je de oorzaak hebt vastgesteld.",
        }


class MarketTradeable(GoldScalperEntity, BinarySensorEntity):
    """Aan als de markt open is en de spread binnen de limiet valt."""

    _attr_name = "Markt verhandelbaar"
    _attr_icon = "mdi:store-clock"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_tradeable"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not data or not data.get("quote"):
            return None
        quote = data["quote"]
        return bool(
            quote.tradeable and quote.spread <= self.coordinator.strategy_cfg.max_spread
        )

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        quote = data.get("quote")
        return {
            "spread": getattr(quote, "spread", None),
            "max_spread": self.coordinator.strategy_cfg.max_spread,
            "market_open": getattr(quote, "tradeable", None),
        }


class DataIntegrity(GoldScalperEntity, BinarySensorEntity):
    """Aan als de OHLCV-kolommen uit de pas lopen.

    Bestaat omdat dit precies zo'n storing is die niets zichtbaars doet: de
    integratie blijft draaien, de sensoren blijven waarden tonen, maar de
    indicatoren rekenen op verschoven data. Zonder deze melding zie je het pas
    als je een diagnostiekexport naast elkaar legt.
    """

    _attr_name = "Dataprobleem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert-decagram"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_data_integrity"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not data:
            return None
        if not data.get("candles_consistent", True):
            return True
        # Te weinig historie is óók een dataprobleem: de indicatoren geven dan
        # waarden terug die nergens op steunen.
        return (data.get("candles") or 0) < 60

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "candles": data.get("candles"),
            "indicator_bars": self.coordinator.state.bars,
            "columns_consistent": data.get("candles_consistent"),
            "hint": (
                "Bij een probleem wordt de historie automatisch opnieuw opgehaald "
                "bij de volgende cyclus."
            ),
        }
