"""Controleert dat de integratie structureel klopt: manifest, platforms,
services, vertalingen, en dat er geen verboden afhankelijkheden zijn."""
import ast, json, os, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "custom_components" / "gold_scalper"
sys.path.insert(0, str(ROOT / "custom_components"))


def test_manifest_declares_no_requirements():
    """Kernbelofte: geen extra afhankelijkheden."""
    m = json.loads((PKG / "manifest.json").read_text())
    assert m["requirements"] == []
    assert m["domain"] == "gold_scalper"
    assert m["config_flow"] is True


def test_no_windows_only_imports_anywhere():
    """MetaTrader5 is Windows-only en mag nergens meer voorkomen."""
    offenders = []
    for path in PKG.rglob("*.py"):
        source = path.read_text()
        if "MetaTrader5" in source or "import mt5" in source:
            offenders.append(str(path.relative_to(PKG)))
    assert offenders == [], f"Windows-afhankelijkheid in: {offenders}"


def test_every_platform_module_exists_and_parses():
    from gold_scalper.const import PLATFORMS
    for platform in PLATFORMS:
        path = PKG / f"{platform}.py"
        assert path.exists(), f"{platform}.py ontbreekt"
        tree = ast.parse(path.read_text())
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
        assert "async_setup_entry" in names, f"{platform}.py mist async_setup_entry"


def test_services_yaml_matches_registered_services():
    import yaml
    from gold_scalper import const
    declared = set(yaml.safe_load((PKG / "services.yaml").read_text()))
    expected = {
        const.SERVICE_PREPARE_SHUTDOWN, const.SERVICE_CLOSE_ALL,
        const.SERVICE_RESUME, const.SERVICE_GENERATE_REPORT,
    }
    assert expected <= declared


def test_translations_cover_every_config_step():
    """Elke stap in de config flow moet vertaald zijn, anders krijgt de
    gebruiker rauwe veldnamen te zien."""
    for name in ("nl", "en"):
        data = json.loads((PKG / "translations" / f"{name}.json").read_text())
        steps = data["config"]["step"]
        for required in ("user", "simulator", "oanda"):
            assert required in steps, f"{name}.json mist stap '{required}'"
            assert steps[required].get("data"), f"{name}.json: stap '{required}' zonder velden"
        assert steps["oanda"]["data"]["token"]
        assert "options" in data


def test_simulator_is_the_default_venue():
    """Je moet de integratie kunnen installeren zonder ergens een account te hebben."""
    from gold_scalper import const
    assert const.DEFAULT_VENUE == const.VENUE_SIMULATOR


def test_all_modules_parse():
    for path in PKG.rglob("*.py"):
        ast.parse(path.read_text(), filename=str(path))


def test_pure_logic_modules_import_without_home_assistant():
    """Analyse, strategie en opslag mogen niet van HA afhangen; dat houdt ze
    testbaar en herbruikbaar."""
    import importlib
    for module in (
        "gold_scalper.analysis.engine",
        "gold_scalper.strategy.scalping",
        "gold_scalper.strategy.streaming",
        "gold_scalper.storage.database",
        "gold_scalper.storage.performance",
        "gold_scalper.broker.paper",
        "gold_scalper.broker.exits",
        "gold_scalper.broker.risk",
        "gold_scalper.modes",
        "gold_scalper.lifecycle",
        "gold_scalper.dashboard.report",
    ):
        importlib.import_module(module)


def test_oanda_venue_needs_only_aiohttp():
    source = (PKG / "broker" / "oanda.py").read_text()
    tree = ast.parse(source)
    third_party = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            third_party.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            third_party.add(node.module.split(".")[0])
    stdlib = {"logging", "time", "datetime", "__future__", "typing"}
    assert third_party - stdlib == {"aiohttp"}
