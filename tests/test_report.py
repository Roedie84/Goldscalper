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


def test_times_are_rendered_in_local_timezone(empty_db):
    """UTC opslaan is juist, UTC tonen is verwarrend: een rapport dat je om
    17:31 opent en 15:31 toont, maakt trades lastig terug te vinden."""
    from zoneinfo import ZoneInfo
    from gold_scalper.dashboard.report import _local

    amsterdam = ZoneInfo("Europe/Amsterdam")
    assert _local("2026-08-24T15:31:00+00:00", amsterdam) == "24-08-2026 17:31"
    # Wintertijd is UTC+1, niet +2
    assert _local("2026-01-15T15:31:00+00:00", amsterdam) == "15-01-2026 16:31"


def test_local_handles_missing_and_broken_values():
    from gold_scalper.dashboard.report import _local
    assert _local(None) == "—"
    assert "kapot" in _local("kapot")


def test_daily_breakdown_groups_on_local_day():
    """Een trade van 01:30 Nederlandse tijd hoort niet op de vorige dag te
    vallen; anders telt het handelsdagen-criterium verkeerd."""
    from zoneinfo import ZoneInfo
    from gold_scalper.storage.database import Trade
    from gold_scalper.storage.performance import daily_breakdown

    trade = Trade(run_id=1, mode="paper", symbol="XAU_USD", side="buy", volume=0.01,
                  open_time="2026-08-23T23:00:00+00:00", open_price=3300.0,
                  open_mid=3300.0, open_spread=0.2,
                  close_time="2026-08-23T23:30:00+00:00", net_pnl=1.0, gross_pnl=1.0)
    utc = daily_breakdown([trade])
    local = daily_breakdown([trade], ZoneInfo("Europe/Amsterdam"))
    assert utc[0]["date"] == "2026-08-23"
    assert local[0]["date"] == "2026-08-24"


def test_equity_curve_is_downsampled_for_long_runs():
    """De grafiek is 720 pixels breed; meer punten leveren geen detail maar
    wel een enorme SVG."""
    from gold_scalper.dashboard.report import _downsample
    values = [float(i) for i in range(5000)]
    out = _downsample(values, 720)
    assert len(out) == 720
    assert out[0] == 0.0 and out[-1] == 4999.0


def test_downsampling_preserves_extremes():
    """Middelen zou drawdowns gladstrijken; juist die wil je zien."""
    from gold_scalper.dashboard.report import _downsample
    values = [100.0] * 1000
    values[500] = 10.0          # scherpe drawdown
    out = _downsample(values, 100)
    assert min(out) == 10.0


def test_short_series_is_left_alone():
    from gold_scalper.dashboard.report import _downsample
    assert _downsample([1.0, 2.0, 3.0], 720) == [1.0, 2.0, 3.0]


def test_compute_for_run_accepts_preloaded_trades(tmp_path):
    """De tradetabel twee keer inlezen kostte 100 ms per cyclus."""
    from gold_scalper.storage.database import TradeDatabase
    from gold_scalper.storage.performance import compute_for_run
    db = TradeDatabase(tmp_path / "r2.db"); db.connect()
    run = db.start_run("paper", "v", "XAU_USD", {}, 1000.0)
    stats = compute_for_run(db, run, [])
    assert stats["trades"] == 0


def test_report_states_whether_money_is_involved(empty_db):
    db, run = empty_db
    html = build_report(db, run)
    assert "Papierhandel" in html
    assert "niets naar je broker" in html


@pytest.mark.parametrize("mode,fragment", [
    ("paper", "berekend, niet gemeten"),
    ("demo", "gemeten in plaats van gemodelleerd"),
    ("live", "echt geld"),
])
def test_report_states_the_mode(tmp_path, mode, fragment):
    """Een geëxporteerd rapport mag niet uit zijn verband gelezen worden."""
    from gold_scalper.storage.database import TradeDatabase
    db = TradeDatabase(tmp_path / f"m{mode}.db"); db.connect()
    run = db.start_run(mode, "v1", "GOLD", {}, 1000.0)
    assert fragment in build_report(db, run)
