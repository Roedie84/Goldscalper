"""Het rapportpaneel moet altijd iets tonen, ook als er nog niets gebeurd is.
Een lege iframe is de slechtst denkbare uitkomst: je weet dan niet of het
kapot is of nog leeg."""
import os, sys, tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.dashboard.report import build_report
from gold_scalper.http import PANEL_URL_PATH, REPORT_URL, _placeholder
from gold_scalper.storage.database import TradeDatabase


@pytest.fixture
def fresh_run(tmp_path):
    db = TradeDatabase(tmp_path / "p.db"); db.connect()
    run = db.start_run("paper", "v1", "GC=F",
                       {"venue": "public_data", "simulated": False}, 10000.0)
    return db, run


def test_report_renders_before_any_trade(fresh_run):
    db, run = fresh_run
    html = build_report(db, run)
    assert html.startswith("<!DOCTYPE html>")
    assert "Nog geen gesloten trades" in html
    assert "IN KEURING" in html


def test_report_shows_symbol_and_mode(fresh_run):
    db, run = fresh_run
    html = build_report(db, run)
    assert "GC=F" in html and "paper" in html


def test_report_contains_no_scripts(fresh_run):
    """Het paneel is een iframe; scripts erin zou onnodig risico zijn."""
    db, run = fresh_run
    assert "<script" not in build_report(db, run)


def test_placeholder_is_valid_html():
    page = _placeholder("Titel", "Uitleg voor de gebruiker")
    assert page.startswith("<!DOCTYPE html>")
    assert "Titel" in page and "Uitleg voor de gebruiker" in page


def test_urls_are_stable():
    """Deze paden staan in de documentatie; ze mogen niet ongemerkt wijzigen."""
    assert REPORT_URL == "/api/gold_scalper/report"
    assert PANEL_URL_PATH == "gold-scalper"


def test_view_does_not_require_auth():
    """Bewuste keuze: een iframe stuurt geen bearer-token mee, dus met auth aan
    blijft het paneel leeg. Zie de toelichting in http.py."""
    from gold_scalper.http import GoldScalperReportView
    assert GoldScalperReportView.requires_auth is False


def test_report_never_contains_credentials(fresh_run):
    """De rapportgenerator krijgt token noch account-ID; dit legt dat vast."""
    db, run = fresh_run
    html = build_report(db, run).lower()
    for forbidden in ("token", "password", "wachtwoord", "api_key", "bearer"):
        assert forbidden not in html
