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

#: Uitkomst van de consistentietoets die de poort accepteert.
GOOD_ROBUSTNESS = {"verdict": "houdbaar", "explanation": "consistent over de tijd"}


def test_gate_opens_when_everything_passes():
    r = LiveGate().evaluate(
        good_stats(), good_run(), good_daily(), GOOD_ROBUSTNESS
    )
    assert r.unlocked, r.reasons


def test_gate_stays_shut_without_a_consistency_verdict():
    """Alle andere eisen gehaald, maar de consistentie is niet vastgesteld.
    Dan is de poort dicht: hoeveelheid is geen betekenis."""
    r = LiveGate().evaluate(good_stats(), good_run(), good_daily())
    assert not r.unlocked
    assert not r.checks["houdt_stand_over_tijd"]


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
    # Expliciet dezelfde dag als NOW: anders hangt de uitkomst af van de
    # wandklok van de machine waarop de test draait.
    return RiskManager(RiskLimits(**over), 10000.0, now=NOW)


def args(**over):
    base = dict(now=NOW, balance=10000.0, equity=10000.0, starting_balance=10000.0,
                open_positions=0, volume=0.01, spread=0.35, last_tick_age=1.0,
                market_open=True, atr=4.1)
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


def test_spread_limit_is_relative_to_volatility():
    """Een absolute grens deugt niet als primaire toets: bij goud op 4640 met
    een ATR van 4,1 is een spread van 0,80 normaal, bij goud op 3300 met een
    ATR van 1,0 is diezelfde spread veel te breed."""
    rm = fresh(max_spread_atr_ratio=0.75, max_spread=10.0)
    ok, _ = rm.can_open(**args(spread=0.80, atr=4.1))
    assert ok, "normale spread bij hoge volatiliteit hoort door te komen"

    ok, reason = rm.can_open(**args(spread=0.80, atr=1.0))
    assert not ok and "ATR" in reason


def test_risk_net_is_wider_than_the_strategy_filter():
    """Het vangnet en het filter zijn twee verschillende dingen; ze door
    elkaar halen leverde een grens op die normale spreads weigerde."""
    from gold_scalper.broker.risk import RiskLimits
    from gold_scalper.strategy.scalping import ScalpConfig
    assert RiskLimits().max_spread_atr_ratio > ScalpConfig().max_spread_atr_ratio


def test_absolute_limit_still_catches_a_blowout():
    rm = fresh(max_spread=10.0)
    ok, reason = rm.can_open(**args(spread=25.0, atr=4.1))
    assert not ok and "vangnet" in reason


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


def test_backwards_clock_does_not_reset_daily_loss():
    """Een klok die terugspringt - NTP-correctie, tijdzonewissel, herstart met
    verkeerde tijd - mag de dagverlieslimiet niet wissen. Juist dan moet hij
    blijven staan."""
    rm = fresh(max_daily_loss_pct=2.0)
    rm.can_open(**args(balance=10000.0))
    yesterday = NOW - timedelta(days=1)
    ok, _ = rm.can_open(**args(now=yesterday, balance=9700.0))
    assert not ok
    assert rm.state.state is TradingState.HALTED
    assert rm.state.day_start_balance == 10000.0


def test_forward_day_rollover_resets_counters():
    rm = fresh(max_trades_per_day=5)
    for _ in range(5):
        rm.record_open()
    ok, _ = rm.can_open(**args(now=NOW + timedelta(days=1), balance=10000.0))
    assert ok and rm.state.trades_today == 0


def test_risk_manager_day_is_explicit_not_wall_clock():
    """Anders hangt het gedrag rond middernacht af van wanneer je draait."""
    from datetime import date
    rm = RiskManager(RiskLimits(), 10000.0, now=datetime(2020, 1, 15, tzinfo=timezone.utc))
    assert rm.state.day == date(2020, 1, 15)


def test_closed_market_does_not_halt():
    """Goud handelt niet in het weekend en kent een dagelijkse onderbreking.
    De laatste koers is dan uren oud zonder dat er iets mis is.

    Zonder dit onderscheid legt de bot zichzelf de eerste vrijdagavond
    permanent stil met een noodstop die handmatig hervat moet worden."""
    rm = fresh(max_data_staleness_seconds=30)
    ok, reason = rm.can_open(**args(last_tick_age=48000.0, market_open=False))
    assert not ok
    assert reason == "markt gesloten"
    assert rm.state.state is TradingState.RUNNING


def test_stale_data_during_open_market_still_halts():
    """Tijdens handelsuren is verouderde data wél een storing."""
    rm = fresh(max_data_staleness_seconds=30)
    ok, _ = rm.can_open(**args(last_tick_age=300.0, market_open=True))
    assert not ok
    assert rm.state.state is TradingState.HALTED


def test_trading_resumes_after_market_reopens():
    """Na sluiting moet hij vanzelf weer aan; geen handmatige actie nodig."""
    rm = fresh(max_data_staleness_seconds=30)
    rm.can_open(**args(last_tick_age=48000.0, market_open=False))
    ok, _ = rm.can_open(**args(last_tick_age=2.0, market_open=True))
    assert ok


def test_only_one_definition_of_a_wide_spread():
    """Er zaten drie spreadgrenzen in de code - strategie, risicobewaking en
    de sensor - en ze werden niet tegelijk aangepast. Dan toont de sensor
    groen terwijl de strategie weigert.

    Deze test controleert dat elke plek die 'te breed' beoordeelt, dat
    relatief aan de ATR doet.
    """
    from pathlib import Path

    pkg = Path(__file__).resolve().parent.parent / "custom_components" / "gold_scalper"
    for path, marker in [
        (pkg / "strategy" / "scalping.py", "spread_ratio"),
        (pkg / "broker" / "risk.py", "max_spread_atr_ratio"),
        (pkg / "binary_sensor.py", "max_spread_atr_ratio"),
    ]:
        source = path.read_text(encoding="utf-8")
        assert marker in source, f"{path.name} toetst de spread niet relatief"


# ---------------- demo-modus ----------------

def test_demo_places_orders_but_is_not_real_money():
    assert TradingMode.DEMO.places_orders is True
    assert TradingMode.DEMO.uses_real_money is False
    assert TradingMode.LIVE.places_orders is True
    assert TradingMode.LIVE.uses_real_money is True
    assert TradingMode.PAPER.places_orders is False


def test_demo_does_not_require_the_gate():
    """Op demo is er niets te beschermen behalve de kwaliteit van je meting,
    en juist die meting is het doel."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text(encoding="utf-8")
    block = source.split("self.venue.supports_trading = bool(")[1].split(")")[0]
    assert "TradingMode.DEMO" in block
    assert "gate" in block and "LIVE" in block


def test_live_still_requires_the_gate():
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text(encoding="utf-8")
    block = source.split("self.venue.supports_trading = bool(")[1].split("\n\n")[0]
    assert 'self.mode is TradingMode.LIVE and self.gate["unlocked"]' in block


def test_unrealised_loss_counts_towards_the_daily_limit():
    """Balance bevat alleen gesloten trades. Vier open posities met samen 200
    verlies gingen daardoor onopgemerkt voorbij een daglimiet van 2%."""
    rm = fresh(max_daily_loss_pct=2.0)
    ok, reason = rm.can_open(**args(
        balance=10000.0,     # nog niets afgerekend
        equity=9700.0,       # 300 onrealiseerd verlies
        starting_balance=10000.0,
    ))
    assert not ok
    assert rm.state.state is TradingState.HALTED
    assert "niet gerealiseerd" in (rm.state.halt_reason or "")


def test_realised_loss_is_not_masked_by_an_open_winner():
    """De strengste van de twee telt: een gerealiseerd verlies boven de limiet
    mag niet verdwijnen achter een open positie die toevallig in de plus staat."""
    rm = fresh(max_daily_loss_pct=2.0)
    ok, _ = rm.can_open(**args(
        balance=9700.0,      # 300 al verloren
        equity=10100.0,      # open positie staat in de plus
        starting_balance=10000.0,
    ))
    assert not ok


def test_healthy_account_is_not_halted():
    rm = fresh(max_daily_loss_pct=2.0)
    ok, _ = rm.can_open(**args(
        balance=10000.0, equity=9950.0, starting_balance=10000.0
    ))
    assert ok


def test_no_code_path_treats_demo_as_paper():
    """Demo plaatst echte orders. Elke plek die op `is TradingMode.LIVE` toetst
    behandelt demo als papermodus, en dat gaf een integratie die in demomodus
    haar eigen posities niet zag: de limiet van één positie sloeg nooit aan en
    er stapelden zich vier longs op in een dalende markt."""
    from pathlib import Path

    pkg = Path(__file__).resolve().parent.parent / "custom_components" / "gold_scalper"
    offenders = []
    for path in pkg.rglob("*.py"):
        if path.name == "modes.py":
            continue      # daar is de enum zelf gedefinieerd
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "uses_real_money" in stripped:
                continue
            if "is TradingMode.LIVE" in stripped and "gate" not in stripped:
                offenders.append(f"{path.name}:{number}  {stripped[:70]}")
    assert offenders == [], (
        "gebruik mode.places_orders in plaats van een toets op LIVE:\n"
        + "\n".join(offenders)
    )


# ---------------- hervatten na een noodstop ----------------

def test_resume_rebaselines_the_day():
    """Zonder nieuw ijkpunt is hervatten zinloos: de volgende cyclus rekent
    het dagverlies opnieuw uit vanaf hetzelfde beginsaldo en stopt meteen weer.
    """
    rm = fresh(max_daily_loss_pct=2.0)
    rm.can_open(**args(balance=9740.0, equity=9740.0, starting_balance=10000.0))
    assert rm.state.state is TradingState.HALTED

    ok, _ = rm.manual_resume(balance=9740.0)
    assert ok
    assert rm.state.day_start_balance == 9740.0

    # Nu mag hij weer, want er is sindsdien niets verloren.
    allowed, _ = rm.can_open(
        **args(balance=9740.0, equity=9740.0, starting_balance=10000.0)
    )
    assert allowed, "hervatten leverde meteen weer een noodstop op"


def test_resume_without_balance_still_works():
    rm = fresh()
    rm.halt("test")
    ok, _ = rm.manual_resume()
    assert ok and rm.state.state is TradingState.RUNNING


def test_resuming_is_capped_per_day():
    """Onbeperkt hervatten maakt van de daglimiet een suggestie: je kunt dan
    telkens opnieuw hetzelfde percentage verliezen."""
    rm = fresh(max_resumes_per_day=2)
    for i in range(2):
        rm.halt(f"poging {i}")
        ok, _ = rm.manual_resume(balance=9500.0 - i * 100)
        assert ok

    rm.halt("derde keer")
    ok, message = rm.manual_resume(balance=9300.0)
    assert not ok
    assert "morgen" in message
    assert rm.state.state is TradingState.HALTED


def test_new_day_restores_the_resume_budget():
    from datetime import timedelta
    rm = fresh(max_resumes_per_day=1)
    rm.halt("x")
    rm.manual_resume(balance=9800.0)
    rm.halt("y")
    assert not rm.manual_resume(balance=9800.0)[0]

    rm.can_open(**args(now=NOW + timedelta(days=1), balance=9800.0))
    assert rm.state.resumes_today == 0


def test_resume_count_is_visible():
    rm = fresh(max_resumes_per_day=2)
    rm.halt("x")
    rm.manual_resume(balance=9900.0)
    data = rm.as_dict()
    assert data["resumes_today"] == 1
    assert data["max_resumes_per_day"] == 2


# ---------------- de poortcontrole ----------------

def test_gate_check_accepts_the_dict_form():
    """De coordinator geeft het dict door, niet het GateResult. Eerder werd er
    met type("G", (), gate)() een klasse uit gefabriceerd; die kreeg attributen
    met de dict-namen, waardoor gate.reasons niet bestond en de foutmelding
    zélf een AttributeError opgooide."""
    from gold_scalper.modes import ModeLockedError, require_live_unlocked

    gate = {
        "unlocked": False,
        "blocking_reasons": ["0 trades in de bewijsfase"],
        "checks": {}, "summary": "vergrendeld",
    }
    with pytest.raises(ModeLockedError) as excinfo:
        require_live_unlocked(TradingMode.LIVE, gate)
    assert "0 trades" in str(excinfo.value)


def test_gate_check_accepts_the_object_form():
    from gold_scalper.modes import LiveGate, ModeLockedError, require_live_unlocked

    result = LiveGate().evaluate({}, {}, [])
    with pytest.raises(ModeLockedError):
        require_live_unlocked(TradingMode.LIVE, result)


def test_demo_is_never_blocked_by_the_gate():
    """De poort beschermt tegen geldverlies. Op demo is er geen geld te
    verliezen, en hij eist metingen die je zonder handelen nooit krijgt."""
    from gold_scalper.modes import require_live_unlocked

    require_live_unlocked(TradingMode.DEMO, {"unlocked": False, "blocking_reasons": []})
    require_live_unlocked(TradingMode.PAPER, {"unlocked": False, "blocking_reasons": []})


def test_unlocked_gate_lets_live_through():
    from gold_scalper.modes import require_live_unlocked
    require_live_unlocked(TradingMode.LIVE, {"unlocked": True, "blocking_reasons": []})


def test_empty_reasons_still_produce_a_message():
    """De foutmelding mag niet zelf omvallen als de redenlijst leeg is."""
    from gold_scalper.modes import ModeLockedError, require_live_unlocked

    with pytest.raises(ModeLockedError) as excinfo:
        require_live_unlocked(TradingMode.LIVE, {"unlocked": False})
    assert "onbekend" in str(excinfo.value)


def test_coordinator_passes_the_gate_directly():
    """Vangt de terugkeer van de klasse-uit-dict-truc."""
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "coordinator.py").read_text(encoding="utf-8")
    assert 'type("G"' not in source
    assert "require_live_unlocked(self.mode, self.gate)" in source


def test_resume_limit_of_zero_blocks_every_resume():
    """Nul betekent: een noodstop duurt tot morgen. Dat is een verdedigbare
    keuze voor wie zichzelf niet wil kunnen overrulen."""
    rm = fresh(max_resumes_per_day=0)
    rm.halt("dagverlies")
    ok, message = rm.manual_resume(balance=9800.0)
    assert not ok
    assert "morgen" in message
    assert rm.state.state is TradingState.HALTED


def test_higher_resume_limit_is_respected():
    rm = fresh(max_resumes_per_day=5)
    for _ in range(5):
        rm.halt("x")
        assert rm.manual_resume(balance=9800.0)[0]
    rm.halt("x")
    assert not rm.manual_resume(balance=9800.0)[0]


def test_resume_limit_is_configurable_from_the_options():
    """Vangt het geval waarin de instelling er wel is maar nooit aankomt."""
    from pathlib import Path

    pkg = Path(__file__).resolve().parent.parent / "custom_components" / "gold_scalper"
    coordinator = (pkg / "coordinator.py").read_text(encoding="utf-8")
    flow = (pkg / "config_flow.py").read_text(encoding="utf-8")

    assert "max_resumes_per_day=_as_int(" in coordinator, (
        "de instelling bereikt de RiskLimits niet"
    )
    assert "CONF_MAX_RESUMES_PER_DAY" in flow, "het veld staat niet in de opties"


def test_gate_requires_time_consistency():
    """Vijfhonderd trades in dezelfde marktsituatie bewijzen niets over een
    andere. De aantalseis meet hoeveelheid; deze toets meet betekenis."""
    from gold_scalper.modes import LiveGate

    strong = {
        "trades": 800, "verdict": "passed", "net_pnl": 400.0,
        "started_at": "2026-01-01T00:00:00+00:00",
    }
    run = {"config_json": '{"simulated": false, "costs_disabled": false}'}
    daily = [
        {"date": f"2026-0{m}-{d:02d}", "net_pnl": 10.0}
        for m in range(1, 3) for d in range(1, 21)
    ]

    zonder = LiveGate().evaluate(strong, run, daily, None)
    assert not zonder.unlocked
    assert any("consistentie" in r for r in zonder.reasons)

    zwak = LiveGate().evaluate(
        strong, run, daily,
        {"verdict": "geconcentreerd", "explanation": "winst uit één periode"},
    )
    assert not zwak.unlocked
    assert any("geconcentreerd" in r for r in zwak.reasons)

    goed = LiveGate().evaluate(
        strong, run, daily, {"verdict": "houdbaar", "explanation": "ok"}
    )
    assert goed.checks["houdt_stand_over_tijd"] is True


# ---------------- dagreset ----------------

def test_reset_day_clears_the_counters():
    """De teller reset anders alleen om middernacht, en dat is soms te laat:
    je bereikte de limiet door een instelling die je inmiddels hebt
    gecorrigeerd."""
    rm = fresh(max_resumes_per_day=2)
    rm.state.resumes_today = 20
    rm.state.trades_today = 74
    rm.state.consecutive_losses = 5
    rm.halt("dagverlies")

    bericht = rm.reset_day(balance=9500.0)

    assert rm.state.resumes_today == 0
    assert rm.state.trades_today == 0
    assert rm.state.consecutive_losses == 0
    assert rm.state.state is TradingState.RUNNING
    assert rm.state.day_start_balance == 9500.0
    assert "9500" in bericht


def test_reset_day_reports_what_was_wiped():
    """Stil terugzetten zou verhullen hoe ver je vandaag bent gekomen."""
    rm = fresh()
    rm.state.resumes_today = 12
    rm.state.trades_today = 40
    bericht = rm.reset_day(balance=9800.0)
    assert "12 hervattingen" in bericht
    assert "40 trades" in bericht


def test_reset_day_does_not_erase_the_loss():
    """Het verlies blijft in de database; alleen de noodrem begint opnieuw.
    Anders zou een reset je resultaten mooier maken dan ze zijn."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "custom_components"
              / "gold_scalper" / "broker" / "risk.py").read_text(encoding="utf-8")
    body = source.split("def reset_day")[1].split("\n    def ")[0]
    assert "database" in body
    for verboden in ("net_pnl", "delete", "DELETE"):
        assert verboden not in body, f"reset_day raakt {verboden} aan"


def test_reset_day_allows_trading_again():
    rm = fresh(max_daily_loss_pct=2.0)
    rm.can_open(**args(balance=9700.0, equity=9700.0, starting_balance=10000.0))
    assert rm.state.state is TradingState.HALTED

    rm.reset_day(balance=9700.0)
    ok, _ = rm.can_open(
        **args(balance=9700.0, equity=9700.0, starting_balance=10000.0)
    )
    assert ok
