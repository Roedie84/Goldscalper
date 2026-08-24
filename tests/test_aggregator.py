"""Candles bouwen uit live koersen.

Bestaat omdat brokers historische koersen per datapunt afrekenen. IG's
demo-quotum was binnen een dag op, en dan kan de analyse niet meer starten.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.strategy.aggregator import QuoteAggregator, sampling_correction

T0 = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _feed(agg, prices, start=T0, step=20):
    closed = 0
    for i, price in enumerate(prices):
        if agg.add(price, start + timedelta(seconds=i * step)):
            closed += 1
    return closed


def test_bars_close_on_the_boundary():
    agg = QuoteAggregator("5m")
    # 15 monsters van 20s = 5 minuten, dan één erover
    assert _feed(agg, [3300.0] * 16) == 1
    assert agg.bar_count == 1


def test_ohlc_is_correct():
    agg = QuoteAggregator("5m")
    _feed(agg, [3300.0, 3302.0, 3298.0, 3301.0] + [3301.0] * 12)
    candles = agg.candles()
    assert candles.open[0] == 3300.0
    assert candles.high[0] == 3302.0
    assert candles.low[0] == 3298.0
    candles.validate()


def test_current_bar_is_excluded():
    """Handelen op een onvoltooide bar maakt papier en live onvergelijkbaar."""
    agg = QuoteAggregator("5m")
    _feed(agg, [3300.0] * 20)          # één afgesloten bar, één in aanbouw
    assert len(agg.candles()) == 1


def test_no_bars_yet_raises():
    with pytest.raises(ValueError, match="Nog geen"):
        QuoteAggregator("5m").candles()


def test_backwards_clock_is_ignored():
    """Een NTP-correctie mag de reeks niet bederven."""
    agg = QuoteAggregator("5m")
    agg.add(3300.0, T0)
    agg.add(3350.0, T0 - timedelta(hours=1))
    assert agg.candles if agg.bar_count else True
    agg.add(3301.0, T0 + timedelta(seconds=20))
    _feed(agg, [3301.0] * 20, start=T0 + timedelta(seconds=40))
    candles = agg.candles()
    assert max(candles.high) < 3350.0


def test_old_bars_are_trimmed():
    agg = QuoteAggregator("5m", max_bars=5)
    _feed(agg, [3300.0] * 200)
    assert agg.bar_count <= 5


# ---------------- nauwkeurigheid ----------------

def test_sampling_correction_compensates_understated_range():
    """Periodiek bemonsteren mist de uitersten; bij 20s is dat ~5%."""
    assert sampling_correction(15) == pytest.approx(1.05, abs=0.02)
    assert sampling_correction(5) > sampling_correction(30)
    assert sampling_correction(120) == 1.0


def test_correction_follows_the_actual_sample_rate():
    agg = QuoteAggregator("5m")
    _feed(agg, [3300.0] * 40, step=20)     # 15 monsters per bar
    assert 1.0 < agg.correction < 1.15


def test_progress_reports_time_remaining():
    agg = QuoteAggregator("5m")
    _feed(agg, [3300.0] * 32)              # 2 bars
    progress = agg.progress(60)
    assert progress["bars"] == 2
    assert progress["remaining"] == 58
    assert progress["eta_minutes"] == 58 * 5
    assert progress["ready"] is False


def test_progress_reports_ready():
    agg = QuoteAggregator("5m")
    _feed(agg, [3300.0] * 32)
    assert agg.progress(2)["ready"] is True


# ---------------- opslag ----------------

def test_bars_survive_a_restart():
    """Zonder dit kost elke update opnieuw uren opwarmen."""
    agg = QuoteAggregator("5m")
    _feed(agg, [3300.0 + i * 0.1 for i in range(50)])
    restored = QuoteAggregator.from_dict(agg.to_dict(), "5m")
    assert restored.bar_count == agg.bar_count
    assert restored.candles().close == agg.candles().close


def test_timeframe_change_discards_old_bars():
    """M1-bars zijn geen M5-bars; mengen zou onzin opleveren."""
    agg = QuoteAggregator("1m")
    _feed(agg, [3300.0] * 50, step=20)
    restored = QuoteAggregator.from_dict(agg.to_dict(), "5m")
    assert restored.bar_count == 0


def test_corrupt_rows_are_skipped():
    data = {"timeframe": "5m", "bars": [
        [1, 2.0, 3.0, 1.0, 2.5, 5.0],
        ["kapot"],
        [2, 2.0, 3.0, 1.0, 2.5, 5.0],
    ]}
    assert QuoteAggregator.from_dict(data, "5m").bar_count == 2


def test_correction_matches_a_measured_comparison():
    """Uitgelijnd op dezelfde tijdstempels vergelijken, niet op dezelfde duur.

    Een eerdere meting vergeleek twee verschillende tijdvensters en leek 19%
    afwijking te tonen; dat was marktverschil, geen bemonsteringsfout.
    """
    import asyncio
    import statistics
    from datetime import datetime, timezone

    from gold_scalper.broker.simulator import SimulatorVenue

    venue = SimulatorVenue(seed=20260823)
    real = asyncio.run(venue.candles("XAU_USD", "5m", 120))

    agg = QuoteAggregator("5m")
    moment = real.timestamp[0]
    while moment < real.timestamp[-1] + 300:
        agg.add(venue.price_at(moment),
                datetime.fromtimestamp(moment, timezone.utc))
        moment += 20
    built = agg.candles()

    shared = set(built.timestamp) & set(real.timestamp)
    built_ranges = [
        built.high[i] - built.low[i]
        for i, ts in enumerate(built.timestamp) if ts in shared
    ]
    real_ranges = [
        real.high[i] - real.low[i]
        for i, ts in enumerate(real.timestamp) if ts in shared
    ]
    needed = statistics.median(real_ranges) / statistics.median(built_ranges)

    assert abs(agg.correction - needed) < 0.03, (
        f"correctie {agg.correction:.3f} wijkt af van gemeten {needed:.3f}"
    )
