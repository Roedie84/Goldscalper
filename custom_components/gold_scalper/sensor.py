"""Sensoren voor Gold Scalper."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DISCLAIMER, DOMAIN
from .coordinator import GoldScalperCoordinator
from .entity import GoldScalperEntity


@dataclass(frozen=True, kw_only=True)
class ScalperSensor(SensorEntityDescription):
    value_fn: Callable[[dict], object]
    attrs_fn: Callable[[dict], dict] | None = None


def _stats(d: dict) -> dict:
    return d.get("stats") or {}


SENSORS: tuple[ScalperSensor, ...] = (
    ScalperSensor(
        key="price", name="Koers", icon="mdi:gold",
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=2,
        value_fn=lambda d: d.get("price"),
        attrs_fn=lambda d: {"bid": d["quote"].bid, "ask": d["quote"].ask},
    ),
    ScalperSensor(
        key="spread", name="Spread", icon="mdi:arrow-expand-horizontal",
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=3,
        value_fn=lambda d: d.get("spread"),
        attrs_fn=lambda d: {
            "meaning": "Round-trip kostprijs per ounce, vóór slippage.",
        },
    ),
    ScalperSensor(
        key="atr", name="ATR", icon="mdi:pulse",
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=3,
        value_fn=lambda d: d.get("atr"),
    ),
    ScalperSensor(
        key="signal", name="Signaal", icon="mdi:chart-timeline-variant",
        device_class=SensorDeviceClass.ENUM, options=["buy", "sell", "flat"],
        value_fn=lambda d: (
            "buy" if d.get("signal") and d["signal"].direction > 0
            else "sell" if d.get("signal") and d["signal"].direction < 0 else "flat"
        ),
        attrs_fn=lambda d: {
            "score": round(d["signal"].score, 3) if d.get("signal") else None,
            "confidence": round(d["signal"].confidence, 3) if d.get("signal") else None,
            "components": d["signal"].components if d.get("signal") else {},
            "reason": d["signal"].reason if d.get("signal") else None,
            "reject_reason": d.get("reject_reason"),
            "expected_move": round(d["signal"].expected_move, 4) if d.get("signal") else None,
            "expected_cost": round(d["signal"].expected_cost, 4) if d.get("signal") else None,
            "disclaimer": DISCLAIMER,
        },
    ),
    ScalperSensor(
        key="equity", name="Equity", icon="mdi:scale-balance",
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=2,
        value_fn=lambda d: d.get("equity"),
        attrs_fn=lambda d: {"balance": d.get("balance")},
    ),
    ScalperSensor(
        key="net_pnl", name="Nettoresultaat", icon="mdi:cash",
        state_class=SensorStateClass.TOTAL, suggested_display_precision=2,
        value_fn=lambda d: _stats(d).get("net_pnl"),
        attrs_fn=lambda d: {
            "gross_pnl": _stats(d).get("gross_pnl"),
            "total_costs": _stats(d).get("total_costs"),
            "cost_ratio": _stats(d).get("cost_ratio"),
        },
    ),
    ScalperSensor(
        key="total_costs", name="Kosten", icon="mdi:cash-minus",
        state_class=SensorStateClass.TOTAL, suggested_display_precision=2,
        value_fn=lambda d: _stats(d).get("total_costs"),
    ),
    ScalperSensor(
        key="trades", name="Trades", icon="mdi:swap-horizontal",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _stats(d).get("trades"),
        attrs_fn=lambda d: {
            "wins": _stats(d).get("wins"), "losses": _stats(d).get("losses"),
            "avg_duration_seconds": _stats(d).get("avg_duration_seconds"),
            "close_reasons": _stats(d).get("close_reasons"),
        },
    ),
    ScalperSensor(
        key="win_rate", name="Winstpercentage", icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _stats(d).get("win_rate"),
    ),
    ScalperSensor(
        key="profit_factor", name="Profit factor", icon="mdi:division",
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=3,
        value_fn=lambda d: _stats(d).get("profit_factor"),
    ),
    ScalperSensor(
        key="t_statistic", name="t-statistiek", icon="mdi:sigma",
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=2,
        value_fn=lambda d: _stats(d).get("t_statistic"),
        attrs_fn=lambda d: {
            "meaning": (
                "Onder 2,0 is het resultaat niet te onderscheiden van toeval."
            )
        },
    ),
    ScalperSensor(
        key="max_drawdown", name="Max. drawdown", icon="mdi:trending-down",
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _stats(d).get("max_drawdown_pct"),
    ),
    ScalperSensor(
        key="verdict", name="Oordeel", icon="mdi:gavel",
        device_class=SensorDeviceClass.ENUM,
        options=["no_data", "insufficient_data", "failed", "passed"],
        value_fn=lambda d: _stats(d).get("verdict"),
        attrs_fn=lambda d: {
            "text": _stats(d).get("verdict_text"),
            "blocking_reasons": _stats(d).get("blocking_reasons", []),
            "checks": (d.get("gate") or {}).get("checks", {}),
            "gate_unlocked": (d.get("gate") or {}).get("unlocked"),
        },
    ),
    ScalperSensor(
        key="mode", name="Modus", icon="mdi:shield-check",
        device_class=SensorDeviceClass.ENUM, options=["backtest", "paper", "live"],
        value_fn=lambda d: d.get("mode"),
    ),
    ScalperSensor(
        key="lifecycle", name="Toestand", icon="mdi:state-machine",
        value_fn=lambda d: (d.get("lifecycle") or {}).get("state"),
        attrs_fn=lambda d: d.get("lifecycle") or {},
    ),
    ScalperSensor(
        key="risk_state", name="Risicobewaking", icon="mdi:shield-alert",
        value_fn=lambda d: (d.get("risk") or {}).get("state"),
        attrs_fn=lambda d: d.get("risk") or {},
    ),
    ScalperSensor(
        key="open_positions", name="Open posities", icon="mdi:briefcase",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.get("open_positions") or []),
        attrs_fn=lambda d: {
            "positions": [
                {
                    "side": p.side,
                    "units": getattr(p, "units", None) or getattr(p, "volume", None),
                    "open_price": p.open_price,
                    "stop_loss": getattr(p, "stop_loss", None),
                }
                for p in (d.get("open_positions") or [])
            ]
        },
    ),
    ScalperSensor(
        key="evaluations", name="Evaluaties", icon="mdi:filter-variant",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: (_stats(d).get("signals") or {}).get("evaluations"),
        attrs_fn=lambda d: {
            "acted": (_stats(d).get("signals") or {}).get("acted"),
            "rejections": (_stats(d).get("signals") or {}).get("rejections", {}),
        },
    ),
    ScalperSensor(
        key="latency", name="Latency p99", icon="mdi:timer-outline",
        native_unit_of_measurement="ms", state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: ((d.get("latency") or {}).get("total") or {}).get("p99"),
        attrs_fn=lambda d: d.get("latency") or {},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: GoldScalperCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(ScalperSensorEntity(coordinator, entry, d) for d in SENSORS)


class ScalperSensorEntity(GoldScalperEntity, SensorEntity):
    entity_description: ScalperSensor

    def __init__(self, coordinator, entry, description: ScalperSensor) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except (KeyError, AttributeError, TypeError):
            return None

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data or not self.entity_description.attrs_fn:
            return None
        try:
            return self.entity_description.attrs_fn(self.coordinator.data)
        except (KeyError, AttributeError, TypeError):
            return None
