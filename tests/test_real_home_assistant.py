"""Draait de echte config- en options-flow tegen een geïnstalleerde Home Assistant.

De rest van de suite werkt met stubs, wat snel is maar principieel beperkt: een
stub kan niet aantonen dat Home Assistant de schema's accepteert. Precies daar
ging het mis met `unit_of_measurement=None`, dat pas in de UI opdook als
"400: Bad Request".

Deze tests slaan zichzelf over als Home Assistant niet geïnstalleerd is, zodat
de suite overal blijft draaien:

    pip install homeassistant && pytest tests/test_real_home_assistant.py
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "custom_components")
)

# Alleen zinvol met de échte Home Assistant; de conftest-stub zou hier precies
# de fout maskeren die we willen vangen.
ha = pytest.importorskip(
    "homeassistant.helpers.selector",
    reason="Home Assistant niet geïnstalleerd; installeer met pip install homeassistant",
)
if type(ha).__name__ == "_StubModule":
    pytest.skip("Home Assistant wordt gestubt", allow_module_level=True)


def _flow():
    from gold_scalper.config_flow import GoldScalperConfigFlow
    flow = GoldScalperConfigFlow()
    flow.hass = MagicMock()
    return flow


def _options():
    """Bouw een OptionsFlow zoals Home Assistant dat zelf doet.

    ``config_entry`` is een property die de entry opzoekt via ``handler`` en
    ``hass.config_entries``. Een waarde in ``__dict__`` zetten helpt niet: een
    property is een data-descriptor en wint van het instance-dict.
    """
    from gold_scalper.config_flow import GoldScalperOptionsFlow

    entry = MagicMock()
    entry.entry_id = "testentry"
    entry.domain = "gold_scalper"
    entry.data = {"venue": "simulator", "symbol": "XAU_USD", "timeframe": "1m",
                  "mode": "paper", "sim_seed": 20260823.0, "sim_spread": 0.2}
    entry.options = {}

    options = GoldScalperOptionsFlow()
    options.handler = entry.entry_id
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = entry
    options.hass = hass
    return options


@pytest.mark.parametrize("step", ["user", "simulator", "public"])
def test_config_flow_step_builds(step):
    from homeassistant.data_entry_flow import FlowResultType
    result = asyncio.run(getattr(_flow(), f"async_step_{step}")())
    assert result["type"] == FlowResultType.FORM
    assert len(list(result["data_schema"].schema)) >= 1


def test_options_flow_builds():
    """De regressie: op 1.4.0 faalde precies deze stap."""
    from homeassistant.data_entry_flow import FlowResultType
    result = asyncio.run(_options().async_step_init())
    assert result["type"] == FlowResultType.FORM
    assert len(list(result["data_schema"].schema)) >= 14


def test_options_schema_accepts_plausible_input():
    """Opbouwen is één ding; de ingevulde waarden moeten er ook doorheen."""
    result = asyncio.run(_options().async_step_init())
    validated = result["data_schema"]({
        "mode": "paper", "update_seconds": 20, "units": 1.0, "max_units": 5.0,
        "starting_balance": 10000, "max_spread": 0.3, "min_edge_multiple": 2.0,
        "entry_threshold": 0.45, "trading_start_hour": 7, "trading_end_hour": 20,
        "max_daily_loss_pct": 2.0, "equity_floor_pct": 80,
        "max_trades_per_day": 100, "max_consecutive_losses": 5, "show_panel": True,
    })
    assert validated["starting_balance"] == 10000


def test_manifest_matches_installed_platforms():
    """Elk platform in PLATFORMS moet een bestaande HA-component zijn."""
    import importlib
    from gold_scalper.const import PLATFORMS
    for platform in PLATFORMS:
        importlib.import_module(f"homeassistant.components.{platform}")
