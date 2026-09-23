"""Toetst het meetinstrument zelf: klopt de backtest met wat er live gebeurde?

Alle hypothesen die je op historische data toetst, rusten op de aanname dat de
backtest de werkelijkheid nabootst. Die aanname is zelden gecontroleerd, en als
hij niet klopt, is elke toets erop waardeloos - inclusief de toetsen die je
overtuigden.

Hier wordt dat gecontroleerd op de enige manier die kan: de backtest draaien
over precies de periode waarin de bot werkelijk handelde, en de uitkomsten
naast elkaar leggen.

**Wat nooit gelijk zal zijn, en waarom dat goed is.** De live-run stapte in op
losse koersen tussen bars door; de backtest gebruikt afgesloten bars. De
live-run had slippage die per fill verschilde; de backtest gebruikt één
aanname. Posities werden door de broker gesloten op momenten die de backtest
niet kent.

Exacte gelijkheid zou dus verdacht zijn: dat zou betekenen dat de backtest de
werkelijkheid te gunstig weergeeft door de ongemakkelijke delen weg te laten.

**Wat wel gelijk hoort te zijn.** De ordegrootte: hetzelfde aantal trades
binnen een factor, een vergelijkbare trefkans, en vooral een vergelijkbare
kostprijs per trade. Wijkt dat sterk af, dan meet de backtest iets anders dan
de bot doet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from ..analysis.backtest import BacktestResult
from ..storage.database import Trade

_LOGGER = logging.getLogger(__name__)

#: Onder dit aantal live-trades zegt een vergelijking niets.
MIN_LIVE_TRADES = 30


@dataclass(slots=True)
class Comparison:
    naam: str
    live: float
    backtest: float
    #: Hoeveel afwijking nog aanvaardbaar is, als fractie.
    tolerantie: float
    eenheid: str = ""

    @property
    def afwijking(self) -> float:
        if self.live == 0:
            return 0.0 if self.backtest == 0 else 1.0
        return abs(self.backtest - self.live) / abs(self.live)

    @property
    def richting(self) -> str:
        """Welke kant op wijkt de backtest af, en hoe erg is dat?

        Dit onderscheid ontbrak eerst, en het is belangrijker dan de grootte.
        Een backtest die gunstiger uitvalt dan de werkelijkheid, laat je
        conclusies trekken die live niet standhouden - dat is het gevaarlijke
        geval. Een backtest die pessimistischer is, laat je hooguit een kans
        missen.

        Voor kosten is het omgekeerd: te láge kosten in de backtest zijn te
        optimistisch.
        """
        if self.klopt:
            return "gelijk"
        te_hoog = self.backtest > self.live
        if self.naam == "kosten per trade":
            return "pessimistisch" if te_hoog else "optimistisch"
        return "optimistisch" if te_hoog else "pessimistisch"

    @property
    def klopt(self) -> bool:
        return self.afwijking <= self.tolerantie

    def as_dict(self) -> dict:
        return {
            "maat": self.naam,
            "live": round(self.live, 3),
            "backtest": round(self.backtest, 3),
            "afwijking": round(self.afwijking, 3),
            "tolerantie": self.tolerantie,
            "klopt": self.klopt,
            "richting": self.richting,
            "eenheid": self.eenheid,
        }


@dataclass(slots=True)
class Validation:
    period_start: str | None = None
    period_end: str | None = None
    live_trades: int = 0
    backtest_trades: int = 0
    comparisons: list = field(default_factory=list)
    verdict: str = "onvoldoende_data"
    explanation: str = ""

    def as_dict(self) -> dict:
        return {
            "periode": {"van": self.period_start, "tot": self.period_end},
            "live_trades": self.live_trades,
            "backtest_trades": self.backtest_trades,
            "vergelijkingen": [c.as_dict() for c in self.comparisons],
            "oordeel": self.verdict,
            "toelichting": self.explanation,
        }


def _live_stats(trades: Sequence[Trade]) -> dict:
    gesloten = [t for t in trades if t.close_time and t.net_pnl is not None]
    if not gesloten:
        return {}
    winnaars = [t for t in gesloten if (t.net_pnl or 0) > 0]
    return {
        "trades": len(gesloten),
        "trefkans": len(winnaars) / len(gesloten) * 100,
        "netto": sum(t.net_pnl or 0 for t in gesloten),
        "bruto": sum(t.gross_pnl or 0 for t in gesloten),
        "kosten_per_trade": (
            sum(t.total_cost or 0 for t in gesloten) / len(gesloten)
        ),
        "netto_per_trade": sum(t.net_pnl or 0 for t in gesloten) / len(gesloten),
    }


def compare(
    live_trades: Sequence[Trade], backtest: BacktestResult,
) -> Validation:
    """Leg de live-uitkomst naast de backtest over dezelfde periode."""
    result = Validation()
    live = _live_stats(live_trades)

    if not live or live["trades"] < MIN_LIVE_TRADES:
        result.explanation = (
            f"{live.get('trades', 0)} live-trades; minstens {MIN_LIVE_TRADES} "
            "nodig voordat een vergelijking iets zegt."
        )
        return result

    bt = backtest.summary()
    if not bt["trades"]:
        result.verdict = "backtest_leeg"
        result.explanation = (
            "De backtest deed geen enkele trade over deze periode terwijl de "
            "bot er wel handelde. Dat betekent dat de backtest andere signalen "
            "ziet dan de bot zag - meestal doordat het archief gaten heeft of "
            "doordat de bars anders zijn opgebouwd."
        )
        return result

    gesloten = [t for t in live_trades if t.close_time]
    result.period_start = min(t.open_time for t in gesloten)[:16]
    result.period_end = max(t.close_time or "" for t in gesloten)[:16]
    result.live_trades = live["trades"]
    result.backtest_trades = bt["trades"]

    result.comparisons = [
        # Het aantal trades mag flink verschillen: de bot handelde op losse
        # koersen, de backtest op afgesloten bars. Een factor twee is nog te
        # verklaren; een factor tien niet.
        Comparison("aantal trades", live["trades"], bt["trades"], 1.0),
        # De trefkans hoort dicht bij elkaar te liggen: dezelfde strategie op
        # dezelfde markt.
        Comparison("trefkans", live["trefkans"], bt["win_rate"], 0.25, "%"),
        # De belangrijkste. Kosten zijn rekenkunde, geen voorspelling; wijken
        # die af, dan rekent de backtest met andere aannames dan de
        # werkelijkheid oplevert.
        Comparison(
            "kosten per trade", live["kosten_per_trade"],
            bt["total_costs"] / bt["trades"], 0.30,
        ),
        Comparison(
            "netto per trade", live["netto_per_trade"],
            bt["net_pnl"] / bt["trades"], 1.5,
        ),
    ]

    result.verdict, result.explanation = _judge(result)
    return result


def _judge(result: Validation) -> tuple[str, str]:
    kosten = next(
        (c for c in result.comparisons if c.naam == "kosten per trade"), None
    )
    trefkans = next(
        (c for c in result.comparisons if c.naam == "trefkans"), None
    )
    mislukt = [c for c in result.comparisons if not c.klopt]

    if kosten is not None and not kosten.klopt:
        return "kosten_wijken_af", (
            f"De kostprijs per trade verschilt {kosten.afwijking:.0%}: live "
            f"{kosten.live:.2f} tegen {kosten.backtest:.2f} in de backtest.\n\n"
            "Dit is de ernstigste afwijking, want kosten zijn rekenkunde en "
            "geen voorspelling. Zolang die niet kloppen, is elke hypothese die "
            "je op deze backtest toetst gebouwd op een verkeerde kostenbasis - "
            "en juist de kosten bepalen bij deze strategie of iets rendabel "
            f"is.\n\nDe backtest is hier {kosten.richting}: "
            + (
                "hij rekent te weinig kosten, dus elk resultaat eruit is te "
                "rooskleurig."
                if kosten.richting == "optimistisch"
                else "hij rekent te veel kosten, dus elk resultaat eruit is te "
                "somber. Minder gevaarlijk, maar je verwerpt hypothesen die "
                "het live wel zouden halen."
            )
        )

    if trefkans is not None and not trefkans.klopt:
        return "trefkans_wijkt_af", (
            f"De trefkans verschilt {trefkans.afwijking:.0%}: live "
            f"{trefkans.live:.1f}% tegen {trefkans.backtest:.1f}% in de "
            "backtest.\n\nDezelfde strategie op dezelfde markt hoort dichter "
            "bij elkaar te komen. Meestal ligt het aan het instapmoment: de "
            "bot stapte in op een losse koers tussen bars door, de backtest op "
            "een afgesloten bar."
        )

    if mislukt:
        optimistisch = [c for c in mislukt if c.richting == "optimistisch"]
        namen = ", ".join(c.naam for c in mislukt)

        if optimistisch:
            return "te_optimistisch", (
                f"Kosten en trefkans komen overeen, maar {namen} valt in de "
                "backtest gunstiger uit dan live.\n\n"
                "Dat is de gevaarlijke richting: je zou hypothesen goedkeuren "
                "die het live niet halen. Tel bij elke uitkomst van deze "
                "backtest af wat hier te rooskleurig is."
            )

        return "pessimistisch", (
            f"Kosten en trefkans komen overeen; {namen} valt in de backtest "
            "ongunstiger uit dan live.\n\n"
            "Dat is de veilige richting: een hypothese die hier slaagt, doet "
            "het live waarschijnlijk beter. Wat je mist zijn hypothesen die "
            "hier net afvallen."
        )

    return "bruikbaar", (
        "Kosten, trefkans en resultaat komen binnen de tolerantie overeen. De "
        "backtest bootst na wat de bot werkelijk deed.\n\n"
        "Let op wat dit niet zegt: dat de backtest de toekomst voorspelt. Het "
        "zegt dat hij het verleden goed genoeg nabootst om er hypothesen op te "
        "vergelijken - en dat is precies waarvoor hij bedoeld is."
    )
