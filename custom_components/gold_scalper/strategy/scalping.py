"""Scalpingstrategie voor XAU/USD op M1.

De 48-indicatorenbrij uit de crypto-versie is hier bewust níet gebruikt. Op een
tijdsframe van een minuut zijn EMA200, Ichimoku en Hurst-exponenten
betekenisloos ruis: ze zijn ontworpen voor swings van dagen. Wat op deze
schaal wél informatie draagt is een klein aantal dingen — richting van de
korte trend, mate van uitrekking ten opzichte van het gemiddelde, en vooral de
actuele spread.

De belangrijkste component van deze strategie is geen indicator maar een
kostenpoort. Elk signaal wordt getoetst aan de vraag: is de verwachte beweging
groter dan wat deze trade aan spread, commissie en slippage gaat kosten? Zo
niet, dan wordt er niet gehandeld, hoe overtuigend het signaal er ook uitziet.
Bij goud filtert die poort in de praktijk het overgrote deel van de signalen
weg, en dat is de bedoeling.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..analysis.core import ema, linreg_slope, rma, safe_div, stdev, true_range
from ..analysis.momentum import rsi
from ..analysis.signals import Candles
from ..analysis.volatility import atr, bollinger

_LOGGER = logging.getLogger(__name__)

STRATEGY_VERSION = "scalp-0.2.0"

CONTRACT_SIZE = 100.0


@dataclass(slots=True)
class ScalpConfig:
    """Instellingen. De defaults zijn conservatief, niet geoptimaliseerd.

    Er is bewust geen parameteroptimalisatie ingebouwd. Op een dataset van een
    paar duizend trades vindt een optimizer altijd een combinatie die er
    prachtig uitziet en die volledig overfit is. Als je wilt afstellen, doe dat
    op basis van de bewijsfase-resultaten en met een aparte validatieperiode.
    """

    #: Handel alleen als de spread onder deze waarde ligt (USD per ounce).
    max_spread: float = 0.30
    #: Verwachte beweging moet dit veelvoud van de kosten zijn.
    min_edge_multiple: float = 2.0
    #: Doelwinst en stop als veelvoud van de ATR.
    take_profit_atr: float = 1.5
    stop_loss_atr: float = 1.0
    #: Maximale positieduur in seconden; scalps die blijven hangen zijn verliezers.
    max_hold_seconds: int = 300
    #: Positiegrootte in lots.
    volume: float = 0.01
    #: Maximaal aantal gelijktijdige posities.
    max_positions: int = 1
    #: Minimale afstand tussen twee entries, in seconden.
    cooldown_seconds: int = 60
    #: Handelsvenster in UTC-uren. Buiten de Londen/New York-overlap is de
    #: spread breder en de beweging kleiner: de slechtste combinatie.
    trading_hours_utc: tuple[int, int] = (7, 20)
    #: Drempel voor de samengestelde scalpscore.
    entry_threshold: float = 0.45
    #: Commissie per lot per zijde; moet matchen met je BrokerCosts.
    commission_per_lot_per_side: float = 3.50
    #: Geschatte slippage per zijde in USD per ounce.
    expected_slippage: float = 0.02


@dataclass(slots=True)
class ScalpSignal:
    """Uitkomst van één evaluatie."""

    direction: int  # 1 = long, -1 = short, 0 = geen actie
    score: float
    confidence: float
    should_trade: bool
    reject_reason: str | None
    reason: str
    stop_loss: float | None = None
    take_profit: float | None = None
    expected_move: float = 0.0
    expected_cost: float = 0.0
    components: dict[str, float] = field(default_factory=dict)


def _micro_trend(close: list[float]) -> tuple[float, str]:
    """Richting van de korte trend via EMA(9) tegen EMA(21) plus helling."""
    fast, slow = ema(close, 9), ema(close, 21)
    if fast[-1] is None or slow[-1] is None:
        return 0.0, "onvoldoende data voor microtrend"
    gap_pct = safe_div(fast[-1] - slow[-1], slow[-1]) * 100.0
    slope, r2 = linreg_slope(close, 15)
    slope_component = 0.0 if slope is None else max(-1.0, min(1.0, slope * 8.0)) * (r2 or 0)
    gap_component = max(-1.0, min(1.0, gap_pct * 60.0))
    score = 0.6 * gap_component + 0.4 * slope_component
    return score, f"EMA9/21 gap {gap_pct:+.4f}%, helling {slope or 0:+.4f}%/candle"


def _stretch(close: list[float]) -> tuple[float, str]:
    """Mean reversion: hoe ver staat de koers van de korte VWAP-proxy af.

    Op M1 is goud grotendeels mean-reverting rond het 20-periode gemiddelde,
    behalve tijdens nieuws. Dit component is dus contrair aan uitrekking.
    """
    upper, middle, lower, pct_b, _ = bollinger(close, 20, 2.0)
    if pct_b[-1] is None:
        return 0.0, "onvoldoende data voor stretch"
    b = pct_b[-1]
    score = max(-1.0, min(1.0, (0.5 - b) * 2.4))
    return score, f"%B {b:.2f}"


def _momentum(close: list[float]) -> tuple[float, str]:
    r = rsi(close, 7)
    if r[-1] is None:
        return 0.0, "onvoldoende data voor RSI"
    # RSI(7) op M1: extremen zijn hier veel gewoner dan op hogere tijdsframes,
    # dus de drempels liggen ruimer dan de klassieke 30/70.
    value = r[-1]
    if value <= 20:
        score = 0.8
    elif value >= 80:
        score = -0.8
    else:
        score = (50.0 - value) / 40.0
    return max(-1.0, min(1.0, score)), f"RSI(7) {value:.1f}"


def _volatility_regime(candles: Candles) -> tuple[float, str]:
    """Is er genoeg beweging om iets te verdienen, en niet zoveel dat het chaos is."""
    a = atr(candles, 14)
    if a[-1] is None:
        return 0.0, "geen ATR"
    recent = [v for v in a[-60:] if v is not None]
    if not recent:
        return 0.0, "geen ATR-historie"
    median = sorted(recent)[len(recent) // 2]
    ratio = safe_div(a[-1], median, 1.0)
    if ratio < 0.6:
        return -0.5, f"volatiliteit {ratio:.2f}× mediaan: te stil om de spread terug te verdienen"
    if ratio > 2.5:
        return -0.8, f"volatiliteit {ratio:.2f}× mediaan: waarschijnlijk nieuws, spread onbetrouwbaar"
    return 0.3, f"volatiliteit {ratio:.2f}× mediaan: bruikbaar"


def evaluate(
    candles: Candles,
    bid: float,
    ask: float,
    cfg: ScalpConfig,
    hour_utc: int,
    open_position_count: int = 0,
    seconds_since_last_entry: float = 1e9,
) -> ScalpSignal:
    """Beoordeel of er nu een scalp te maken is.

    De volgorde is bewust: eerst de harde poorten (spread, tijd, cooldown),
    dan pas de indicatoren. Een prachtig signaal bij een spread van 0,80 is
    geen kans maar een val.
    """
    spread = ask - bid
    mid = (bid + ask) / 2.0
    components: dict[str, float] = {}

    def reject(reason: str, text: str) -> ScalpSignal:
        return ScalpSignal(
            direction=0, score=0.0, confidence=0.0, should_trade=False,
            reject_reason=reason, reason=text, components=components,
        )

    if len(candles) < 60:
        return reject("insufficient_data", f"Slechts {len(candles)} candles beschikbaar")

    if spread > cfg.max_spread:
        return reject(
            "spread_too_wide",
            f"Spread {spread:.3f} boven de limiet {cfg.max_spread:.3f}",
        )

    start, end = cfg.trading_hours_utc
    if not (start <= hour_utc < end):
        return reject(
            "outside_hours",
            f"Uur {hour_utc}:00 UTC valt buiten het venster {start}:00-{end}:00",
        )

    if open_position_count >= cfg.max_positions:
        return reject("max_positions", f"Al {open_position_count} positie(s) open")

    if seconds_since_last_entry < cfg.cooldown_seconds:
        return reject(
            "cooldown",
            f"Nog {cfg.cooldown_seconds - seconds_since_last_entry:.0f}s cooldown",
        )

    # --- Indicatorcomponenten ------------------------------------------------
    close = candles.close
    trend_score, trend_note = _micro_trend(close)
    stretch_score, stretch_note = _stretch(close)
    mom_score, mom_note = _momentum(close)
    vol_score, vol_note = _volatility_regime(candles)

    components.update({
        "trend": round(trend_score, 3),
        "stretch": round(stretch_score, 3),
        "momentum": round(mom_score, 3),
        "volatility": round(vol_score, 3),
    })

    if vol_score < 0:
        return reject("volatility_regime", vol_note)

    # Trend en mean reversion spreken elkaar per definitie tegen. Dat is geen
    # bug: als ze het oneens zijn is er geen heldere kans, en dan zakt de score
    # vanzelf onder de drempel.
    score = 0.40 * trend_score + 0.35 * stretch_score + 0.25 * mom_score
    direction = 1 if score > 0 else -1

    agreement = 1.0 - (
        abs(trend_score - stretch_score) + abs(trend_score - mom_score)
    ) / 4.0
    confidence = max(0.0, min(1.0, agreement))

    if abs(score) < cfg.entry_threshold:
        return reject(
            "score_below_threshold",
            f"Score {score:+.3f} onder drempel {cfg.entry_threshold:.2f}",
        )

    # --- De kostenpoort ------------------------------------------------------
    a = atr(candles, 14)
    atr_value = a[-1] or 0.0
    if atr_value <= 0:
        return reject("no_atr", "ATR is nul; kan geen doelen bepalen")

    expected_move = atr_value * cfg.take_profit_atr
    # Kosten per ounce: spread (één keer per round trip) + slippage (beide
    # zijden) + commissie omgerekend naar per ounce.
    commission_per_oz = (cfg.commission_per_lot_per_side * 2) / CONTRACT_SIZE
    expected_cost = spread + (cfg.expected_slippage * 2) + commission_per_oz

    if expected_move < expected_cost * cfg.min_edge_multiple:
        return ScalpSignal(
            direction=0,
            score=score,
            confidence=confidence,
            should_trade=False,
            reject_reason="edge_below_cost",
            reason=(
                f"Verwachte beweging {expected_move:.3f} USD/oz haalt de drempel niet: "
                f"kosten zijn {expected_cost:.3f} en er is minimaal "
                f"{expected_cost * cfg.min_edge_multiple:.3f} nodig"
            ),
            expected_move=expected_move,
            expected_cost=expected_cost,
            components=components,
        )

    if direction == 1:
        entry = ask
        stop = entry - atr_value * cfg.stop_loss_atr
        target = entry + expected_move
    else:
        entry = bid
        stop = entry + atr_value * cfg.stop_loss_atr
        target = entry - expected_move

    return ScalpSignal(
        direction=direction,
        score=score,
        confidence=confidence,
        should_trade=True,
        reject_reason=None,
        reason=(
            f"{'Long' if direction == 1 else 'Short'} bij score {score:+.3f}. "
            f"{trend_note}; {stretch_note}; {mom_note}; {vol_note}. "
            f"Doel {expected_move:.3f} USD/oz tegen kosten {expected_cost:.3f}"
        ),
        stop_loss=round(stop, 3),
        take_profit=round(target, 3),
        expected_move=expected_move,
        expected_cost=expected_cost,
        components=components,
    )
