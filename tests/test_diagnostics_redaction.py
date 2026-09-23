"""Diagnostiekexports mogen geen geheimen bevatten.

Deze exports worden juist gedeeld om hulp te vragen. Toen de IG- en
Capital.com-adapters erbij kwamen, liep de redactielijst achter en kwamen
api_key, identifier en password ongefilterd in elke export terecht.

Een lijst die je met de hand moet bijwerken loopt altijd een keer achter, dus
wordt hier statisch gecontroleerd dat elk configuratieveld dat een geheim kan
bevatten ook geredigeerd wordt.
"""
import os
import re
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "gold_scalper"
sys.path.insert(0, str(PKG.parent))

from gold_scalper.diagnostics import REDACT

#: Woorden die duiden op iets dat toegang geeft.
SENSITIVE = ("key", "password", "secret", "token", "identifier", "login",
             "username", "account_id")


def _config_constants() -> dict[str, str]:
    """Alle CONF_*-constanten met hun waarde."""
    source = (PKG / "const.py").read_text(encoding="utf-8")
    found = {}
    for match in re.finditer(r'^(CONF_\w+):\s*Final\s*=\s*"([^"]+)"', source, re.M):
        found[match.group(1)] = match.group(2)
    return found


def test_constants_were_found():
    """Vangt het geval af waarin de extractie stilletjes niets oplevert."""
    assert len(_config_constants()) > 15


@pytest.mark.parametrize(
    "name,value",
    [
        (n, v) for n, v in _config_constants().items()
        if any(word in v.lower() for word in SENSITIVE)
    ],
)
def test_every_sensitive_field_is_redacted(name, value):
    assert value in REDACT, (
        f"{name} = '{value}' bevat mogelijk een geheim maar wordt niet "
        "geredigeerd in diagnostics.py"
    )


def test_known_secrets_are_covered():
    for field in ("api_key", "password", "token", "identifier", "account_id"):
        assert field in REDACT


def test_report_contains_no_credentials():
    """Het keuringsrapport is zonder authenticatie bereikbaar."""
    import tempfile
    from gold_scalper.dashboard.report import build_report
    from gold_scalper.storage.database import TradeDatabase

    with tempfile.TemporaryDirectory() as folder:
        db = TradeDatabase(os.path.join(folder, "r.db"))
        db.connect()
        run = db.start_run(
            "paper", "v1", "GOLD",
            {"venue": "ig", "api_key": "GEHEIM123", "password": "OOKGEHEIM"},
            1000.0,
        )
        html = build_report(db, run)
    assert "GEHEIM123" not in html
    assert "OOKGEHEIM" not in html
