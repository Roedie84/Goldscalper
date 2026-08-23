"""De poort en de noodremmen. Dit zijn de tests die er het meest toe doen:
alle andere gaan over of het systeem goed werkt, deze over of het veilig faalt."""
import os, sys
from datetime import date, datetime, timedelta, timezone
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.modes import (
    LiveGate, ModeLockedError, TradingMode, require_live_unlocked,
)
from gold_scalper.broker.risk import RiskLimits, RiskManager, TradingState

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def good_stats(**over):
    base = dict(trades=600, ready_for_live=True, blocking_reasons=[],
                net_pnl=1000.0, total_costs=450.0)
    base.update(over); return base


def good_run(days_ago=45):
    return {"started_at": (NOW - timedelta(days=days_ago)).isoformat()}


def good_daily(n=20, each=50.0):
    return [{"date": f"2026-07-{i+1:02d}", "trades": 30, "net_pnl": each} for i in range(n)]


# ---------------- de poort ----------------

def test_gate_opens_when_everything_passes():
    r = LiveGate().evaluate(good_stats(), good_run(), good_daily())
    assert r.unlocked, r.reasons


def test_gate_blocks_on_too_few_trades():
    r = LiveGate().evaluate(good_stats(trades=50), good_run(), good_daily())
    assert not r.unlocked and not r.checks["genoeg_trades"]


def test_gate_blocks_on_too_short_period():
    r = LiveGate().evaluate(good_stats(), good_run(days_ago=3), good_daily())
    assert not r.unlocked and not r.checks["genoeg_verstreken_tijd"]


def test_gate_blocks_when_few_active_days():
    r = LiveGate().evaluate(good_stats(), good_run(), good_daily(n=4))
    assert not r.unlocked and not r.checks["genoeg_handelsdagen"]


def test_gate_blocks_when_profit_is_one_lucky_day():
    daily = good_daily(n=20, each=10.0)
    daily[0]["net_pnl"] = 5000.0
    r = LiveGate().evaluate(good_stats(net_pnl=5190.0), good_run(), daily)
    assert not r.unlocked and not r.checks["winst_goed_verdeeld"]


def test_gate_blocks_on_failed_performance_verdict():
    stats = good_stats(ready_for_live=False, blocking_reasons=["kosten te hoog"])
    r = LiveGate().evaluate(stats, good_run(), good_daily())
    assert not r.unlocked and "kosten te hoog" in r.reasons


def test_live_order_raises_when_gate_closed():
    r = LiveGate().evaluate(good_stats(trades=10), good_run(), good_daily())
    with pytest.raises(ModeLockedError):
        require_live_unlocked(TradingMode.LIVE, r)


def test_paper_never_blocked_by_gate():
    r = LiveGate().evaluate(good_stats(trades=0), good_run(days_ago=0), [])
    require_live_unlocked(TradingMode.PAPER, r)  # mag niet gooien


# ---------------- noodremmen ----------------

def fresh(**over):
    limits = RiskLimits(**over)
    return RiskManager(limits, 10000.0)


def args(**over):
    base = dict(now=NOW, balance=10000.0, equity=10000.0, starting_balance=10000.0,
                open_positions=0, volume=0.01, spread=0.35, last_tick_age=1.0)
    base.update(over); return base


def test_normal_conditions_allow_trading():
    ok, reason = fresh().can_open(**args())
    assert ok and reason is None


def test_daily_loss_limit_halts():
    rm = fresh(max_daily_loss_pct=2.0)
    ok, _ = rm.can_open(**args(balance=9700.0))
    assert not ok and rm.state.state is TradingState.HALTED


def test_equity_floor_halts():
    rm = fresh(equity_floor_pct=80.0)
    ok, _ = rm.can_open(**args(equity=7500.0))
    assert not ok and rm.state.state is TradingState.HALTED


def test_dead_data_feed_halts():
    """Het gevaarlijkste scenario: de bot denkt de prijs te kennen maar
    die is minuten oud."""
    rm = fresh(max_data_staleness_seconds=30)
    ok, _ = rm.can_open(**args(last_tick_age=120.0))
    assert not ok and rm.state.state is TradingState.HALTED


def test_trade_count_limit_halts():
    rm = fresh(max_trades_per_day=5)
    for _ in range(5):
        rm.record_open()
    ok, _ = rm.can_open(**args())
    assert not ok and rm.state.state is TradingState.HALTED


def test_consecutive_losses_pause_then_resume():
    rm = fresh(max_consecutive_losses=3, cooldown_minutes=60)
    for _ in range(3):
        rm.record_close(-5.0, NOW)
    assert rm.state.state is TradingState.PAUSED
    ok, _ = rm.can_open(**args())
    assert not ok
    ok, _ = rm.can_open(**args(now=NOW + timedelta(minutes=61)))
    assert ok


def test_a_win_resets_the_loss_streak():
    rm = fresh(max_consecutive_losses=3)
    rm.record_close(-5.0, NOW); rm.record_close(-5.0, NOW); rm.record_close(+2.0, NOW)
    assert rm.state.consecutive_losses == 0
    assert rm.state.state is TradingState.RUNNING


def test_wide_spread_blocks_but_does_not_halt():
    rm = fresh(max_spread=0.60)
    ok, _ = rm.can_open(**args(spread=1.20))
    assert not ok and rm.state.state is TradingState.RUNNING


def test_oversized_volume_blocked():
    ok, reason = fresh(max_volume=0.10).can_open(**args(volume=1.0))
    assert not ok and "volume" in reason


def test_halt_survives_day_rollover():
    """Een noodstop hoort niet om middernacht vanzelf op te lossen."""
    rm = fresh()
    rm.halt("test")
    ok, _ = rm.can_open(**args(now=NOW + timedelta(days=2)))
    assert not ok and rm.state.state is TradingState.HALTED


def test_manual_resume_is_required_after_halt():
    rm = fresh()
    rm.halt("test")
    rm.manual_resume()
    ok, _ = rm.can_open(**args())
    assert ok


def test_stale_positions_flagged_for_force_close():
    class T:
        open_time = (NOW - timedelta(seconds=1800)).isoformat()
    rm = fresh(max_position_age_seconds=900)
    assert len(rm.positions_to_force_close(NOW, [T()])) == 1
