"""Statische controle op de hele codebase.

Deze module bestaat om een concrete storing: `import json` ontbrak in
coordinator.py, waardoor de integratie bij het opstarten faalde met
`NameError: name 'json' is not defined`. Geen enkele test ving dat, omdat de
betreffende functie alleen draait binnen een echte Home Assistant.

Een ontbrekende import is precies het soort fout dat je niet met unittests moet
zoeken maar met een parser: die vindt hem in milliseconden, over álle regels,
ook de regels die geen test ooit aanraakt.
"""
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "gold_scalper"


def _pyflakes() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pyflakes", str(PKG)],
        capture_output=True, text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


pyflakes_available = (
    subprocess.run([sys.executable, "-m", "pyflakes", "--version"],
                   capture_output=True).returncode == 0
)
requires_pyflakes = pytest.mark.skipif(
    not pyflakes_available, reason="pyflakes niet geïnstalleerd"
)


@requires_pyflakes
def test_no_undefined_names():
    """De fout die de integratie sloopte: een naam gebruiken zonder import."""
    problems = [line for line in _pyflakes() if "undefined name" in line]
    assert problems == [], "\n".join(problems)


@requires_pyflakes
def test_no_unused_local_variables():
    """Meestal onschuldig, soms het spoor van een half afgemaakte wijziging."""
    problems = [
        line for line in _pyflakes()
        if "assigned to but never used" in line
    ]
    assert problems == [], "\n".join(problems)


@requires_pyflakes
def test_no_broken_fstrings():
    """Een f-string zonder placeholders is bijna altijd een vergeten variabele."""
    problems = [line for line in _pyflakes() if "f-string is missing" in line]
    assert problems == [], "\n".join(problems)


@requires_pyflakes
def test_no_redefinitions():
    problems = [line for line in _pyflakes() if "redefinition of unused" in line]
    assert problems == [], "\n".join(problems)


# --- Controles die zonder pyflakes werken -------------------------------- #

def test_every_module_parses():
    for path in PKG.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_no_leftover_debug_statements():
    """print() en breakpoint() horen niet in een integratie die onbeheerd draait."""
    offenders = []
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ("print", "breakpoint"):
                    offenders.append(f"{path.name}:{node.lineno} {node.func.id}()")
    assert offenders == [], "\n".join(offenders)


def test_no_bare_except():
    """`except:` vangt ook KeyboardInterrupt en SystemExit."""
    offenders = []
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], "\n".join(offenders)


def test_all_source_files_are_utf8():
    """Eerder ging ENTITEITEN.md hierop stuk; nu ook de broncode bewaakt."""
    for path in list(PKG.rglob("*.py")) + list(PKG.rglob("*.json")):
        path.read_text(encoding="utf-8")


def test_no_dict_get_with_too_many_arguments():
    """`dict.get()` neemt hoogstens twee argumenten.

    Een zoek-en-vervangactie op het korte patroon `CONF_MAX_SPREAD,` sloeg
    blind toe midden in een aanroep en maakte er
    `options.get(CONF_MAX_SPREAD, CONF_MAX_SPREAD_ATR, 3.00)` van. Dat crasht
    pas bij het opstarten van de integratie; pyflakes ziet het niet, want
    syntactisch klopt het.
    """
    offenders = []
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and len(node.args) > 2):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], "\n".join(offenders)


# Een test op dubbele positionele argumenten is hier weggelaten. Hij vond
# `_Bar(start, price, price, price, price)`, waar dezelfde waarde terecht
# vier keer wordt doorgegeven omdat open, high, low en close bij een nieuwe
# bar gelijk zijn. Een test die vals alarm geeft leer je negeren, en dan vangt
# hij ook de echte gevallen niet meer.


def _dataclass_fields(path: Path, name: str) -> set[str]:
    """Veldnamen van een dataclass, uit de bron gelezen."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {
                item.target.id for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            }
    return set()


def test_config_attributes_actually_exist():
    """Vangt verzonnen attributen op configuratieobjecten.

    `self.exits.config.take_profit_atr` bestond niet - die multiplier hoort bij
    de strategie, niet bij de exitmanager. De integratie viel daarop om in de
    handelslus, en 449 tests merkten er niets van omdat geen enkele test die
    lus doorloopt met echte objecten.
    """
    sources = {
        "self.strategy_cfg": _dataclass_fields(
            PKG / "strategy" / "scalping.py", "ScalpConfig"
        ),
        "self.exits.config": _dataclass_fields(
            PKG / "broker" / "exits.py", "ExitConfig"
        ),
        "self.risk.limits": _dataclass_fields(
            PKG / "broker" / "risk.py", "RiskLimits"
        ),
        "self.paper.costs": _dataclass_fields(
            PKG / "broker" / "paper.py", "BrokerCosts"
        ),
    }
    for name, fields in sources.items():
        assert fields, f"geen velden gevonden voor {name}; de test zelf is stuk"

    coordinator = (PKG / "coordinator.py").read_text(encoding="utf-8")
    offenders = []
    for prefix, fields in sources.items():
        for match in re.finditer(
            rf"{re.escape(prefix)}\.(\w+)", coordinator
        ):
            attribute = match.group(1)
            if attribute not in fields:
                line = coordinator[: match.start()].count("\n") + 1
                offenders.append(f"coordinator.py:{line}  {prefix}.{attribute}")
    assert offenders == [], "\n".join(offenders)


def test_broker_positions_are_fetched_once_per_cycle():
    """Vier aanroepen naar de broker per cyclus verviervoudigde de cyclustijd
    zodra de posities daar stonden in plaats van in de papersimulatie: bij tien
    seconden verversen zijn dat vierentwintig verzoeken per minuut, precies waar
    rate limits vandaan komen."""
    source = (PKG / "coordinator.py").read_text(encoding="utf-8")
    body = source.split("async def _open_positions")[1].split("\n    async def ")[0]
    assert "_positions_cache" in body, "geen cache binnen de cyclus"
    assert "refresh" in body, "geen manier om de cache te verversen"


def test_cache_is_cleared_at_the_start_of_a_cycle():
    """Een cache die een cyclus overleeft, laat de bot handelen op posities van
    tien seconden geleden."""
    source = (PKG / "coordinator.py").read_text(encoding="utf-8")
    update = source.split("async def _async_update_data")[1][:600]
    assert "self._positions_cache = None" in update


def test_cache_is_cleared_after_placing_or_closing():
    source = (PKG / "coordinator.py").read_text(encoding="utf-8")
    close = source.split("async def _close_position")[1].split("\n    def ")[0]
    assert "_positions_cache = None" in close, (
        "na sluiten blijft de gecachte lijst verouderd achter"
    )


def test_home_assistant_imports_resolve():
    """Vangt namen die uit de verkeerde Home Assistant-module worden gehaald.

    Een zoek-en-vervangactie op `from homeassistant.core import HomeAssistant`
    raakte de langere regel `... import HomeAssistant, ServiceCall` en
    verplaatste ServiceCall naar `exceptions`. Dat is syntactisch correct en
    pyflakes ziet het niet; de integratie viel er pas bij het laden op om.
    """
    pytest.importorskip("homeassistant", reason="Home Assistant niet geïnstalleerd")
    import importlib

    problems = []
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if not (node.module or "").startswith("homeassistant"):
                continue
            try:
                module = importlib.import_module(node.module)
            except Exception:
                # Sommige componenten laten zich niet importeren zonder een
                # draaiende Home Assistant. Dat is geen fout in onze code, en
                # zo'n geval als probleem melden maakt de test onbruikbaar.
                continue
            for alias in node.names:
                if hasattr(module, alias.name):
                    continue
                # Een submodule is pas een attribuut ná het importeren; dat is
                # geen fout maar hoe pakketten werken.
                try:
                    importlib.import_module(f"{node.module}.{alias.name}")
                    continue
                except Exception:
                    pass
                problems.append(
                    f"{path.name}:{node.lineno}  {node.module}.{alias.name}"
                )
    assert problems == [], "\n".join(problems)


def test_no_classes_fabricated_from_dicts():
    """`type("G", (), some_dict)()` maakt een klasse met de sleutels van het
    dict als attributen. Dat lijkt te werken tot de namen niet overeenkomen -
    en dan gooit de foutmelding zélf een AttributeError, precies op het moment
    dat je een duidelijke melding nodig had.
    """
    offenders = []
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "type"
                    and len(node.args) == 3):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], "\n".join(offenders)
