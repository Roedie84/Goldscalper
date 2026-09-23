"""De analyse-engine: draait alle indicatoren en voegt ze samen.

Ontwerpkeuzes die bewust zijn gemaakt en waar je van af kunt wijken:

1. **Regime-afhankelijke weging.** Oscillatoren zoals RSI werken in een
   zijwaartse markt, en zijn schadelijk in een sterke trend (waar 'overbought'
   maandenlang overbought blijft). De ADX bepaalt daarom hoe zwaar
   trendindicatoren versus oscillatoren meetellen.

2. **Confidence is los van score.** Een score van +80 met lage confidence is
   iets anders dan +80 met hoge confidence. De confidence daalt bij onenigheid
   tussen indicatoren, bij te weinig historie, bij een actieve volatility
   squeeze en bij een random-walk regime.

3. **Meerdere tijdsframes.** Een koopsignaal op de 1u-chart dat tegen de
   dagtrend in gaat, is zwakker dan een dat ermee meeloopt. De confluence
   tussen tijdsframes is een aparte factor.

4. **Geen advies.** De engine geeft een *signaal* en een onderbouwing. Wat de
   engine expliciet niet doet is positiegroottes bepalen, orders plaatsen, of
   doen alsof deze score een verwachte-rendementsschatting is. Dat is hij niet.
"""

from __future__ import annotations

import math  # noqa: F401
from dataclasses import dataclass, field

from . import levels, momentum, patterns, statistics, trend, volatility, volume
from .core import safe_div
from .signals import (
    ALL_CATEGORIES,
    CATEGORY_MOMENTUM,
    CATEGORY_PATTERN,
    CATEGORY_STATISTICAL,
    CATEGORY_TREND,
    CATEGORY_VOLATILITY,
    CATEGORY_VOLUME,
    Candles,
    Signal,
)
from .trend import adx

# Signaalstaten, oplopend van bearish naar bullish.
STATE_STRONG_SELL = "strong_sell"
STATE_SELL = "sell"
STATE_NEUTRAL = "neutral"
STATE_BUY = "buy"
STATE_STRONG_BUY = "strong_buy"

ALL_STATES = (STATE_STRONG_SELL, STATE_SELL, STATE_NEUTRAL, STATE_BUY, STATE_STRONG_BUY)

MIN_CANDLES = 60
RECOMMENDED_CANDLES = 250


@dataclass(slots=True)
class AnalysisResult:
    score: float
    state: str
    confidence: float
    regime: str
    category_scores: dict[str, float]
    signals: list[Signal]
    levels: dict
    warnings: list[str] = field(default_factory=list)
    timeframe: str = ""

    @property
    def bullish_count(self) -> int:
        return sum(1 for s in self.signals if s.weight > 0 and s.score > 0.15)

    @property
    def bearish_count(self) -> int:
        return sum(1 for s in self.signals if s.weight > 0 and s.score < -0.15)

    @property
    def neutral_count(self) -> int:
        return sum(1 for s in self.signals if s.weight > 0 and abs(s.score) <= 0.15)

    def top_drivers(self, count: int = 5) -> list[dict]:
        """De indicatoren die de score het sterkst duwen, beide kanten op."""
        weighted = [s for s in self.signals if s.weight > 0]
        weighted.sort(key=lambda s: abs(s.score * s.weight), reverse=True)
        return [s.as_dict() for s in weighted[:count]]

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 2),
            "state": self.state,
            "confidence": round(self.confidence, 3),
            "regime": self.regime,
            "timeframe": self.timeframe,
            "category_scores": {k: round(v, 2) for k, v in self.category_scores.items()},
            "bullish": self.bullish_count,
            "bearish": self.bearish_count,
            "neutral": self.neutral_count,
            "warnings": self.warnings,
            "levels": self.levels,
            "signals": [s.as_dict() for s in self.signals],
        }


def detect_regime(candles: Candles) -> tuple[str, float]:
    """Bepaal het marktregime en de trendsterkte (0-1) op basis van ADX."""
    adx_line, _, _ = adx(candles, 14)
    value = adx_line[-1] if adx_line and adx_line[-1] is not None else None
    if value is None:
        return "unknown", 0.5
    if value >= 40:
        return "strong_trend", 1.0
    if value >= 25:
        return "trending", 0.5 + (value - 25) / 30.0
    if value >= 20:
        return "weak_trend", 0.4
    return "ranging", max(0.0, value / 50.0)


def _regime_multiplier(category: str, regime: str) -> float:
    """Hoe zwaar telt een categorie mee in het huidige regime.

    Dit is de belangrijkste knop in het hele systeem. In een sterke trend
    worden oscillatoren gehalveerd, omdat 'RSI 78, dus verkopen' precies de
    fout is die mensen in bullruns arm maakt.
    """
    table = {
        "strong_trend": {
            CATEGORY_TREND: 1.5,
            CATEGORY_MOMENTUM: 0.5,
            CATEGORY_VOLATILITY: 0.6,
            CATEGORY_VOLUME: 1.0,
            CATEGORY_PATTERN: 0.7,
            CATEGORY_STATISTICAL: 0.7,
        },
        "trending": {
            CATEGORY_TREND: 1.3,
            CATEGORY_MOMENTUM: 0.8,
            CATEGORY_VOLATILITY: 0.8,
            CATEGORY_VOLUME: 1.0,
            CATEGORY_PATTERN: 0.9,
            CATEGORY_STATISTICAL: 0.9,
        },
        "weak_trend": {
            CATEGORY_TREND: 1.0,
            CATEGORY_MOMENTUM: 1.0,
            CATEGORY_VOLATILITY: 1.0,
            CATEGORY_VOLUME: 1.0,
            CATEGORY_PATTERN: 1.0,
            CATEGORY_STATISTICAL: 1.0,
        },
        "ranging": {
            CATEGORY_TREND: 0.6,
            CATEGORY_MOMENTUM: 1.4,
            CATEGORY_VOLATILITY: 1.3,
            CATEGORY_VOLUME: 0.9,
            CATEGORY_PATTERN: 1.1,
            CATEGORY_STATISTICAL: 1.2,
        },
    }
    return table.get(regime, table["weak_trend"]).get(category, 1.0)


def score_to_state(score: float, thresholds: dict | None = None) -> str:
    t = thresholds or {}
    strong = t.get("strong", 45.0)
    weak = t.get("weak", 18.0)
    if score >= strong:
        return STATE_STRONG_BUY
    if score >= weak:
        return STATE_BUY
    if score <= -strong:
        return STATE_STRONG_SELL
    if score <= -weak:
        return STATE_SELL
    return STATE_NEUTRAL


def _confidence(
    signals: list[Signal], regime: str, candle_count: int, warnings: list[str]
) -> float:
    """Bereken hoeveel vertrouwen deze uitkomst verdient.

    Begint op 1.0 en gaat omlaag voor elke reden tot twijfel. Er is bewust
    geen mechanisme dat de confidence weer omhoog duwt: overtuiging moet je
    verdienen door het ontbreken van problemen, niet door een bonus.
    """
    weighted = [s for s in signals if s.weight > 0]
    if not weighted:
        return 0.0

    confidence = 1.0

    # Onenigheid tussen indicatoren, gemeten als gewogen richtingsovereenstemming.
    #
    # Eerdere versie gebruikte de standaarddeviatie van de scores. Dat werkte
    # niet: met vijftig indicatoren is de spreiding altijd fors, dus kwam élke
    # uitkomst als 'onzeker' uit de bus en zei de meter niets meer. Wat telt is
    # niet of indicatoren dezelfde *waarde* hebben, maar of ze dezelfde *kant*
    # op wijzen.
    net = sum(s.score * s.weight for s in weighted)
    gross = sum(abs(s.score) * s.weight for s in weighted)
    agreement = safe_div(abs(net), gross)  # 0 = volledig verdeeld, 1 = unaniem
    confidence *= 0.3 + 0.7 * agreement

    # Te weinig historie.
    if candle_count < RECOMMENDED_CANDLES:
        ratio = candle_count / RECOMMENDED_CANDLES
        confidence *= 0.55 + 0.45 * ratio
        if candle_count < 120:
            warnings.append(
                f"Slechts {candle_count} candles beschikbaar; langetermijnindicatoren "
                "zoals EMA200 en Ichimoku zijn onbetrouwbaar of ontbreken"
            )

    # Actieve squeeze: uitbraak komt, richting onbekend.
    squeeze_signal = next((s for s in signals if s.key == "squeeze"), None)
    if squeeze_signal and squeeze_signal.extra.get("active"):
        confidence *= 0.7
        warnings.append(
            "Volatility squeeze actief: de richting van de uitbraak is nog niet bepaald"
        )

    # Random walk volgens Hurst.
    hurst_signal = next((s for s in signals if s.key == "hurst"), None)
    if hurst_signal and isinstance(hurst_signal.value, float):
        if 0.45 <= hurst_signal.value <= 0.55:
            confidence *= 0.8

    # Extreme volatiliteit maakt elk signaal fragiel.
    atr_signal = next((s for s in signals if s.key == "atr"), None)
    if atr_signal:
        atr_pct = atr_signal.extra.get("atr_pct")
        if isinstance(atr_pct, (int, float)) and atr_pct > 6.0:
            confidence *= 0.75
            warnings.append(
                f"Zeer hoge volatiliteit ({atr_pct:.1f}% per candle); signalen "
                "verouderen snel"
            )

    return max(0.0, min(1.0, confidence))


def analyse(candles: Candles, cfg: dict | None = None, timeframe: str = "") -> AnalysisResult:
    """Draai de volledige analyse op één tijdsframe."""
    cfg = cfg or {}
    warnings: list[str] = []

    if len(candles) < MIN_CANDLES:
        raise ValueError(
            f"Minimaal {MIN_CANDLES} candles nodig voor een zinnige analyse, "
            f"maar er zijn er {len(candles)}"
        )

    signals: list[Signal] = []
    for module in (trend, momentum, volatility, volume, patterns, levels, statistics):
        try:
            signals.extend(module.evaluate(candles, cfg))
        except Exception as err:  # noqa: BLE001 - één kapotte indicator mag de rest niet slopen
            warnings.append(f"Indicatorgroep {module.__name__.split('.')[-1]} faalde: {err}")

    regime, _strength = detect_regime(candles)

    # Weeg per categorie en tel op.
    category_totals: dict[str, list[tuple[float, float]]] = {c: [] for c in ALL_CATEGORIES}
    total_weighted = 0.0
    total_weight = 0.0
    for signal in signals:
        if signal.weight <= 0:
            continue
        multiplier = _regime_multiplier(signal.category, regime)
        effective_weight = signal.weight * multiplier
        category_totals.setdefault(signal.category, []).append(
            (signal.score, effective_weight)
        )
        total_weighted += signal.score * effective_weight
        total_weight += effective_weight

    raw_score = safe_div(total_weighted, total_weight)
    category_scores = {
        cat: safe_div(
            sum(s * w for s, w in items), sum(w for _s, w in items)
        ) * 100.0
        for cat, items in category_totals.items()
        if items
    }

    confidence = _confidence(signals, regime, len(candles), warnings)
    score = raw_score * 100.0

    return AnalysisResult(
        score=score,
        state=score_to_state(score, cfg.get("thresholds")),
        confidence=confidence,
        regime=regime,
        category_scores=category_scores,
        signals=signals,
        levels=levels.nearest_levels(candles),
        warnings=warnings,
        timeframe=timeframe,
    )


def combine_timeframes(
    results: dict[str, AnalysisResult], weights: dict[str, float] | None = None
) -> dict:
    """Voeg de analyses van meerdere tijdsframes samen.

    Het hoogste tijdsframe krijgt standaard het zwaarste gewicht: een
    dagtrend overrulet doorgaans een uursignaal. De confluence-factor
    (0-1) zegt hoe eens de tijdsframes het met elkaar zijn, en drukt de
    uiteindelijke confidence als ze elkaar tegenspreken.
    """
    if not results:
        return {}
    default_weights = {"1h": 0.8, "4h": 1.0, "1d": 1.4, "1w": 1.2}
    weights = weights or default_weights

    total_w = 0.0
    total_s = 0.0
    for tf, result in results.items():
        w = weights.get(tf, 1.0) * max(0.2, result.confidence)
        total_s += result.score * w
        total_w += w
    combined_score = safe_div(total_s, total_w)

    scores = [r.score for r in results.values()]
    if len(scores) > 1:
        signs = [1 if s > 10 else -1 if s < -10 else 0 for s in scores]
        agreeing = abs(sum(signs)) / len(signs)
        spread = max(scores) - min(scores)
        confluence = max(0.0, min(1.0, agreeing * (1.0 - min(1.0, spread / 160.0))))
    else:
        confluence = 1.0

    mean_conf = sum(r.confidence for r in results.values()) / len(results)
    final_conf = mean_conf * (0.55 + 0.45 * confluence)

    conflicts = [
        f"{a} zegt {results[a].state} terwijl {b} {results[b].state} zegt"
        for i, a in enumerate(results)
        for b in list(results)[i + 1 :]
        if (results[a].score > 15 and results[b].score < -15)
        or (results[a].score < -15 and results[b].score > 15)
    ]

    return {
        "score": round(combined_score, 2),
        "state": score_to_state(combined_score),
        "confidence": round(final_conf, 3),
        "confluence": round(confluence, 3),
        "conflicts": conflicts,
        "per_timeframe": {
            tf: {
                "score": round(r.score, 2),
                "state": r.state,
                "confidence": round(r.confidence, 3),
                "regime": r.regime,
            }
            for tf, r in results.items()
        },
    }
