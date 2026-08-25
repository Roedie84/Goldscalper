"""Pyramiden: bijkopen bij bevestiging, nooit bij tegenslag.

Het spiegelbeeld van middelen. Bij middelen vergroot je een positie die
ongelijk krijgt; hier vergroot je er een die gelijk krijgt.

Het verschil is niet cosmetisch. Bij middelen groeit je verlies kwadratisch
terwijl je stop dezelfde blijft: vier keer bijkopen bij een dalende koers
verandert een verlies van veertig in vierhonderd zonder dat je stop ook maar
één keer geraakt is. Bij pyramiden groeit je positie alleen als de markt je
gelijk geeft, en verschuift de stop van het geheel mee omhoog.

**De regel die alles bijeenhoudt.** Elke toevoeging gaat gepaard met het
verplaatsen van de stop, zodat het totale risico van de samengestelde positie
niet groter wordt dan dat van de eerste. Lukt dat niet - de stop kan niet mee
omhoog - dan gaat de toevoeging niet door. Zonder die koppeling is pyramiden
gewoon een trager soort middelen.

**Afnemende omvang.** Elke volgende toevoeging is kleiner dan de vorige. Een
piramide die naar boven toe breder wordt, valt om: je grootste inzet zit dan op
het hoogste punt, precies waar een trend het vaakst eindigt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PyramidConfig:
    enabled: bool = False
    #: Winst in ATR waarbij de eerste toevoeging mag.
    #:
    #: Onder 1,0 voeg je toe voordat de beweging zich heeft bewezen, en dan
    #: lijkt het op middelen met een positief voorteken.
    trigger_atr: float = 1.0
    #: Hoeveel toevoegingen maximaal.
    max_additions: int = 2
    #: Omvang van de eerste toevoeging, als fractie van de oorspronkelijke.
    first_fraction: float = 0.5
    #: Waarmee elke volgende toevoeging krimpt.
    decay: float = 0.5
    #: Minimale extra winst tussen twee toevoegingen, in ATR.
    #:
    #: Zonder deze afstand stapelen alle toevoegingen zich op één prijsniveau,
    #: en dan heb je geen piramide maar een grote positie met een dun excuus.
    spacing_atr: float = 0.75


@dataclass(slots=True)
class PyramidDecision:
    add: bool
    units: float = 0.0
    new_stop: float | None = None
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "add": self.add, "units": round(self.units, 3),
            "new_stop": self.new_stop, "reason": self.reason,
        }


def consider_addition(
    cfg: PyramidConfig,
    *,
    side: str,
    entry_price: float,
    current_price: float,
    current_stop: float | None,
    original_units: float,
    total_units: float,
    additions_done: int,
    last_addition_price: float | None,
    atr: float,
    round_trip_cost_per_oz: float,
) -> PyramidDecision:
    """Mag er bijgekocht worden, en zo ja hoeveel?

    Weigert bij elke twijfel. Een gemiste toevoeging kost je een stukje winst;
    een verkeerde vergroot je risico op het slechtst denkbare moment.
    """
    if not cfg.enabled:
        return PyramidDecision(False, reason="pyramiden staat uit")
    if atr <= 0:
        return PyramidDecision(False, reason="geen bruikbare ATR")
    if additions_done >= cfg.max_additions:
        return PyramidDecision(
            False, reason=f"maximum van {cfg.max_additions} toevoegingen bereikt"
        )

    direction = 1.0 if side == "buy" else -1.0
    profit = (current_price - entry_price) * direction
    profit_atr = profit / atr

    # Alleen bij bevestiging. Dit is het hele verschil met middelen.
    if profit_atr < cfg.trigger_atr:
        return PyramidDecision(
            False,
            reason=(
                f"winst {profit_atr:+.2f}xATR onder de drempel "
                f"{cfg.trigger_atr}; bijkopen mag alleen bij bevestiging"
            ),
        )

    # Afstand tot de vorige toevoeging.
    if last_addition_price is not None:
        sinds = (current_price - last_addition_price) * direction
        if sinds < cfg.spacing_atr * atr:
            return PyramidDecision(
                False,
                reason=(
                    f"slechts {sinds / atr:.2f}xATR sinds de vorige toevoeging; "
                    f"minimaal {cfg.spacing_atr} vereist"
                ),
            )

    units = original_units * cfg.first_fraction * (cfg.decay ** additions_done)
    if units <= 0:
        return PyramidDecision(False, reason="berekende omvang is nul")

    # De stop moet mee omhoog, zodat het totale risico niet groeit. Het nieuwe
    # niveau volgt uit de eis dat het verlies van de samengestelde positie
    # hoogstens gelijk is aan dat van de oorspronkelijke.
    if current_stop is None:
        return PyramidDecision(
            False, reason="geen stop; bijkopen zonder stop is nooit veilig"
        )

    original_risk = abs(entry_price - current_stop) * original_units
    combined = total_units + units
    # Waar moet de stop staan zodat combined x afstand = original_risk?
    max_distance = original_risk / combined
    new_stop = current_price - direction * max_distance

    # De stop mag alleen gunstiger worden.
    if (new_stop - current_stop) * direction <= 0:
        return PyramidDecision(
            False,
            reason=(
                "de stop kan niet ver genoeg mee omhoog om het risico gelijk te "
                "houden; toevoeging overgeslagen"
            ),
        )

    # En hij moet minstens de kosten van het geheel dekken, anders sluit je
    # met verlies op een positie die in de plus stond.
    breakeven = entry_price + direction * round_trip_cost_per_oz
    if (new_stop - breakeven) * direction < 0:
        return PyramidDecision(
            False,
            reason=(
                "de vereiste stop ligt nog onder break-even; te vroeg om bij te "
                "kopen"
            ),
        )

    return PyramidDecision(
        add=True,
        units=round(units, 2),
        new_stop=round(new_stop, 3),
        reason=(
            f"winst {profit_atr:.2f}xATR bevestigt de richting; {units:.2f} oz "
            f"toegevoegd (toevoeging {additions_done + 1} van "
            f"{cfg.max_additions}) en de stop naar {new_stop:.2f}, zodat het "
            f"totale risico gelijk blijft aan de eerste positie "
            f"({original_risk:.2f})"
        ),
    )
