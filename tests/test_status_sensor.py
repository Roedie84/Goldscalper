"""De statussensor moet in gewone taal zeggen waarom er niets gebeurt.

Deze sensor bestaat omdat de informatie wel aanwezig was maar verspreid over
drie entiteiten en hun attributen. De meest voorkomende oorzaak - een
uitgeschakelde hoofdschakelaar - stond alleen in een attribuut van de
signaalsensor, en werd daardoor vier diagnostiekexports lang gemist.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.status import build_status as _status


def data(**over):
    base = {
        "enabled": True, "mode": "paper", "open_positions": [],
        "lifecycle": {"state": "running"}, "risk": {"state": "running"},
        "reject_reason": None, "stats": {"signals": {"evaluations": 0, "acted": 0}},
    }
    base.update(over)
    return base


def test_disabled_switch_is_reported_first():
    """Het geval dat vier keer gemist werd."""
    state, text = _status(data(enabled=False))
    assert state == "uitgeschakeld"
    assert "Handel actief" in text


def test_halt_outranks_disabled():
    """Een noodstop is belangrijker nieuws dan een uitgezette schakelaar."""
    state, text = _status(data(
        enabled=False, risk={"state": "halted", "halt_reason": "dagverlies"}
    ))
    assert state == "noodstop"
    assert "dagverlies" in text
    assert "resume" in text


def test_diverged_positions_block_and_explain():
    state, text = _status(data(lifecycle={"state": "diverged"}))
    assert state == "afgestemd_probleem"
    assert "geblokkeerd" in text


def test_open_position_is_reported():
    state, text = _status(data(open_positions=[object()]))
    assert state == "positie_open"
    assert "1 positie" in text


@pytest.mark.parametrize("reason,fragment", [
    ("outside_hours", "handelsvenster"),
    ("spread_too_wide", "Spread"),
    ("edge_below_cost", "kosten"),
    ("score_below_threshold", "zwak"),
    ("cooldown", "Wachttijd"),
])
def test_reject_reasons_get_readable_text(reason, fragment):
    state, text = _status(data(reject_reason=reason))
    assert state == "wachtend"
    assert fragment in text


def test_unknown_reject_reason_is_passed_through():
    """Liever de ruwe reden dan niets; een onbekende code blijft leesbaar."""
    _, text = _status(data(reject_reason="iets_nieuws"))
    assert "iets_nieuws" in text


def test_active_and_waiting():
    state, text = _status(data())
    assert state == "wachtend"
    assert "wachten" in text.lower()


def test_status_is_the_first_sensor():
    """Wie zich afvraagt waarom er niets gebeurt, moet hem meteen zien.

    Statisch gecontroleerd: sensor.py importeert Home Assistant en is daarom
    niet te importeren zonder draaiende HA.
    """
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "sensor.py").read_text()
    block = source.split("SENSORS: tuple[ScalperSensor, ...] = (")[1]
    first_key = block.split('key="')[1].split('"')[0]
    assert first_key == "status"


def test_status_module_has_no_home_assistant_imports():
    """Pure tekstlogica moet los van het platform testbaar blijven."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "status.py").read_text()
    assert "homeassistant" not in source
