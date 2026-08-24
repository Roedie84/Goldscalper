"""Testopzet.

Het probleem: ``custom_components/gold_scalper/__init__.py`` importeert Home
Assistant, zoals elke integratie doet. Daardoor sleept het importeren van een
zuivere logicamodule als ``storage.database`` de hele HA-boom mee, en die is
hier niet geïnstalleerd.

De oplossing is niet om Home Assistant als testafhankelijkheid toe te voegen -
dat is honderden megabytes en maakt de suite traag en broos. In plaats daarvan
staat hier een minimale nep-``homeassistant`` die alleen bij het importeren
hoeft te werken. Alles wat écht getest wordt (indicatoren, kostenboekhouding,
risicolimieten, poort, exits, rapportage) raakt Home Assistant niet aan.

Het gevolg is dat de kernlogica in elke omgeving te draaien is: op je laptop,
in CI, zonder HA. Dat is bewust: de rekenkern hoort onafhankelijk verifieerbaar
te zijn van het platform waarop hij toevallig draait.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))


class _Anything:
    """Staat alles toe: aanroepen, indexeren, erven, attributen opvragen.

    Genoeg om import-tijd te overleven zonder ook maar iets te doen.
    """

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __call__(self, *args, **kwargs):
        return _Anything()

    def __getattr__(self, name):
        return _Anything()

    def __getitem__(self, key):
        return _Anything()

    def __iter__(self):
        return iter(())

    def __or__(self, other):
        return _Anything()

    def __ror__(self, other):
        return _Anything()

    def __mro_entries__(self, bases):
        # Nodig omdat integratieklassen erven van HA-basisklassen zoals
        # DataUpdateCoordinator[dict]. Python vraagt dan om de echte bases;
        # een lege tuple laat de klasse gewoon van object erven.
        return ()


class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Anything()


class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Fabriceert elke ``homeassistant.*`` module op aanvraag.

    Gebruikt het moderne ``find_spec``-protocol; het oude
    ``find_module``/``load_module`` is in Python 3.12 verwijderd.
    """

    # Bewust alléén homeassistant. Voluptuous is een klein zuiver
    # Python-pakket dat gewoon geïnstalleerd kan worden, en het stubben ervan
    # was een dure fout: alle schemavalidatie slikte dan stilzwijgend alles,
    # waardoor een ongeldige config-flow er ongemerkt doorheen kwam en pas in
    # de UI opdook als "400: Bad Request".
    PREFIXES = ("homeassistant",)

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] not in self.PREFIXES:
            return None
        spec = importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        spec.submodule_search_locations = []
        return spec

    def create_module(self, spec):
        return _StubModule(spec.name)

    def exec_module(self, module):
        module.__path__ = []


def _install_stubs() -> None:
    try:
        import homeassistant  # noqa: F401
        return  # echte HA aanwezig; niets te doen
    except ImportError:
        pass
    sys.meta_path.insert(0, _StubFinder())

    # Een paar constanten die als echte waarde gebruikt worden in plaats van
    # alleen doorgegeven, en die dus geen _Anything mogen zijn.
    import homeassistant.const as ha_const  # type: ignore

    ha_const.PERCENTAGE = "%"
    ha_const.EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"


_install_stubs()
