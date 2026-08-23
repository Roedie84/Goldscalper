"""Latency-instrumentatie en gebufferd schrijven."""
import os, sys, tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.storage.database import TradeDatabase
from gold_scalper.storage.latency import (
    BufferedWriter, LatencyBudget, LatencyTracker, install_buffered_signals,
)


def test_budget_reports_each_stage():
    b = LatencyBudget()
    b.mark("tick", 0.0); b.mark("signal", 0.010); b.mark("order", 0.055)
    r = b.report()
    assert r["tick->signal"] == pytest.approx(10.0)
    assert r["signal->order"] == pytest.approx(45.0)
    assert r["total"] == pytest.approx(55.0)


def test_tracker_reports_percentiles_not_mean():
    t = LatencyTracker()
    for i in range(100):
        b = LatencyBudget(); b.mark("a", 0.0); b.mark("b", i / 1000.0)
        t.record(b)
    s = t.stats()["a->b"]
    assert s["samples"] == 100
    assert s["p99"] > s["median"]


def test_slowest_stage_identified():
    t = LatencyTracker()
    b = LatencyBudget()
    b.mark("tick", 0.0); b.mark("signal", 0.001); b.mark("order", 0.200)
    t.record(b)
    stage, _ = t.slowest_stage()
    assert stage == "signal->order"


def test_buffered_writer_flushes_on_size():
    flushed = []
    w = BufferedWriter(lambda rows: flushed.extend(rows), max_buffer=10, max_age_seconds=999)
    for i in range(25):
        w.add((i,))
    assert len(flushed) == 20 and w.pending == 5
    w.flush()
    assert len(flushed) == 25


def test_buffered_signals_persist_all_rows(tmp_path):
    db = TradeDatabase(tmp_path / "b.db"); db.connect()
    run = db.start_run("paper", "t", "XAUUSD", {}, 10000.0)
    install_buffered_signals(db)
    for _ in range(500):
        db.log_signal(run, 0.1, 0.5, "flat", None, 0.35, False, "test")
    db.flush_signals()
    assert db.conn.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"] == 500


def test_failed_flush_is_counted_not_silent():
    def boom(rows): raise RuntimeError("db weg")
    w = BufferedWriter(boom, max_buffer=2, max_age_seconds=999)
    w.add((1,)); w.add((2,))
    assert w.dropped == 2
