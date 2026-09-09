"""Rekenen met twee valuta zonder ze door elkaar te halen.

Goud noteert in dollars, maar een account kan in euro's staan. Dan meet je je
resultaat in de ene eenheid en je risicolimieten in de andere, en dat gaat mis
op twee plekken:

* **Positiegrootte.** Het budget volgt uit het eigen vermogen in euro's, de
  stopafstand staat in dollars per ounce. Delen zonder omrekenen levert een
  positie op die bij een koers rond 1,08 zo'n acht procent te groot is.
* **Kostenprojectie en resultaat.** Trades worden in dollars geboekt, het
  saldo in euro's. Ze naast elkaar zetten suggereert een nauwkeurigheid die er
  niet is.

**De koers komt van de broker, niet van een externe bron.** IG rekent zelf om
bij het afrekenen, en de koers die hij daarbij hanteert is de enige die
klopt voor jouw rekening. Een tarief van een website erbij halen zou een tweede
waarheid introduceren die net iets anders is.

Zolang die koers niet bekend is, wordt er **niet** omgerekend maar gemeld dat
het niet kan. Een geschatte koers is hier gevaarlijker dan geen koers: hij
maakt een fout onzichtbaar in plaats van zichtbaar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class Conversion:
    """Hoe je van de instrumentvaluta naar de accountvaluta komt."""

    instrument: str = "USD"
    account: str = "USD"
    #: Hoeveel accountvaluta één eenheid instrumentvaluta waard is.
    #: Bij USD naar EUR met een koers van 1,08 dollar per euro is dit 0,926.
    rate: float | None = None

    @property
    def needed(self) -> bool:
        return self.instrument.upper() != self.account.upper()

    @property
    def usable(self) -> bool:
        """Kan er betrouwbaar omgerekend worden?"""
        if not self.needed:
            return True
        return self.rate is not None and self.rate > 0

    def to_account(self, amount: float) -> float:
        """Reken een bedrag in instrumentvaluta om naar accountvaluta."""
        if not self.needed:
            return amount
        if not self.usable:
            # Bewust ongewijzigd teruggeven én melden. Stil een geschatte koers
            # gebruiken zou de fout onzichtbaar maken.
            return amount
        return amount * (self.rate or 1.0)

    def to_instrument(self, amount: float) -> float:
        """Reken een bedrag in accountvaluta om naar instrumentvaluta.

        Dit is de richting die de positiegrootte nodig heeft: een risicobudget
        in euro's moet naar dollars voordat je het door een stopafstand in
        dollars per ounce deelt.
        """
        if not self.needed:
            return amount
        if not self.usable:
            return amount
        return amount / (self.rate or 1.0)

    def note(self) -> str | None:
        """Waarschuwing als er omgerekend moet worden maar dat niet kan."""
        if not self.needed:
            return None
        if self.usable:
            return None
        return (
            f"Het instrument noteert in {self.instrument} en het account staat "
            f"in {self.account}, maar de wisselkoers is niet bekend. Er wordt "
            "niet omgerekend: positiegrootte en resultaat staan daardoor in "
            "verschillende eenheden, wat bij een koers rond 1,08 zo'n acht "
            "procent scheelt."
        )

    def as_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "account": self.account,
            "rate": self.rate,
            "needed": self.needed,
            "usable": self.usable,
            "note": self.note(),
        }


def derive_rate_from_position(
    unrealised_account: float | None,
    open_price: float,
    current_price: float | None,
    units: float,
    side: str,
) -> float | None:
    """Leid de koers af uit een open positie.

    IG meldt de onrealiseerde winst (``upl``) in accountvaluta, terwijl de
    prijsbeweging in instrumentvaluta staat. De verhouding tussen die twee is
    de koers, en hij komt dus van de broker zelf - precies degene die ook
    afrekent.

    Geeft None bij een te kleine beweging: dan bepaalt afronding de uitkomst en
    is de afgeleide koers onbetrouwbaar.
    """
    if unrealised_account is None or not current_price or units <= 0:
        return None

    richting = 1.0 if side == "buy" else -1.0
    beweging = (current_price - open_price) * richting * units
    if abs(beweging) < 1.0:
        # Onder één eenheid bepaalt afronding de uitkomst.
        return None

    koers = unrealised_account / beweging
    if not 0.1 < koers < 10.0:
        _LOGGER.debug(
            "Afgeleide koers %.4f uit een positie ligt buiten het aannemelijke "
            "bereik; genegeerd.", koers,
        )
        return None
    return koers


def derive_rate(
    account_balance: float, account_balance_in_instrument: float | None
) -> float | None:
    """Leid de koers af uit twee weergaven van hetzelfde saldo.

    Geeft None terug bij ontbrekende of onzinnige waarden. Een koers die uit
    afronding van kleine bedragen komt, is onbetrouwbaar; daarom een ondergrens
    op het saldo.
    """
    if not account_balance_in_instrument or account_balance_in_instrument <= 0:
        return None
    if abs(account_balance) < 100:
        return None
    koers = account_balance / account_balance_in_instrument
    # Buiten dit bereik gaat het niet om een gangbaar valutapaar maar om een
    # rekenfout, en dan is geen koers beter dan een verkeerde.
    if not 0.1 < koers < 10.0:
        _LOGGER.warning(
            "Afgeleide wisselkoers %.4f ligt buiten het aannemelijke bereik; "
            "er wordt niet omgerekend.", koers,
        )
        return None
    return koers
