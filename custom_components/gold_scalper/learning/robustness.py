"""Toetst of de strategie standhoudt op data die niet gebruikt is om hem te bouwen.

De bewijsfase telt trades en dagen, maar dat is een *hoeveelheids*-eis. Vijfhonderd
trades die allemaal in dezelfde marktsituatie zijn genomen, bewijzen niets over
een andere marktsituatie. En elke instelling die is aangepast op grond van
eerdere resultaten - de drempel, de doelverhouding, het tijdsframe - heeft de
uitkomst besmet.

Wat hier gebeurt is de standaardtoets uit de systematische handel: de historie
in stukken knippen, elk stuk beoordelen, en kijken of het resultaat standhoudt
over de stukken heen. Als de winst uit één periode komt en de rest vlak of
negatief is, dan heb je geen strategie maar een gelukkige maand.

**Wat dit niet oplost.** Dit toetst op je eigen trades, en die zijn genomen
door de huidige strategie. Een werkelijke out-of-sample toets vereist data die
tijdens het ontwerp niet bestond - dus vooruit, niet achteruit. Dit is de beste
benadering die achteraf mogelijk is, en dat is minder dan het klinkt.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from ..storage.database import Trade

_LOGGER = logging.getLogger(__name__)

#: Minimaal aantal trades per periode voordat die iets zegt.
MIN_PER_PERIOD = 30

#: Minimaal aantal periodes voor een uitspraak over consistentie.
MIN_PERIODS = 3


@dataclass(slots=True)
class Period:
    label: str
    trades: int
    net_pnl: float
    win_rate: float
    t_statistic: float
    profitable: bool

    def as_dict(self) -> dict:
        return {
            "label": self.label, "trades": self.trades,
            "net_pnl": round(self.net_pnl, 2),
            "win_rate": round(self.win_rate, 1),
            "t_statistic": round(self.t_statistic, 2),
            "profitable": self.profitable,
        }


@dataclass(slots=True)
class Robustness:
    """Uitkomst van de consistentietoets."""

    periods: list[Period] = field(default_factory=list)
    profitable_periods: int = 0
    total_periods: int = 0
    consistency: float = 0.0
    #: Aandeel van de totale winst dat uit de beste periode komt.
    best_period_share: float = 0.0
    t_statistic: float = 0.0
    verdict: str = "onvoldoende_data"
    explanation: str = ""

    def as_dict(self) -> dict:
        return {
            "periods": [p.as_dict() for p in self.periods],
            "profitable_periods": self.profitable_periods,
            "total_periods": self.total_periods,
            "consistency": round(self.consistency, 3),
            "best_period_share": round(self.best_period_share, 3),
            "t_statistic": round(self.t_statistic, 2),
            "verdict": self.verdict,
            "explanation": self.explanation,
        }


def _t_stat(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    sd = statistics.stdev(values)
    if sd == 0:
        return 0.0
    return statistics.mean(values) / (sd / math.sqrt(len(values)))


def _chunk(trades: list[Trade], periods: int) -> list[list[Trade]]:
    """Verdeel in gelijke stukken op volgorde van tijd."""
    size = len(trades) // periods
    if size < MIN_PER_PERIOD:
        return []
    return [
        trades[i * size:(i + 1) * size] for i in range(periods)
    ]


def evaluate_robustness(
    trades: Sequence[Trade], target_periods: int = 5
) -> Robustness:
    """Beoordeel of het resultaat over de tijd standhoudt."""
    closed = sorted(
        (t for t in trades if t.close_time and t.net_pnl is not None),
        key=lambda t: t.close_time or "",
    )
    result = Robustness()

    needed = MIN_PER_PERIOD * MIN_PERIODS
    if len(closed) < needed:
        result.explanation = (
            f"{len(closed)} gesloten trades. Er zijn er minstens {needed} nodig "
            f"om in {MIN_PERIODS} periodes van {MIN_PER_PERIOD} te verdelen; "
            "daaronder meet je ruis."
        )
        return result

    # Zoveel periodes als er passen, maar niet meer dan gevraagd.
    periods = min(target_periods, len(closed) // MIN_PER_PERIOD)
    chunks = _chunk(closed, periods)
    if not chunks:
        result.explanation = "Te weinig trades per periode."
        return result

    for index, chunk in enumerate(chunks, start=1):
        nets = [t.net_pnl for t in chunk]
        total = sum(nets)
        first = (chunk[0].close_time or "")[:10]
        last = (chunk[-1].close_time or "")[:10]
        result.periods.append(Period(
            label=f"{index}: {first} t/m {last}",
            trades=len(chunk),
            net_pnl=total,
            win_rate=sum(1 for n in nets if n > 0) / len(nets) * 100,
            t_statistic=_t_stat(nets),
            profitable=total > 0,
        ))

    result.total_periods = len(result.periods)
    result.profitable_periods = sum(1 for p in result.periods if p.profitable)
    result.consistency = result.profitable_periods / result.total_periods

    totals = [p.net_pnl for p in result.periods]
    grand_total = sum(totals)
    best = max(totals)
    # Als de totale winst uit één periode komt, is de rest ballast.
    result.best_period_share = (
        best / grand_total if grand_total > 0 else 0.0
    )
    result.t_statistic = _t_stat([t.net_pnl for t in closed])

    result.verdict, result.explanation = _judge(result)
    return result


def _judge(r: Robustness) -> tuple[str, str]:
    """Vel een oordeel, en wees streng waar dat hoort.

    De drempels zijn bewust hoog. Een strategie die in de helft van de periodes
    verliest, heeft geen edge maar variantie - en variantie oogt in een goede
    maand precies als vakmanschap.
    """
    if r.t_statistic <= 0:
        return "negatief", (
            f"Het totaal is negatief (t={r.t_statistic:.2f}). Er is geen edge om "
            "te bewijzen; parameters bijstellen tot het positief wordt, is "
            "curve fitting."
        )

    if r.consistency < 0.5:
        return "inconsistent", (
            f"Slechts {r.profitable_periods} van de {r.total_periods} periodes "
            "was winstgevend. Een strategie die in de helft van de tijd verliest "
            "heeft geen edge maar variantie, en variantie oogt in een goede "
            "maand precies als vakmanschap."
        )

    if r.best_period_share > 0.60:
        return "geconcentreerd", (
            f"{r.best_period_share:.0%} van de winst komt uit één periode. Haal "
            "die weg en er blijft weinig over. Dat is een gelukkige periode, "
            "geen herhaalbaar systeem."
        )

    if r.t_statistic < 2.0:
        return "onbewezen", (
            f"Positief maar met t={r.t_statistic:.2f}. Onder 2,0 is het "
            "resultaat niet te onderscheiden van toeval; er zijn meer trades "
            "nodig, geen andere instellingen."
        )

    return "houdbaar", (
        f"{r.profitable_periods} van de {r.total_periods} periodes winstgevend, "
        f"t={r.t_statistic:.2f}, grootste periode {r.best_period_share:.0%} van "
        "het totaal. Dit houdt stand over de tijd heen.\n\n"
        "Let op wat dit niet zegt: deze trades zijn genomen door de huidige "
        "strategie op data uit het verleden. Een echte out-of-sample toets "
        "loopt vooruit, niet achteruit."
    )
