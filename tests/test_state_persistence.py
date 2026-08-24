"""Toestand die een herstart moet overleven.

De hoofdschakelaar en de noodstop stonden alleen in het geheugen. Gevolg: wie
handel aanzette en daarna herstartte voor een update, kwam terug met een bot
die stilstond zonder melding. En ernstiger: een noodstop verdween bij elke
herstart, waardoor de noodrem net zo betrouwbaar was als de vraag of er
toevallig herstart werd.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.storage.state import RuntimeState


def test_defaults_are_safe():
    """Zonder bewaarde toestand staat alles uit: nooit vanzelf beginnen."""
    state = RuntimeState()
    assert state.enabled is False
    assert state.halted is False
    assert state.consecutive_losses == 0


def test_round_trip_preserves_everything():
    state = RuntimeState(
        enabled=True, halted=True, halt_reason="dagverlies",
        consecutive_losses=3, day="2026-08-24",
        day_start_balance=1000.0, trades_today=17, run_id=8,
    )
    restored = RuntimeState(**state.as_dict())
    assert restored == state


def test_unknown_fields_are_ignored():
    """Een oudere of nieuwere opslagversie mag het laden niet slopen."""
    raw = {"enabled": True, "verzonnen_veld": 42}
    fields = set(RuntimeState.__dataclass_fields__)
    state = RuntimeState(**{k: v for k, v in raw.items() if k in fields})
    assert state.enabled is True


def test_halt_reason_survives():
    """Zonder reden weet je na een herstart niet waaróm hij stilstaat."""
    state = RuntimeState(halted=True, halt_reason="equity onder de ondergrens")
    assert RuntimeState(**state.as_dict()).halt_reason == "equity onder de ondergrens"


def test_daily_counters_survive_a_restart_mid_day():
    """Anders reset de dagverlieslimiet bij elke herstart, en is hij dus geen
    limiet maar een suggestie."""
    state = RuntimeState(day="2026-08-24", day_start_balance=1000.0, trades_today=42)
    restored = RuntimeState(**state.as_dict())
    assert restored.day == "2026-08-24"
    assert restored.day_start_balance == 1000.0
    assert restored.trades_today == 42


def test_coordinator_persists_after_every_cycle():
    """Een noodstop halverwege een cyclus mag niet verloren gaan doordat er
    pas bij het afsluiten wordt opgeslagen."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text()
    update = source.split("async def _async_update_data")[1].split("\n    async def ")[0]
    assert "_persist()" in update, "toestand wordt niet per cyclus bewaard"


def test_coordinator_restores_before_first_cycle():
    """De bewaarde toestand moet gelden vóórdat er iets gebeurt."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text()
    setup = source.split("async def async_setup")[1].split("\n    async def ")[0]
    load_pos = setup.index("async_load()")
    assert "warm_up" not in setup[:load_pos], "historie geladen vóór de toestand"
    assert "self.risk.halt" in setup, "noodstop wordt niet hersteld"
