"""Resultaat per handelssessie.

Goud handelt vierentwintig uur, maar niet elk uur is hetzelfde. Onderzoek naar
goudfutures in Tokio en New York vindt dat niet-geïnformeerde handel overheerst
tijdens de Aziatische sessie, terwijl geïnformeerde handel domineert tijdens de
New Yorkse. Wie met een technisch signaal in New York handelt, staat dus vaker
tegenover een tegenpartij die meer weet.

Of dat voor deze strategie uitmaakt, valt niet te beredeneren maar wel te
meten - en de data ligt er al: elke trade heeft een tijdstempel.

**Uitsluitend observatie.** Er wordt niets gefilterd en niets geweigerd. Dat is
bewust: bij honderdvijftig trades over drie sessies zijn dat er vijftig per
groep, en een verschil dat je daar vindt is waarschijnlijk ruis. Handelen op
grond daarvan is curve fitting met een verhaal eromheen.

De grens ligt bij een paar honderd trades per sessie. Tot die tijd is dit een
venster, geen stuurmiddel.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

from ..storage.database import Trade

#: Sessietijden in UTC. Bewust UTC en niet lokale tijd: de sessies volgen de
#: beurzen, niet de Nederlandse klok, en schuiven dus niet mee met onze
#: zomertijd.
UTC = ZoneInfo("UTC")

#: Minimaal aantal trades per sessie voordat een percentage iets zegt.
MIN_PER_SESSION = 30


@dataclass(frozen=True, slots=True)
class Session:
    name: str
    start: time
    end: time
    note: str

    def contains(self, moment: datetime) -> bool:
        klok = moment.astimezone(UTC).time()
        if self.start <= self.end:
            return self.start <= klok < self.end
        return klok >= self.start or klok < self.end


#: De drie sessies, met de grenzen waarop ze in de literatuur worden gesplitst.
SESSIONS = (
    Session(
        "azie", time(23, 0), time(7, 0),
        "Tokio. Overwegend niet-geïnformeerde handel; rustiger, vormt vaak de "
        "openingsrange van de dag.",
    ),
    Session(
        "londen", time(7, 0), time(13, 0),
        "Londen vóór de opening van New York.",
    ),
    Session(
        "newyork", time(13, 0), time(23, 0),
        "New York en de overlap met Londen. Hoogste volatiliteit, en volgens "
        "onderzoek de sessie waarin geïnformeerde handel domineert.",
    ),
)


def session_of(moment: datetime) -> str:
    for sessie in SESSIONS:
        if sessie.contains(moment):
            return sessie.name
    return "onbekend"


@dataclass(slots=True)
class SessionStats:
    name: str
    trades: int = 0
    wins: int = 0
    net: float = 0.0
    gross: float = 0.0
    costs: float = 0.0
    _nets: list = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades else 0.0

    @property
    def per_trade(self) -> float:
        return (self.net / self.trades) if self.trades else 0.0

    @property
    def t_statistic(self) -> float:
        """Zonder deze waarde is een verschil tussen sessies niet te wegen."""
        if len(self._nets) < 2:
            return 0.0
        sd = statistics.stdev(self._nets)
        if sd == 0:
            return 0.0
        return statistics.mean(self._nets) / (sd / math.sqrt(len(self._nets)))

    def as_dict(self) -> dict:
        return {
            "sessie": self.name,
            "trades": self.trades,
            "trefkans": round(self.win_rate, 1),
            "netto": round(self.net, 2),
            "per_trade": round(self.per_trade, 3),
            "bruto": round(self.gross, 2),
            "kosten": round(self.costs, 2),
            "t": round(self.t_statistic, 2),
            "betrouwbaar": self.trades >= MIN_PER_SESSION,
        }


@dataclass(slots=True)
class SessionReport:
    sessions: list = field(default_factory=list)
    conclusion: str = ""

    def as_dict(self) -> dict:
        return {
            "sessies": [s.as_dict() for s in self.sessions],
            "conclusie": self.conclusion,
            "toelichting": {s.name: s.note for s in SESSIONS},
        }


def build_sessions(trades: Sequence[Trade]) -> SessionReport:
    """Splits het resultaat uit naar handelssessie."""
    stats = {s.name: SessionStats(s.name) for s in SESSIONS}
    stats["onbekend"] = SessionStats("onbekend")

    for trade in trades:
        if not trade.close_time or trade.net_pnl is None:
            continue
        try:
            moment = datetime.fromisoformat(trade.close_time)
        except (TypeError, ValueError):
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)

        s = stats[session_of(moment)]
        s.trades += 1
        s.wins += 1 if trade.net_pnl > 0 else 0
        s.net += trade.net_pnl
        s.gross += trade.gross_pnl or 0.0
        s.costs += trade.total_cost or 0.0
        s._nets.append(trade.net_pnl)

    report = SessionReport(
        sessions=[stats[s.name] for s in SESSIONS if stats[s.name].trades]
    )
    if stats["onbekend"].trades:
        report.sessions.append(stats["onbekend"])

    report.conclusion = _judge(report)
    return report


def _judge(report: SessionReport) -> str:
    """Zeg wat er te zeggen valt, en niet meer.

    Het gevaar hier is precies het omgekeerde van bij de andere analyses: een
    uitsplitsing in drieën vindt bijna altijd een 'beste' groep, ook in zuivere
    ruis. Wie daarop handelt, kiest de gunstigste van drie toevalstrekkingen.
    """
    bruikbaar = [s for s in report.sessions if s.trades >= MIN_PER_SESSION]
    if not bruikbaar:
        totaal = sum(s.trades for s in report.sessions)
        return (
            f"{totaal} trades verdeeld over de sessies, geen enkele met de "
            f"{MIN_PER_SESSION} die nodig zijn voor een percentage dat iets "
            "zegt."
        )

    beste = max(bruikbaar, key=lambda s: s.per_trade)
    slechtste = min(bruikbaar, key=lambda s: s.per_trade)

    if beste.name == slechtste.name:
        return f"Alleen {beste.name} heeft genoeg trades; geen vergelijking mogelijk."

    verschil = beste.per_trade - slechtste.per_trade
    if abs(beste.t_statistic) < 2.0:
        return (
            f"{beste.name} doet het het best ({beste.per_trade:+.2f} per trade) "
            f"en {slechtste.name} het slechtst ({slechtste.per_trade:+.2f}), een "
            f"verschil van {verschil:.2f}. Maar met t={beste.t_statistic:.2f} is "
            "zelfs de beste sessie niet van toeval te onderscheiden.\n\n"
            "Een uitsplitsing in drieën vindt bijna altijd een 'beste' groep, "
            "ook in zuivere ruis. Hier handelen op grond van dit verschil "
            "betekent de gunstigste van drie toevalstrekkingen kiezen."
        )

    return (
        f"{beste.name}: {beste.per_trade:+.2f} per trade over {beste.trades} "
        f"trades, t={beste.t_statistic:.2f}. Dat is de eerste sessie waarvan "
        "het resultaat boven de ruis uitkomt.\n\n"
        "Voordat je hierop stuurt: dit is één uitsplitsing van bestaande data. "
        "Een bevinding is pas bruikbaar als hij standhoudt op trades die ná "
        "deze constatering zijn gemaakt."
    )


# -- nieuwsvensters ------------------------------------------------------- #
#
# De grote gouddrijvers verschijnen op vaste tijdstippen: Amerikaanse
# inflatie- en banencijfers om 13:30 UTC, rentebesluiten om 19:00 UTC. De
# datums wisselen per maand, maar de klok niet.
#
# Bewust geen externe agenda. Die vraagt een sleutel, een netwerkverbinding en
# een dienst die kan omvallen - en levert alleen de datums, terwijl het venster
# al met de klok af te bakenen is. Het gevolg is dat er dagen worden
# meegeteld waarop er niets werd gepubliceerd; dat maakt de meting minder
# scherp maar niet onjuist, want die dagen verdunnen het effect in plaats van
# het te verzinnen.

#: Vensters in UTC waarin publicaties de spread verbreden.
NEWS_WINDOWS = (
    (time(13, 25), time(13, 45), "Amerikaanse macrocijfers (CPI, banen, PPI)"),
    (time(18, 55), time(19, 15), "rentebesluit"),
    (time(19, 25), time(19, 45), "persconferentie na het rentebesluit"),
)


def in_news_window(moment: datetime) -> str | None:
    """Viel dit tijdstip in een venster waarin publicaties gebruikelijk zijn?"""
    klok = moment.astimezone(UTC).time()
    for start, eind, naam in NEWS_WINDOWS:
        if start <= klok < eind:
            return naam
    return None


@dataclass(slots=True)
class NewsImpact:
    """Presteren trades rond publicatietijden anders?"""

    in_window: SessionStats = field(
        default_factory=lambda: SessionStats("rond publicaties")
    )
    outside: SessionStats = field(
        default_factory=lambda: SessionStats("daarbuiten")
    )

    def as_dict(self) -> dict:
        binnen, buiten = self.in_window, self.outside
        uit = {
            "rond_publicaties": binnen.as_dict(),
            "daarbuiten": buiten.as_dict(),
            "vensters": [
                f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')} UTC: {n}"
                for s, e, n in NEWS_WINDOWS
            ],
        }
        if binnen.trades < MIN_PER_SESSION:
            uit["conclusie"] = (
                f"{binnen.trades} trades rond publicatietijden; minstens "
                f"{MIN_PER_SESSION} nodig voor een vergelijking."
            )
        else:
            verschil = binnen.per_trade - buiten.per_trade
            uit["conclusie"] = (
                f"Rond publicaties {binnen.per_trade:+.2f} per trade tegen "
                f"{buiten.per_trade:+.2f} daarbuiten, een verschil van "
                f"{verschil:+.2f}. "
                + (
                    "Dat is de moeite van nader kijken waard."
                    if abs(verschil) > 0.5
                    else "Te klein om iets aan te veranderen."
                )
            )
        return uit


def build_news_impact(trades: Sequence[Trade]) -> NewsImpact:
    """Splits het resultaat uit naar wel of niet rond een publicatievenster."""
    impact = NewsImpact()
    for trade in trades:
        if not trade.open_time or trade.net_pnl is None or not trade.close_time:
            continue
        try:
            moment = datetime.fromisoformat(trade.open_time)
        except (TypeError, ValueError):
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)

        # Op de INSTAP kijken, niet op de uitstap: het venster gaat over de
        # omstandigheden waaronder je de positie opende.
        doel = impact.in_window if in_news_window(moment) else impact.outside
        doel.trades += 1
        doel.wins += 1 if trade.net_pnl > 0 else 0
        doel.net += trade.net_pnl
        doel.gross += trade.gross_pnl or 0.0
        doel.costs += trade.total_cost or 0.0
        doel._nets.append(trade.net_pnl)
    return impact
