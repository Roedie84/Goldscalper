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
