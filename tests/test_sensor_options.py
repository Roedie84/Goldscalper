"""ENUM-sensoren moeten elke waarde kennen die ze kunnen tonen.

Home Assistant weigert een sensor met een ValueError zodra hij een waarde
teruggeeft die niet in zijn optielijst staat. Toen de modus `demo` erbij kwam,
liep de handmatig overgetypte lijst achter en viel de hele sensor om.

Een lijst die je met de hand bijhoudt, loopt een keer achter. Deze tests
controleren dat de lijsten uit de bron worden afgeleid of ermee overeenkomen.
"""
import ast
import os
import re
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "gold_scalper"
sys.path.insert(0, str(PKG.parent))

SENSOR = (PKG / "sensor.py").read_text(encoding="utf-8")


def test_mode_options_are_derived_from_the_enum():
    """De storing: 'demo' toegevoegd aan TradingMode, lijst niet bijgewerkt."""
    block = SENSOR.split('key="mode"')[1].split("ScalperSensor(")[0]
    assert "for m in TradingMode" in block, (
        "de modusopties zijn overgetypt in plaats van uit de enum gehaald"
    )


def test_every_trading_mode_would_be_accepted():
    from gold_scalper.modes import TradingMode
    block = SENSOR.split('key="mode"')[1].split("ScalperSensor(")[0]
    options = [m.value for m in TradingMode] if "for m in TradingMode" in block else []
    for mode in TradingMode:
        assert mode.value in options, mode.value


def test_signal_options_cover_what_the_sensor_returns():
    block = SENSOR.split('key="signal"')[1].split("ScalperSensor(")[0]
    declared = set(re.findall(r'"(\w+)"', block.split("options=")[1].split("]")[0]))
    # De value_fn geeft precies deze drie terug.
    assert {"buy", "sell", "flat"} <= declared


def test_verdict_options_match_the_performance_module():
    from gold_scalper.storage import performance

    source = Path(performance.__file__).read_text(encoding="utf-8")
    produced = set(re.findall(r'"verdict":\s*"(\w+)"', source))
    block = SENSOR.split('key="verdict"')[1].split("ScalperSensor(")[0]
    declared = set(re.findall(r'"(\w+)"', block.split("options=")[1].split("]")[0]))
    missing = produced - declared
    assert not missing, f"oordeelwaarden zonder optie: {missing}"


def test_status_sensor_is_not_an_enum():
    """De statuswaarden groeien mee met de code; een vaste lijst zou de sensor
    laten omvallen bij elke nieuwe toestand."""
    block = SENSOR.split('key="status"')[1].split("ScalperSensor(")[0]
    assert "SensorDeviceClass.ENUM" not in block
    assert "options=" not in block


def test_no_enum_sensor_has_a_literal_list_of_a_known_enum():
    """Vangt de volgende variant van deze fout: een enum overtypen in plaats
    van uitlezen."""
    from gold_scalper.modes import TradingMode

    literal_modes = '"' + '", "'.join(m.value for m in TradingMode) + '"'
    assert literal_modes not in SENSOR, (
        "TradingMode staat letterlijk overgetypt; gebruik de enum"
    )
