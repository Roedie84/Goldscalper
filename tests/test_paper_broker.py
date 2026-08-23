"""Tests voor de kostenboekhouding. Dit is het deel dat absoluut moet kloppen:
als de kosten verkeerd geboekt worden, is de hele bewijsfase waardeloos."""
import os, sys, tempfile
from datetime import datetime, timezone, timedelta
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.storage.database import TradeDatabase
from gold_scalper.storage import performance
from gold_scalper.broker.paper import PaperBroker, BrokerCosts, Quote, CONTRACT_SIZE, InsufficientMargin


@pytest.fixture
def broker(tmp_path):
    db = TradeDatabase(tmp_path / "t.db"); db.connect()
    costs = BrokerCosts(commission_per_lot_per_side=3.5, base_slippage=0.0,
                        volatility_slippage_factor=0.0, size_slippage_per_lot=0.0)
    run = db.start_run("paper", "test", "XAUUSD", {}, 10000.0)
    return PaperBroker(db, run, "XAUUSD", 10000.0, costs, seed=1), db


def q(mid, spread=0.25, t=None):
    return Quote(bid=mid - spread/2, ask=mid + spread/2,
                 time=t or datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc), atr=0.0)


def test_buy_fills_at_ask_sell_at_bid(broker):
    bk, _ = broker
    quote = q(3300.0)
    long = bk.open_position("buy", 0.10, quote)
    assert long.open_price == pytest.approx(quote.ask)


def test_gross_minus_net_equals_cost(broker):
    bk, _ = broker
    t = bk.open_position("buy", 0.10, q(3300.0))
    closed = bk.close_position(t, q(3300.50, t=datetime(2026,8,22,12,0,30,tzinfo=timezone.utc)), "test")
    assert closed.total_cost == pytest.approx(closed.gross_pnl - closed.net_pnl)


def test_known_pnl_by_hand(broker):
    """Long 0.10 lot, mid 3300 -> 3300.50, spread 0.25, commissie 3.50/lot/zijde."""
    bk, _ = broker
    t = bk.open_position("buy", 0.10, q(3300.0))
    closed = bk.close_position(t, q(3300.50, t=datetime(2026,8,22,12,0,30,tzinfo=timezone.utc)), "test")
    # netto = (3300.375 - 3300.125)*10 - 0.70
    assert closed.net_pnl == pytest.approx(1.80, abs=1e-6)
    assert closed.gross_pnl == pytest.approx(5.00, abs=1e-6)
    assert bk.balance == pytest.approx(10001.80, abs=1e-6)


def test_short_is_symmetric(broker):
    bk, _ = broker
    t = bk.open_position("sell", 0.10, q(3300.0))
    closed = bk.close_position(t, q(3299.50, t=datetime(2026,8,22,12,0,30,tzinfo=timezone.utc)), "test")
    assert closed.net_pnl == pytest.approx(1.80, abs=1e-6)


def test_flat_market_always_loses(broker):
    """Zonder koersbeweging moet een round trip per definitie verlies opleveren.
    Als deze test faalt, wordt er ergens een kostenpost gemist."""
    bk, _ = broker
    t = bk.open_position("buy", 0.10, q(3300.0))
    closed = bk.close_position(t, q(3300.0, t=datetime(2026,8,22,12,0,30,tzinfo=timezone.utc)), "test")
    assert closed.net_pnl < 0
    assert closed.gross_pnl == pytest.approx(0.0, abs=1e-9)


def test_stop_loss_triggers_on_exit_price_not_mid(broker):
    bk, _ = broker
    t = bk.open_position("buy", 0.10, q(3300.0), stop_loss=3299.50)
    # mid 3299.60 -> bid 3299.475, onder de stop
    closed = bk.update_positions(q(3299.60, t=datetime(2026,8,22,12,0,10,tzinfo=timezone.utc)))
    assert len(closed) == 1 and closed[0].close_reason == "stop_loss"


def test_margin_is_enforced(broker):
    bk, _ = broker
    with pytest.raises(InsufficientMargin):
        bk.open_position("buy", 100.0, q(3300.0))


def test_verdict_blocks_on_insufficient_trades(broker):
    bk, db = broker
    t = bk.open_position("buy", 0.10, q(3300.0))
    bk.close_position(t, q(3301.0, t=datetime(2026,8,22,12,0,30,tzinfo=timezone.utc)), "test")
    stats = performance.compute_for_run(db, bk.run_id)
    assert stats["verdict"] == "insufficient_data"
    assert stats["ready_for_live"] is False


def test_mae_mfe_tracked(broker):
    bk, _ = broker
    t = bk.open_position("buy", 0.10, q(3300.0))
    bk.update_positions(q(3301.0, t=datetime(2026,8,22,12,0,10,tzinfo=timezone.utc)))
    bk.update_positions(q(3299.0, t=datetime(2026,8,22,12,0,20,tzinfo=timezone.utc)))
    assert t.mfe > 0 and t.mae < 0
