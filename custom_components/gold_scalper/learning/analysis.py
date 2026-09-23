"""Leren van de eigen historie, met een scherpe scheidslijn.

Er zijn twee soorten "leren" en ze verschillen fundamenteel in hoe gevaarlijk
ze zijn.

**Meten wat er werkelijk gebeurde.** Hoeveel slippage kreeg je echt? Hoe breed
was de spread per uur? Hoe vaak werd je stop geraakt vergeleken met wat je op
grond van de ATR zou verwachten? Dat zijn *waarnemingen*, geen voorspellingen.
Ze zeggen niets over wat de markt gaat doen, alleen over hoe jouw uitvoering
zich gedraagt. Zulke metingen mogen automatisch worden toegepast: ze vervangen
een aanname door een feit, en dat kan alleen maar beter worden.

**Parameters bijstellen op je eigen resultaten.** Welke instapdrempel had
achteraf de meeste winst opgeleverd? Dat is *optimalisatie*, en op een paar
honderd trades met acht knoppen vind je gegarandeerd instellingen die er
prachtig uitzien en vooruit falen. Erger nog is de terugkoppeling: een slechte
week leidt tot aanpassing, die aanpassing past bij de ruis van die week, de
volgende week is anders, weer aanpassen. Zo jaagt een bot zijn eigen staart na
en wordt hij niet beter maar instabieler.

Deze module doet daarom het eerste automatisch en het tweede nooit zonder jou.
Parameterwijzigingen worden *voorgesteld*, met walk-forward validatie op data
die bij het zoeken niet is gebruikt, en met een expliciete uitspraak of het
verschil statistisch iets voorstelt. Aannemen doe jij.

En er wordt bijgehouden of eerdere voorstellen achteraf klopten. Een lerend
systeem dat niet meet of zijn eigen lessen deugden, leert niets.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from ..storage.database import Trade

_LOGGER = logging.getLogger(__name__)

#: Minimaal aantal waarnemingen voordat een gemeten grootheid wordt toegepast.
#: Onder dit aantal is het gemiddelde vooral ruis.
MIN_OBSERVATIONS = 30

#: Minimaal aantal trades per helft bij walk-forward validatie.
MIN_PER_FOLD = 50


# --------------------------------------------------------------------------- #
# Deel 1: meten. Wordt automatisch toegepast.
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ExecutionFacts:
    """Wat de uitvoering werkelijk deed, tegenover wat er werd aangenomen."""

    trades: int = 0
    measured_slippage: float | None = None
    assumed_slippage: float | None = None
    slippage_ratio: float | None = None
    spread_by_hour: dict[int, float] = field(default_factory=dict)
    stop_hit_rate: float | None = None
    target_hit_rate: float | None = None
    timeout_rate: float | None = None
    #: Hoe vaak de trade het doel haalde tegenover de stop. Wijkt dit sterk af
    #: van wat de doel/stop-verhouding voorspelt, dan klopt de ATR-schatting
    #: niet of staan de niveaus verkeerd.
    expected_target_rate: float | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "trades": self.trades,
            "measured_slippage": self.measured_slippage,
            "assumed_slippage": self.assumed_slippage,
            "slippage_ratio": self.slippage_ratio,
            "spread_by_hour": self.spread_by_hour,
            "stop_hit_rate": self.stop_hit_rate,
            "target_hit_rate": self.target_hit_rate,
            "timeout_rate": self.timeout_rate,
            "expected_target_rate": self.expected_target_rate,
            "notes": self.notes,
        }


def measure_execution(
    trades: Sequence[Trade], assumed_slippage: float = 0.02
) -> ExecutionFacts:
    """Meet hoe de uitvoering zich werkelijk gedroeg.

    Puur beschrijvend: geen enkele uitspraak over wat de markt gaat doen.
    """
    closed = [t for t in trades if t.close_time]
    facts = ExecutionFacts(trades=len(closed))
    if len(closed) < MIN_OBSERVATIONS:
        facts.notes.append(
            f"{len(closed)} trades; minimaal {MIN_OBSERVATIONS} nodig voordat "
            "gemeten waarden betekenis krijgen"
        )
        return facts

    slippages = [
        t.open_slippage for t in closed if t.open_slippage is not None
    ] + [
        t.close_slippage for t in closed if t.close_slippage
    ]
    if slippages:
        facts.measured_slippage = round(statistics.median(slippages), 5)
        facts.assumed_slippage = assumed_slippage
        if assumed_slippage > 0:
            facts.slippage_ratio = round(facts.measured_slippage / assumed_slippage, 3)
            if facts.slippage_ratio > 1.5:
                facts.notes.append(
                    f"Werkelijke slippage is {facts.slippage_ratio:.1f}x de aanname. "
                    "Eerdere papercijfers waren te optimistisch."
                )
            elif facts.slippage_ratio < 0.6:
                facts.notes.append(
                    f"Werkelijke slippage is {facts.slippage_ratio:.1f}x de aanname; "
                    "de kostenpoort staat strenger dan nodig."
                )

    by_hour: dict[int, list[float]] = defaultdict(list)
    for trade in closed:
        if trade.open_spread is None:
            continue
        try:
            hour = datetime.fromisoformat(trade.open_time).astimezone(timezone.utc).hour
        except (TypeError, ValueError):
            continue
        by_hour[hour].append(trade.open_spread)
    facts.spread_by_hour = {
        hour: round(statistics.median(values), 4)
        for hour, values in sorted(by_hour.items())
        if len(values) >= 5
    }

    reasons = [t.close_reason for t in closed if t.close_reason]
    if reasons:
        total = len(reasons)
        facts.stop_hit_rate = round(reasons.count("stop_loss") / total, 3)
        facts.target_hit_rate = round(reasons.count("take_profit") / total, 3)
        facts.timeout_rate = round(
            sum(1 for r in reasons if r in ("timeout", "drain_timeout")) / total, 3
        )

        # Bij een doel van 1,5xATR en een stop van 1,0xATR verwacht je bij een
        # richtingloze markt ongeveer 1/(1+1,5) = 40% doeltreffers. Wijkt de
        # praktijk daar sterk van af, dan klopt de ATR-schatting niet.
        facts.expected_target_rate = 0.40
        if facts.target_hit_rate is not None:
            afwijking = facts.target_hit_rate - facts.expected_target_rate
            if abs(afwijking) > 0.15:
                facts.notes.append(
                    f"Doel geraakt in {facts.target_hit_rate:.0%} van de gevallen "
                    f"tegen {facts.expected_target_rate:.0%} verwacht. "
                    + (
                        "De ATR wordt mogelijk onderschat, waardoor doelen te dicht "
                        "liggen." if afwijking > 0 else
                        "De ATR wordt mogelijk overschat, waardoor doelen te ver liggen."
                    )
                )

    return facts


# --------------------------------------------------------------------------- #
# Deel 2: voorstellen. Wordt nooit automatisch toegepast.
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Proposal:
    """Een voorgestelde parameterwijziging, met onderbouwing én tegenwerpingen."""

    parameter: str
    current: float
    suggested: float
    in_sample_gain: float
    out_of_sample_gain: float
    t_statistic: float
    trades_evaluated: int
    accept: bool
    reasoning: str

    def as_dict(self) -> dict:
        return {
            "parameter": self.parameter,
            "current": self.current,
            "suggested": self.suggested,
            "in_sample_gain": round(self.in_sample_gain, 2),
            "out_of_sample_gain": round(self.out_of_sample_gain, 2),
            "t_statistic": round(self.t_statistic, 2),
            "trades_evaluated": self.trades_evaluated,
            "recommended": self.accept,
            "reasoning": self.reasoning,
        }


def _net_total(trades: Sequence[Trade]) -> float:
    return sum(t.net_pnl or 0.0 for t in trades)


def _t_statistic(trades: Sequence[Trade]) -> float:
    values = [t.net_pnl for t in trades if t.net_pnl is not None]
    if len(values) < 2:
        return 0.0
    mean = statistics.mean(values)
    sd = statistics.stdev(values)
    if sd == 0:
        return 0.0
    return mean / (sd / math.sqrt(len(values)))


def evaluate_threshold(
    trades: Sequence[Trade], current: float, candidates: Sequence[float]
) -> Proposal | None:
    """Zou een andere instapdrempel beter zijn geweest?

    Met walk-forward validatie: de beste kandidaat wordt gezocht op de eerste
    helft van de trades en beoordeeld op de tweede. Alleen zoeken op alle data
    levert gegarandeerd een winnaar op die achteraf mooi oogt en vooruit faalt.

    De uitkomst is bewust een *voorstel*. Een bot die zijn eigen drempel
    bijstelt na een slechte week, past zich aan de ruis van die week aan.
    """
    scored = [
        t for t in trades
        if t.close_time and t.net_pnl is not None and t.signal_score is not None
    ]
    scored.sort(key=lambda t: t.close_time or "")

    if len(scored) < MIN_PER_FOLD * 2:
        return None

    half = len(scored) // 2
    train, test = scored[:half], scored[half:]

    def keep(subset, threshold):
        return [t for t in subset if abs(t.signal_score or 0) >= threshold]

    baseline_train = _net_total(keep(train, current))
    best_threshold, best_gain = current, 0.0
    for candidate in candidates:
        gain = _net_total(keep(train, candidate)) - baseline_train
        if gain > best_gain:
            best_threshold, best_gain = candidate, gain

    if best_threshold == current:
        return None

    baseline_test = _net_total(keep(test, current))
    out_of_sample = _net_total(keep(test, best_threshold)) - baseline_test
    subset = keep(test, best_threshold)
    t_stat = _t_statistic(subset)

    # Streng: de verbetering moet ook bestaan op data die bij het zoeken niet
    # is gebruikt, én statistisch iets voorstellen.
    accept = out_of_sample > 0 and t_stat >= 2.0 and len(subset) >= MIN_PER_FOLD

    if not accept:
        if out_of_sample <= 0:
            reason = (
                f"Op de zoekhelft leverde {best_threshold} {best_gain:+.2f} op, "
                f"maar op de controlehelft {out_of_sample:+.2f}. Dat is het "
                "kenmerk van overfitting: de winst zat in de ruis."
            )
        elif t_stat < 2.0:
            reason = (
                f"Verbetering van {out_of_sample:+.2f} met t={t_stat:.2f}. "
                "Onder 2,0 is dat niet te onderscheiden van toeval."
            )
        else:
            reason = f"Slechts {len(subset)} trades in de controlegroep; te weinig."
    else:
        reason = (
            f"Verbetering van {out_of_sample:+.2f} op data die bij het zoeken niet "
            f"is gebruikt, met t={t_stat:.2f} over {len(subset)} trades. "
            "Statistisch houdbaar, maar nog steeds jouw beslissing."
        )

    return Proposal(
        parameter="entry_threshold", current=current, suggested=best_threshold,
        in_sample_gain=best_gain, out_of_sample_gain=out_of_sample,
        t_statistic=t_stat, trades_evaluated=len(subset),
        accept=accept, reasoning=reason,
    )


def regime_performance(trades: Sequence[Trade]) -> dict:
    """Hoe presteerde de strategie per marktregime?

    Beschrijvend, niet sturend. Blijkt hij in één regime structureel te
    verliezen, dan is dat informatie voor jou - geen aanleiding om automatisch
    dat regime uit te sluiten, want dan optimaliseer je alsnog op het verleden.
    """
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        if trade.close_time and trade.net_pnl is not None:
            buckets[trade.regime or "onbekend"].append(trade)

    out = {}
    for regime, group in buckets.items():
        nets = [t.net_pnl for t in group]
        out[regime] = {
            "trades": len(group),
            "net_pnl": round(sum(nets), 2),
            "win_rate": round(
                sum(1 for n in nets if n > 0) / len(nets) * 100, 1
            ),
            "t_statistic": round(_t_statistic(group), 2),
            "significant": len(group) >= MIN_OBSERVATIONS and abs(_t_statistic(group)) >= 2.0,
        }
    return out
