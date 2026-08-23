"""Het rapport moet zelfstandig zijn en niet omvallen op lege data."""
import os, sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.storage.database import TradeDatabase
from gold_scalper.dashboard.report import build_report, write_report, _fineness


@pytest.fixture
def empty_db(tmp_path):
    db = TradeDatabase(tmp_path / "r.db"); db.connect()
    run = db.start_run("paper", "v1", "XAUUSD", {}, 10000.0)
    return db, run


def test_report_renders_with_no_trades(empty_db):
    db, run = empty_db
    html = build_report(db, run)
    assert "<!DOCTYPE html>" in html and "Keuringsrapport" in html


def test_report_is_self_contained(empty_db):
    """Geen CDN, geen externe scripts: het bestand moet over twee jaar nog werken."""
    db, run = empty_db
    html = build_report(db, run)
    assert "<script" not in html
    assert "cdn." not in html and "googleapis" not in html


def test_fineness_zero_when_costs_exceed_gross():
    assert _fineness({"gross_pnl": 100.0, "net_pnl": -20.0}) == 0
    assert _fineness({"gross_pnl": 0.0, "net_pnl": 0.0}) == 0


def test_fineness_is_parts_per_thousand():
    assert _fineness({"gross_pnl": 100.0, "net_pnl": 36.0}) == 360
    assert _fineness({"gross_pnl": 100.0, "net_pnl": 100.0}) == 1000


def test_write_report_creates_file(empty_db, tmp_path):
    db, run = empty_db
    path = write_report(db, run, tmp_path / "out" / "r.html")
    assert path.exists() and path.stat().st_size > 2000


def test_html_escaping_of_hostile_strategy_name(tmp_path):
    db = TradeDatabase(tmp_path / "x.db"); db.connect()
    run = db.start_run("paper", "<script>alert(1)</script>", "XAUUSD", {}, 100.0)
    html = build_report(db, run)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_gate_reasons_appear_in_report(empty_db):
    db, run = empty_db
    gate = {"unlocked": False, "blocking_reasons": ["te weinig handelsdagen"]}
    assert "te weinig handelsdagen" in build_report(db, run, gate)
