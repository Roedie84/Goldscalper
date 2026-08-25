"""Positiegrootte: hoeveel je inzet, en waarom dat niet vast hoort te zijn.

Een vaste ordergrootte betekent dat je risico per trade meebeweegt met de
volatiliteit. Bij een ATR van 2 riskeer je met tien ounce twintig dollar; bij
een ATR van 12 riskeer je honderdtwintig. Dat is dezelfde beslissing met een
zes keer zo groot gevolg, zonder dat je het koos.

Hier gebeurt het omgekeerde: je stelt in hoeveel je per trade wilt riskeren,
en de grootte volgt uit de stopafstand. Een brede stop levert een kleinere
positie op, een krappe stop een grotere. Het bedrag dat je verliest als de stop
raakt, blijft gelijk.

**Schalen met signaalsterkte.** Een score van 0,90 en een score van 0,46
leiden nu tot dezelfde inzet. Meer inzetten bij een sterker signaal is
verdedigbaar, maar alleen als het signaal werkelijk voorspellend is - en dat
weet je pas na honderden trades. De schaling is daarom bescheiden begrensd
(hoogstens anderhalf keer) en standaard uit. Vol vertrouwen op een ongetoetst
signaal is precies hoe rekeningen leeglopen.

**Wat dit niet is.** Dit is geen Kelly-criterium. Kelly vereist dat je je
werkelijke trefkans en uitbetalingsverhouding kent; die schat je uit het
verleden, en een overschatting leidt tot systematische overpositionering. De
gangbare praktijk is een fractie van Kelly gebruiken, en die fractie is bij
onzekerheid zo klein dat vast percentage risico praktisch hetzelfde oplevert
met minder aannames.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SizingConfig:
    """Hoe de ordergrootte bepaald wordt."""

    #: Vaste grootte in ounces. Gebruikt als risicoschaling uit staat.
    fixed_units: float = 1.0
    #: Schaal de grootte zodat elke trade hetzelfde bedrag riskeert.
    risk_based: bool = False
    #: Percentage van het eigen vermogen dat je per trade riskeert.
    #:
    #: Een half procent is een gangbaar uitgangspunt: bij twintig verliezers op
    #: rij ben je tien procent kwijt, wat pijnlijk maar overleefbaar is. Boven
    #: de twee procent wordt een normale verliesreeks existentieel.
    risk_per_trade_pct: float = 0.5
    #: Meer inzetten bij een sterker signaal.
    scale_with_confidence: bool = False
    #: Bovengrens op die schaling. Boven anderhalf wordt één sterk signaal
    #: bepalend voor je resultaat, en dat is geen systeem meer maar een gok.
    max_confidence_multiple: float = 1.5
    #: Harde bovengrens in ounces, ongeacht de berekening.
    max_units: float = 5.0
    #: Ondergrens: onder deze omvang weigert de broker of wordt de trade
    #: verhoudingsgewijs door kosten opgegeten.
    min_units: float = 0.01


@dataclass(slots=True)
class SizingResult:
    units: float
    risk_amount: float
    reason: str
    capped_by: str | None = None

    def as_dict(self) -> dict:
        return {
            "units": self.units,
            "risk_amount": round(self.risk_amount, 2),
            "reason": self.reason,
            "capped_by": self.capped_by,
        }


def position_size(
    cfg: SizingConfig,
    equity: float,
    entry_price: float,
    stop_price: float,
    score: float = 0.0,
    entry_threshold: float = 0.45,
) -> SizingResult:
    """Bepaal hoeveel ounce je inzet.

    ``stop_price`` doet het werk: de afstand tot de stop bepaalt hoeveel je
    verliest per ounce, en daarmee hoeveel ounce past binnen je risicobudget.
    """
    if not cfg.risk_based:
        units = min(cfg.fixed_units, cfg.max_units)
        distance = abs(entry_price - stop_price)
        return SizingResult(
            units=units,
            risk_amount=units * distance,
            reason=(
                f"Vaste grootte van {units} ounce. Risico per trade beweegt mee "
                f"met de stopafstand: nu {units * distance:.2f}."
            ),
        )

    distance = abs(entry_price - stop_price)
    if distance <= 0:
        return SizingResult(
            units=cfg.min_units, risk_amount=0.0,
            reason="Stopafstand is nul; teruggevallen op de minimale grootte.",
            capped_by="geen_stopafstand",
        )

    budget = equity * (cfg.risk_per_trade_pct / 100.0)
    units = budget / distance
    capped_by = None
    steps = [f"budget {budget:.2f} / stopafstand {distance:.2f} = {units:.3f} oz"]

    if cfg.scale_with_confidence:
        # Lineair tussen de drempel en 1,0, begrensd. Onder de drempel wordt er
        # sowieso niet gehandeld, dus daar begint de schaal.
        span = max(1e-6, 1.0 - entry_threshold)
        strength = min(1.0, max(0.0, (abs(score) - entry_threshold) / span))
        multiple = 1.0 + strength * (cfg.max_confidence_multiple - 1.0)
        units *= multiple
        steps.append(f"signaal {abs(score):.2f} -> x{multiple:.2f}")

    if units > cfg.max_units:
        units = cfg.max_units
        capped_by = "max_units"
        steps.append(f"begrensd op {cfg.max_units} oz")
    if units < cfg.min_units:
        units = cfg.min_units
        capped_by = "min_units"
        steps.append(f"opgehoogd naar het minimum {cfg.min_units} oz")

    units = round(units, 2)
    return SizingResult(
        units=units,
        risk_amount=units * distance,
        reason=" · ".join(steps),
        capped_by=capped_by,
    )
