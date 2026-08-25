"""Resultaten per dag, week en maand.

Een totaalcijfer verbergt wat je wilt weten. Vierhonderd euro winst over drie
maanden kan betekenen dat je elke week iets verdiende, of dat één week
vierhonderd opleverde en de rest vlak was. Dat zijn twee heel verschillende
systemen, en alleen het eerste is er een.

Alles wordt op lokale kalenderdagen gegroepeerd, niet op UTC. Een trade van
half twee 's nachts hoort bij die nacht zoals jij hem beleeft, niet bij de
vorige dag.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from ..storage.database import Trade


@dataclass(slots=True)
class Bucket:
    label: str
    start: str
    trades: int = 0
    wins: int = 0
    gross: float = 0.0
    costs: float = 0.0
    net: float = 0.0
    best: float = 0.0
    worst: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades else 0.0

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "start": self.start,
            "trades": self.trades,
            "wins": self.wins,
            "win_rate": round(self.win_rate, 1),
            "gross": round(self.gross, 2),
            "costs": round(self.costs, 2),
            "net": round(self.net, 2),
            "best": round(self.best, 2),
            "worst": round(self.worst, 2),
        }


@dataclass(slots=True)
class PeriodReport:
    daily: list[Bucket] = field(default_factory=list)
    weekly: list[Bucket] = field(default_factory=list)
    monthly: list[Bucket] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "daily": [b.as_dict() for b in self.daily[-31:]],
            "weekly": [b.as_dict() for b in self.weekly[-26:]],
            "monthly": [b.as_dict() for b in self.monthly[-24:]],
            "streaks": self.streaks(),
        }

    def streaks(self) -> dict:
        """Opeenvolgende winst- en verliesdagen.

        Zegt meer over de houdbaarheid dan een gemiddelde: acht verliesdagen op
        rij is iets anders dan acht verspreid over twee maanden, ook als het
        totaal gelijk is.
        """
        if not self.daily:
            return {}
        langste_winst = langste_verlies = huidig = 0
        richting = 0
        for bucket in self.daily:
            teken = 1 if bucket.net > 0 else (-1 if bucket.net < 0 else 0)
            if teken == 0:
                continue
            if teken == richting:
                huidig += 1
            else:
                richting, huidig = teken, 1
            if teken > 0:
                langste_winst = max(langste_winst, huidig)
            else:
                langste_verlies = max(langste_verlies, huidig)
        winstdagen = sum(1 for b in self.daily if b.net > 0)
        return {
            "longest_winning": langste_winst,
            "longest_losing": langste_verlies,
            "winning_days": winstdagen,
            "total_days": len(self.daily),
            "share_winning": round(winstdagen / len(self.daily), 3),
            "median_day": round(
                statistics.median([b.net for b in self.daily]), 2
            ),
        }


def _local(iso: str, tz) -> datetime:
    moment = datetime.fromisoformat(iso)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz) if tz else moment


def _fill(bucket: Bucket, trade: Trade) -> None:
    net = trade.net_pnl or 0.0
    bucket.trades += 1
    bucket.wins += 1 if net > 0 else 0
    bucket.gross += trade.gross_pnl or 0.0
    bucket.costs += trade.total_cost or 0.0
    bucket.net += net
    bucket.best = max(bucket.best, net)
    bucket.worst = min(bucket.worst, net)


def build_periods(trades: Sequence[Trade], tz=None) -> PeriodReport:
    """Groepeer gesloten trades per dag, week en maand."""
    closed = [t for t in trades if t.close_time and t.net_pnl is not None]
    report = PeriodReport()
    if not closed:
        return report

    dagen: dict[str, Bucket] = {}
    weken: dict[str, Bucket] = {}
    maanden: dict[str, Bucket] = {}

    for trade in closed:
        try:
            moment = _local(trade.close_time, tz)
        except (TypeError, ValueError):
            continue

        dag = moment.date().isoformat()
        jaar, week, _ = moment.isocalendar()
        weeksleutel = f"{jaar}-W{week:02d}"
        maandsleutel = moment.strftime("%Y-%m")

        _fill(
            dagen.setdefault(dag, Bucket(moment.strftime("%d-%m"), dag)), trade
        )
        _fill(
            weken.setdefault(
                weeksleutel, Bucket(f"week {week}", weeksleutel)
            ),
            trade,
        )
        _fill(
            maanden.setdefault(
                maandsleutel, Bucket(moment.strftime("%b %Y"), maandsleutel)
            ),
            trade,
        )

    report.daily = [dagen[k] for k in sorted(dagen)]
    report.weekly = [weken[k] for k in sorted(weken)]
    report.monthly = [maanden[k] for k in sorted(maanden)]
    return report
