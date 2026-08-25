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
    # Home Assistant zoekt de entry op via async_get_known_entry, niet via
    # async_get_entry. Beide zetten, zodat de fixture blijft werken als HA
    # intern wisselt in plaats van stilletjes een MagicMock door te geven -
    # dat leverde een venue van None op en dus de verkeerde standaardwaarden.
    hass.config_entries.async_get_known_entry.return_value = entry
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


def test_reconfigure_step_builds():
    """Van databron wisselen zonder verwijderen."""
    from homeassistant.data_entry_flow import FlowResultType
    from unittest.mock import patch

    flow = _flow()
    entry = MagicMock()
    entry.data = {"venue": "simulator", "symbol": "XAU_USD"}
    with patch.object(type(flow), "_get_reconfigure_entry", return_value=entry,
                      create=True):
        result = asyncio.run(flow.async_step_reconfigure())
    assert result["type"] == FlowResultType.FORM
    assert "venue" in [str(k) for k in result["data_schema"].schema]


def _options_for(venue: str):
    from gold_scalper.config_flow import GoldScalperOptionsFlow
    entry = MagicMock()
    entry.entry_id = "testentry"
    entry.domain = "gold_scalper"
    entry.data = {"venue": venue, "symbol": "XAU_USD", "timeframe": "1m"}
    entry.options = {"mode": "live"}
    options = GoldScalperOptionsFlow()
    options.handler = entry.entry_id
    hass = MagicMock()
    # Home Assistant zoekt de entry op via async_get_known_entry, niet via
    # async_get_entry. Beide zetten, zodat de fixture blijft werken als HA
    # intern wisselt in plaats van stilletjes een MagicMock door te geven -
    # dat leverde een venue van None op en dus de verkeerde standaardwaarden.
    hass.config_entries.async_get_known_entry.return_value = entry
    hass.config_entries.async_get_entry.return_value = entry
    options.hass = hass
    return options


@pytest.mark.parametrize("venue", ["simulator", "public_data"])
def test_non_executing_venues_hide_live_mode(venue):
    """Wie 'live' kiest bij een databron die niet kan uitvoeren, krijgt niets
    wat daarop lijkt. De keuze mag niet aangeboden worden."""
    result = asyncio.run(_options_for(venue).async_step_init())
    schema = result["data_schema"].schema
    mode_field = next(k for k in schema if str(k) == "mode")
    config = schema[mode_field].config
    assert "live" not in config["options"]
    # Backtest wordt niet aangeboden: die modus is niet geïmplementeerd en
    # gedraagt zich identiek aan paper.
    assert set(config["options"]) == {"paper"}


def test_oanda_offers_live_mode():
    result = asyncio.run(_options_for("oanda").async_step_init())
    schema = result["data_schema"].schema
    mode_field = next(k for k in schema if str(k) == "mode")
    assert "live" in schema[mode_field].config["options"]


@pytest.mark.parametrize("venue", ["simulator", "public_data"])
def test_stale_live_option_falls_back_to_paper(venue):
    """De entry bevat al mode=live uit een eerdere versie. Het formulier moet
    dan niet crashen maar terugvallen op paper."""
    result = asyncio.run(_options_for(venue).async_step_init())
    schema = result["data_schema"].schema
    mode_field = next(k for k in schema if str(k) == "mode")
    assert mode_field.default() == "paper"


@pytest.mark.parametrize("venue", ["simulator", "public_data", "oanda"])
def test_backtest_is_never_offered(venue):
    """Een modus aanbieden die zich identiek aan paper gedraagt, wekt de
    indruk dat er iets anders gebeurt."""
    result = asyncio.run(_options_for(venue).async_step_init())
    schema = result["data_schema"].schema
    mode_field = next(k for k in schema if str(k) == "mode")
    assert "backtest" not in schema[mode_field].config["options"]


@pytest.mark.parametrize("broker", ["ig", "capital"])
def test_broker_step_builds(broker):
    from homeassistant.data_entry_flow import FlowResultType
    flow = _flow()
    flow._broker = broker
    result = asyncio.run(flow.async_step_broker())
    assert result["type"] == FlowResultType.FORM
    fields = {str(k) for k in result["data_schema"].schema}
    assert {"api_key", "identifier", "password", "environment", "epic"} <= fields


@pytest.mark.parametrize("venue", ["ig", "capital", "oanda"])
def test_real_brokers_offer_live_mode(venue):
    result = asyncio.run(_options_for(venue).async_step_init())
    schema = result["data_schema"].schema
    mode_field = next(k for k in schema if str(k) == "mode")
    assert "live" in schema[mode_field].config["options"]


@pytest.mark.parametrize("venue", ["simulator", "public_data", "stooq"])
def test_data_only_venues_hide_live_mode(venue):
    result = asyncio.run(_options_for(venue).async_step_init())
    schema = result["data_schema"].schema
    mode_field = next(k for k in schema if str(k) == "mode")
    assert schema[mode_field].config["options"] == ["paper"]


def test_password_fields_are_masked():
    """Wachtwoord en API-sleutel horen niet leesbaar in beeld te staan."""
    flow = _flow()
    flow._broker = "ig"
    result = asyncio.run(flow.async_step_broker())
    schema = result["data_schema"].schema
    for name in ("api_key", "password"):
        field = next(k for k in schema if str(k) == name)
        assert schema[field].config["type"] == "password"


def test_notify_fields_are_in_the_options():
    result = asyncio.run(_options_for("ig").async_step_init())
    fields = {str(k) for k in result["data_schema"].schema}
    assert {"notify_service", "notify_hourly", "notify_critical",
            "notify_skip_quiet"} <= fields


def test_notify_dropdown_lists_real_services():
    """Overtypen van een servicenaam gaat mis, en dat merk je pas als er een
    melding had moeten uitgaan."""
    options = _options_for("ig")
    options.hass.services.async_services.return_value = {
        "notify": {
            "persistent_notification": None,
            "mobile_app_iphone_van_ruud": None,
        }
    }
    result = asyncio.run(options.async_step_init())
    schema = result["data_schema"].schema
    field = next(k for k in schema if str(k) == "notify_service")
    choices = schema[field].config["options"]
    assert "mobile_app_iphone_van_ruud" in choices
    # Mobiele apps bovenaan: dat is wat je zoekt.
    assert choices.index("mobile_app_iphone_van_ruud") < choices.index(
        "persistent_notification"
    )
    assert choices[0] == "geen"


def test_notify_dropdown_survives_a_broken_service_registry():
    options = _options_for("ig")
    options.hass.services.async_services.side_effect = RuntimeError("stuk")
    result = asyncio.run(options.async_step_init())
    field = next(
        k for k in result["data_schema"].schema if str(k) == "notify_service"
    )
    assert result["data_schema"].schema[field].config["options"] == ["geen"]


def test_resume_limit_is_in_the_options_form():
    result = asyncio.run(_options_for("ig").async_step_init())
    fields = {str(k) for k in result["data_schema"].schema}
    assert "max_resumes_per_day" in fields


def test_resume_limit_accepts_zero():
    """Nul is een geldige keuze: dan duurt een noodstop tot morgen."""
    result = asyncio.run(_options_for("ig").async_step_init())
    schema = result["data_schema"].schema
    field = next(k for k in schema if str(k) == "max_resumes_per_day")
    assert schema[field].config["min"] == 0
