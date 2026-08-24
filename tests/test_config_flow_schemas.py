"""Valideert elk selector-configuratieblok tegen Home Assistant's echte regels.

Deze tests bestaan omdat de vorige lichting ze miste: conftest.py stubt de
HA-selectors, dus een ongeldige configuratie kwam er ongemerkt doorheen en
sloeg pas in de UI toe als:

    Config-flow kon niet geladen worden: 400: Bad Request

Die melding noemt het schuldige veld niet, dus zonder deze tests ben je aan het
gokken. Hier worden HA's validatieregels nagebouwd en toegepast op de
configuraties die config_flow.py werkelijk opbouwt.
"""
import ast
import os
import sys
from pathlib import Path

import pytest
import voluptuous as vol

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "gold_scalper"
sys.path.insert(0, str(PKG.parent))

# --- Home Assistant's eigen validatie, nagebouwd -------------------------- #

def _validate_slider(data):
    if data.get("mode") == "box":
        return data
    if "min" not in data or "max" not in data:
        raise vol.Invalid("min en max zijn verplicht in slider-modus")
    return data


NUMBER_SCHEMA = vol.All(
    vol.Schema({
        vol.Optional("min"): vol.Coerce(float),
        vol.Optional("max"): vol.Coerce(float),
        vol.Optional("step"): vol.Any(
            "any", vol.All(vol.Coerce(float), vol.Range(min=1e-3))
        ),
        vol.Optional("unit_of_measurement"): str,
        vol.Optional("mode", default="slider"): vol.In(["box", "slider"]),
    }),
    _validate_slider,
)

SELECT_SCHEMA = vol.Schema({
    vol.Required("options"): vol.All(list, vol.Length(min=1)),
    vol.Optional("multiple", default=False): bool,
    vol.Optional("custom_value", default=False): bool,
    vol.Optional("mode"): vol.In(["list", "dropdown"]),
    vol.Optional("translation_key"): str,
    vol.Optional("sort", default=False): bool,
})


# --- De configuraties uit de broncode halen ------------------------------- #

def _literal(node):
    """Waarde van een AST-node, of een markering als hij dynamisch is."""
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        # Enum-attribuut: SLIDER -> "slider", precies de enum-waarde.
        if isinstance(node, ast.Attribute):
            return node.attr.lower()
        return "__dynamic__"


def _selector_calls(name: str) -> list[dict]:
    """Verzamel elke ``<name>(...)``-aanroep uit config_flow.py als dict.

    Statisch uit de AST in plaats van de module importeren, zodat er geen
    draaiende Home Assistant nodig is en de stubs niet in de weg zitten.
    """
    tree = ast.parse((PKG / "config_flow.py").read_text())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == name):
            continue
        # Aanroep met **kwargs levert geen inspecteerbare velden op; die
        # variant wordt via _number_helper_calls gedekt.
        if any(kw.arg is None for kw in node.keywords):
            continue
        found.append({kw.arg: _literal(kw.value) for kw in node.keywords})
    return found


def _load_number_helper():
    """Voer de échte ``_number``-functie uit met een opvangende stub.

    Het alternatief - nabootsen wat de helper zou moeten doen - test alleen of
    de test klopt, niet of de code klopt. Door de werkelijke functiebody uit te
    voeren meet dit wat er daadwerkelijk naar Home Assistant gaat.
    """
    source = (PKG / "config_flow.py").read_text()
    body = "def _number(" + source.split("def _number(")[1].split("\nclass ")[0]

    class _Mode:
        SLIDER = "slider"
        BOX = "box"

    namespace: dict = {
        "NumberSelector": lambda cfg: cfg,
        "NumberSelectorConfig": lambda **kw: dict(kw),
        "NumberSelectorMode": _Mode,
    }
    exec(compile(body, "<config_flow._number>", "exec"), namespace)  # noqa: S102
    return namespace["_number"]


def _number_helper_calls() -> list[dict]:
    """Elke ``_number(...)``-aanroep van de optiepagina, echt uitgevoerd.

    Die gaan niet rechtstreeks naar NumberSelectorConfig maar via de helper,
    en juist daar zat de fout. Zonder deze extractie blijft de hele
    optiepagina ongetest.
    """
    build = _load_number_helper()
    tree = ast.parse((PKG / "config_flow.py").read_text())
    names = ["minimum", "maximum", "step", "unit", "slider"]
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_number"):
            continue
        args = {}
        for i, arg in enumerate(node.args):
            if i < len(names):
                args[names[i]] = _literal(arg)
        for kw in node.keywords:
            if kw.arg:
                args[kw.arg] = _literal(kw.value)
        found.append(build(**args))
    return found


def test_source_has_selector_configs_to_check():
    """Vangt het geval af waarin de AST-extractie stilletjes niets vindt."""
    assert len(_selector_calls("NumberSelectorConfig")) >= 3
    assert len(_selector_calls("SelectSelectorConfig")) >= 3
    assert len(_number_helper_calls()) >= 10, "optiepagina niet gedekt"


@pytest.mark.parametrize("config", _selector_calls("NumberSelectorConfig"))
def test_every_number_selector_is_valid(config):
    clean = {k: v for k, v in config.items() if v != "__dynamic__"}
    NUMBER_SCHEMA(clean)


@pytest.mark.parametrize("config", _number_helper_calls())
def test_every_options_page_number_is_valid(config):
    """Dit is de test die 'unit_of_measurement=None' zou hebben gevangen."""
    clean = {k: v for k, v in config.items() if v != "__dynamic__"}
    NUMBER_SCHEMA(clean)


@pytest.mark.parametrize("config", _selector_calls("SelectSelectorConfig"))
def test_every_select_selector_is_valid(config):
    clean = {k: v for k, v in config.items() if v != "__dynamic__"}
    if "options" not in clean:
        clean["options"] = ["placeholder"]  # variabele uit const.py
    SELECT_SCHEMA(clean)


def test_no_none_unit_of_measurement_anywhere():
    """Expliciet, want dit was de fout: HA valideert het veld als str."""
    for config in _selector_calls("NumberSelectorConfig") + _number_helper_calls():
        assert config.get("unit_of_measurement", "ok") is not None


def test_number_helper_omits_unit_when_absent():
    """De helper zelf, uitgevoerd in plaats van gelezen."""
    build = _load_number_helper()
    assert "unit_of_measurement" not in build(1, 10, 1)
    assert build(1, 10, 1, "oz")["unit_of_measurement"] == "oz"


# --- Structuur van de flow ------------------------------------------------ #

def test_every_shown_step_has_a_handler():
    """async_show_form(step_id="x") vereist een methode async_step_x."""
    tree = ast.parse((PKG / "config_flow.py").read_text())
    methods = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
    }
    shown = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "async_show_form":
                for kw in node.keywords:
                    if kw.arg == "step_id" and isinstance(kw.value, ast.Constant):
                        shown.add(kw.value.value)
    for step in shown:
        assert f"async_step_{step}" in methods, f"stap '{step}' heeft geen handler"


def test_every_step_is_translated():
    """Een stap zonder vertaling toont rauwe veldnamen aan de gebruiker."""
    import json
    tree = ast.parse((PKG / "config_flow.py").read_text())
    shown = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "async_show_form":
                for kw in node.keywords:
                    if kw.arg == "step_id" and isinstance(kw.value, ast.Constant):
                        shown.add(kw.value.value)
    for name in ("nl", "en"):
        data = json.loads((PKG / "translations" / f"{name}.json").read_text())
        config_steps = set(data["config"]["step"])
        option_steps = set(data["options"]["step"])
        for step in shown:
            assert step in config_steps | option_steps, (
                f"{name}.json mist vertaling voor stap '{step}'"
            )


def test_voluptuous_is_not_stubbed():
    """Bewaakt de fout die deze hele testmodule bijna waardeloos maakte.

    conftest.py stubte aanvankelijk ook voluptuous. Elk schema slikte daardoor
    alles, inclusief de ongeldige config-flow die in de UI neersloeg als
    "400: Bad Request". Een validatietest die niet valideert is erger dan geen
    test, want hij wekt vertrouwen.
    """
    import voluptuous
    assert voluptuous.__file__ is not None, "voluptuous wordt gestubt"
    with pytest.raises(vol.Invalid):
        vol.Schema({vol.Optional("u"): str})({"u": None})
