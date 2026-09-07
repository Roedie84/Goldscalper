"""Verliezen ontleden: pech of ontwerpfout.

Deze module leert *niet* welke omstandigheden vermeden moeten worden. Dat lijkt
de voor de hand liggende invulling van "leren van verliezen", maar het is een
val.

Bij een trefkans van 40% en een doel dat anderhalf keer de stop is, zijn
verliezers noodzakelijk: ze horen bij de verdeling. De omstandigheden die tot
verlies leidden zijn dezelfde die tot winst leiden, alleen anders afgelopen.
Ze wegfilteren haalt de verliezende helft weg én de winnende, en levert een
systeem op dat na elke tegenvaller iets uitsluit tot er niets meer over is.

Wat wél kan is verliezen ordenen naar *oorzaak*. "De markt ging de andere kant
op" is pech en daar valt niets aan te doen. Maar "de stop werd geraakt en
daarna liep de koers alsnog naar het doel" is een te krappe stop, en dat is een
fout in het exitontwerp die je kunt herstellen.

Dat onderscheid is meetbaar, want per trade wordt bewaard hoe ver de koers mee
en tegen liep (MFE en MAE). Er wordt uitsluitend een patroon gemeld als er
genoeg waarnemingen zijn; onder die grens is elke uitspraak ruis met een
verhaal eromheen.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

from ..storage.database import Trade

_LOGGER = logging.getLogger(__name__)

#: Minimaal aantal verliezers van één soort voordat het een patroon heet.
MIN_PATTERN = 15

# Oorzaken, van "jouw ontwerp" naar "de markt".
STOP_TOO_TIGHT = "stop_te_krap"
HELD_TOO_SHORT = "te_vroeg_gesloten"
COSTS_ATE_IT = "kosten_aten_de_winst"
NO_FOLLOW_THROUGH = "geen_vervolg"
#: Geen bruikbare meting. Bewust een eigen categorie: een trade waarvan de
#: uitersten niet zijn vastgelegd, hoort niet stilzwijgend bij een van de
#: verklaringen te worden opgeteld. Dat gebeurde wel, en leverde 94%
#: "geen_vervolg" op waar de werkelijke oorzaak onbekend was.
UNKNOWN = "niet_gemeten"
WRONG_DIRECTION = "verkeerde_richting"

_EXPLANATION = {
    STOP_TOO_TIGHT: (
        "De stop werd geraakt en daarna liep de koers alsnog ver genoeg door "
        "voor het doel. Dat is geen pech maar een te krappe stop."
    ),
    HELD_TOO_SHORT: (
        "Gesloten op de tijdslimiet terwijl de koers al een eind richting het "
        "doel stond. De maximale positieduur staat te kort."
    ),
    COSTS_ATE_IT: (
        "De richting klopte - bruto positief - maar de kosten waren groter dan "
        "de gevangen beweging. Dit is geen strategiefout maar een kostenprobleem."
    ),
    NO_FOLLOW_THROUGH: (
        "De koers bewoog nauwelijks, in geen van beide richtingen. Het signaal "
        "voorspelde beweging die er niet kwam."
    ),
    UNKNOWN: (
        "Van deze trade zijn de uitersten niet vastgelegd, meestal omdat de "
        "broker hem sloot voordat de beheerlus hem zag. De oorzaak is dus niet "
        "vast te stellen; hem bij een van de verklaringen optellen zou een "
        "conclusie verzinnen."
    ),
    WRONG_DIRECTION: (
        "De koers ging meteen de andere kant op. Dit is de normale, "
        "onvermijdelijke soort verlies."
    ),
}

_ACTIONABLE = {STOP_TOO_TIGHT, HELD_TOO_SHORT, COSTS_ATE_IT}

#: Categorieën die niets verklaren en dus buiten de conclusie blijven.
_UNINFORMATIVE = {UNKNOWN}


@dataclass(slots=True)
class LossPattern:
    cause: str
    count: int
    share: float
    total_loss: float
    explanation: str
    actionable: bool
    suggestion: str | None = None

    def as_dict(self) -> dict:
        return {
            "cause": self.cause,
            "count": self.count,
            "share": round(self.share, 3),
            "total_loss": round(self.total_loss, 2),
            "explanation": self.explanation,
            "actionable": self.actionable,
            "suggestion": self.suggestion,
        }


@dataclass(slots=True)
class PostMortem:
    losses: int = 0
    patterns: list[LossPattern] = field(default_factory=list)
    conclusion: str = ""
    #: Deel van de verliezen dat aan het ontwerp ligt in plaats van aan de markt.
    fixable_share: float = 0.0

    def as_dict(self) -> dict:
        return {
            "losses": self.losses,
            "patterns": [p.as_dict() for p in self.patterns],
            "conclusion": self.conclusion,
            "fixable_share": round(self.fixable_share, 3),
        }


def _classify(trade: Trade, target_atr: float, stop_atr: float) -> str:
    """Bepaal waaraan één verlies lag.

    De volgorde is van diagnosticeerbaar naar onvermijdelijk: pas als geen van
    de herstelbare oorzaken past, heet het pech.
    """
    mfe = trade.mfe or 0.0
    mae = trade.mae or 0.0
    reason = trade.close_reason or ""

    # Geen enkele beweging in beide richtingen betekent in de praktijk dat er
    # niets gemeten is: een trade die werkelijk exact op zijn instapprijs sluit
    # bestaat niet, al is het maar door de spread.
    if mfe == 0.0 and mae == 0.0:
        return UNKNOWN

    # De trade klopte, alleen de kosten niet. Dit staat vooraan omdat het geen
    # strategieprobleem is en anders onder een andere noemer zou verdwijnen.
    if (trade.gross_pnl or 0.0) > 0 and (trade.net_pnl or 0.0) < 0:
        return COSTS_ATE_IT

    # Uitgestopt, maar de koers liep daarna alsnog ver genoeg door. Hiervoor is
    # MFE nodig ná de stop; die wordt bijgehouden zolang de positie leeft, dus
    # dit vangt het geval waarin hij binnen dezelfde cyclus beide raakte.
    if reason == "stop_loss" and target_atr > 0 and mfe >= target_atr * 0.8:
        return STOP_TOO_TIGHT

    if reason in ("timeout", "drain_timeout") and target_atr > 0:
        if mfe >= target_atr * 0.6:
            return HELD_TOO_SHORT

    # Nauwelijks beweging in beide richtingen: het signaal beloofde iets dat
    # niet gebeurde.
    if stop_atr > 0 and abs(mae) < stop_atr * 0.4 and mfe < stop_atr * 0.4:
        return NO_FOLLOW_THROUGH

    return WRONG_DIRECTION


def analyse_losses(
    trades: Sequence[Trade],
    target_atr_multiple: float = 1.5,
    stop_atr_multiple: float = 1.0,
    typical_atr: float | None = None,
) -> PostMortem:
    """Orden de verliezen naar oorzaak.

    ``typical_atr`` schaalt de drempels; zonder die waarde wordt hij uit de
    trades zelf afgeleid. Meldt alleen patronen met genoeg waarnemingen: onder
    die grens is elke uitspraak ruis met een verhaal eromheen.
    """
    losers = [
        t for t in trades
        if t.close_time and (t.net_pnl or 0.0) < 0 and t.mfe is not None
    ]
    result = PostMortem(losses=len(losers))

    if len(losers) < MIN_PATTERN:
        result.conclusion = (
            f"{len(losers)} verliezende trades. Minimaal {MIN_PATTERN} nodig "
            "voordat een patroon iets betekent; daaronder is het toeval met een "
            "verhaal eromheen."
        )
        return result

    if typical_atr is None:
        excursions = [abs(t.mae or 0.0) for t in losers if t.mae]
        typical_atr = statistics.median(excursions) if excursions else 1.0
    target = typical_atr * target_atr_multiple
    stop = typical_atr * stop_atr_multiple

    counts = Counter(_classify(t, target, stop) for t in losers)
    totals: dict[str, float] = {}
    for trade in losers:
        cause = _classify(trade, target, stop)
        totals[cause] = totals.get(cause, 0.0) + (trade.net_pnl or 0.0)

    suggestions = {
        STOP_TOO_TIGHT: "Verhoog stop_loss_atr, bijvoorbeeld van 1,0 naar 1,3.",
        HELD_TOO_SHORT: "Verhoog max_hold_seconds.",
        COSTS_ATE_IT: (
            "Verhoog min_edge_multiple, of ga naar een hoger tijdsframe waar de "
            "beweging groter is ten opzichte van dezelfde spread."
        ),
    }

    for cause, count in counts.most_common():
        share = count / len(losers)
        result.patterns.append(LossPattern(
            cause=cause, count=count, share=share,
            total_loss=totals.get(cause, 0.0),
            explanation=_EXPLANATION[cause],
            actionable=cause in _ACTIONABLE,
            suggestion=suggestions.get(cause) if share >= 0.20 else None,
        ))

    # Alleen over gemeten verliezen uitspraken doen; ongemeten trades tellen
    # niet mee in de noemer, anders verdunnen ze elk percentage.
    meetbaar = sum(
        p.count for p in result.patterns if p.cause not in _UNINFORMATIVE
    )
    fixable = sum(p.count for p in result.patterns if p.actionable)
    result.fixable_share = (fixable / meetbaar) if meetbaar else 0.0

    gemeten = [p for p in result.patterns if p.cause not in _UNINFORMATIVE]
    ongemeten = next(
        (p for p in result.patterns if p.cause == UNKNOWN), None
    )
    if ongemeten and ongemeten.share > 0.5:
        result.conclusion = (
            f"Van {ongemeten.share:.0%} van de verliezen zijn de uitersten niet "
            "vastgelegd, meestal omdat de broker de positie sloot voordat de "
            "beheerlus hem zag. Daar valt geen oorzaak aan toe te kennen, en "
            "hem bij een van de verklaringen optellen zou een conclusie "
            "verzinnen."
        )
        return result

    if not gemeten:
        result.conclusion = "Geen enkele verliezer is bruikbaar gemeten."
        return result

    dominant = gemeten[0]
    if result.fixable_share < 0.25:
        result.conclusion = (
            f"{result.fixable_share:.0%} van de verliezen is toe te schrijven aan "
            "het ontwerp; de rest is de gewone, onvermijdelijke soort. Daar valt "
            "weinig aan te verbeteren zonder ook de winnaars weg te filteren."
        )
    elif dominant.actionable:
        result.conclusion = (
            f"{dominant.count} van de {len(losers)} verliezen ({dominant.share:.0%}) "
            f"komen door: {dominant.explanation} {dominant.suggestion or ''}"
        ).strip()
    else:
        result.conclusion = (
            f"{result.fixable_share:.0%} van de verliezen is herstelbaar, maar de "
            f"grootste groep ({dominant.share:.0%}) is gewone marktbeweging."
        )
    return result
