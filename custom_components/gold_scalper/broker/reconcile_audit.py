"""Controleert of onze administratie klopt met wat de broker werkelijk doet.

De aanleiding is een categorie fouten die geen enkele unittest vangt. Tests
toetsen of de code doet wat de bedoeling was; ze weten niet of die bedoeling
overeenkomt met hoe de broker zich gedraagt. Drie voorbeelden uit de praktijk,
alle drie ontdekt door de brokerinterface naast de eigen rapportage te leggen:

* ``modify_stop`` stuurde alleen het stopniveau. IG's endpoint vervangt de héle
  set, dus dat wiste de take-profit.
* ``close(ticket, units)`` accepteerde een omvang en negeerde die. Elke
  deelsluiting sloot de volledige positie terwijl de administratie de helft
  boekte.
* Orders worden in USD geplaatst terwijl het account in euro's staat, waardoor
  de dagverlieslimiet in een andere eenheid rekent dan het resultaat.

Wat deze module doet is niet slimmer testen maar *vergelijken*: haal op wat de
broker zegt, leg het naast wat wij denken, en meld elk verschil. Een verschil
is niet altijd een fout - er zit vertraging tussen de twee - maar een verschil
dat blijft bestaan is dat wel.

Bewust waarschuwend en niet blokkerend, op één uitzondering na: een positie
zonder stop. Dat is het enige scenario met in principe onbegrensd verlies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

from ..storage.database import Trade
from .adapter import VenuePosition

_LOGGER = logging.getLogger(__name__)

#: Verschil in omvang waaronder we niets zeggen; afrondingsruis.
SIZE_TOLERANCE = 0.005

#: Verschil in prijsniveau waaronder we niets zeggen.
PRICE_TOLERANCE = 0.01


@dataclass(slots=True)
class Finding:
    severity: str          # "kritiek", "waarschuwing", "informatie"
    code: str
    message: str
    ticket: str | None = None

    def as_dict(self) -> dict:
        return {
            "severity": self.severity, "code": self.code,
            "message": self.message, "ticket": self.ticket,
        }


@dataclass(slots=True)
class Audit:
    findings: list[Finding] = field(default_factory=list)
    positions_checked: int = 0

    @property
    def critical(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "kritiek"]

    def as_dict(self) -> dict:
        return {
            "positions_checked": self.positions_checked,
            "critical": len(self.critical),
            "findings": [f.as_dict() for f in self.findings],
        }


def compare_positions(
    broker: Sequence[VenuePosition],
    database: Sequence[Trade],
    *,
    expected_currency: str | None = None,
    account_currency: str | None = None,
) -> Audit:
    """Leg de posities bij de broker naast de open trades in de database."""
    audit = Audit(positions_checked=len(broker))
    by_ticket = {
        str(t.broker_ticket): t for t in database if t.broker_ticket
    }
    seen: set[str] = set()

    for position in broker:
        ticket = str(position.ticket)
        seen.add(ticket)

        # Het gevaarlijkste geval, en het enige dat blokkeert.
        if not position.stop_loss:
            audit.findings.append(Finding(
                "kritiek", "geen_stop",
                f"Positie {ticket} ({position.side} {position.units}) heeft geen "
                "stop bij de broker. Dit is het enige scenario met in principe "
                "onbegrensd verlies.",
                ticket,
            ))

        if not position.take_profit:
            audit.findings.append(Finding(
                "waarschuwing", "geen_doel",
                f"Positie {ticket} heeft geen doel bij de broker. De bot sluit "
                "zelf op het doel, maar die winst is niet beschermd als Home "
                "Assistant uitvalt.",
                ticket,
            ))

        trade = by_ticket.get(ticket)
        if trade is None:
            audit.findings.append(Finding(
                "kritiek", "onbekende_positie",
                f"Positie {ticket} staat open bij de broker maar niet in de "
                "database. Niemand bewaakt hem en hij telt nergens in mee.",
                ticket,
            ))
            continue

        # Omvang. Hier kwam de deelsluiting-bug aan het licht: de broker sloot
        # alles terwijl de database de helft boekte.
        from ..const import CONTRACT_SIZE

        expected_units = trade.volume * CONTRACT_SIZE
        if abs(expected_units - position.units) > SIZE_TOLERANCE:
            audit.findings.append(Finding(
                "kritiek", "omvang_verschilt",
                f"Positie {ticket}: broker meldt {position.units}, database "
                f"{expected_units:.2f}. Zolang die twee uiteenlopen, is elk "
                "resultaatcijfer onbetrouwbaar.",
                ticket,
            ))

        if (
            trade.stop_loss and position.stop_loss
            and abs(trade.stop_loss - position.stop_loss) > PRICE_TOLERANCE
        ):
            audit.findings.append(Finding(
                "waarschuwing", "stop_verschilt",
                f"Positie {ticket}: stop bij de broker {position.stop_loss}, "
                f"in de database {trade.stop_loss}.",
                ticket,
            ))

        if (
            trade.take_profit and position.take_profit
            and abs(trade.take_profit - position.take_profit) > PRICE_TOLERANCE
        ):
            audit.findings.append(Finding(
                "waarschuwing", "doel_verschilt",
                f"Positie {ticket}: doel bij de broker {position.take_profit}, "
                f"in de database {trade.take_profit}.",
                ticket,
            ))

        if position.side != trade.side:
            audit.findings.append(Finding(
                "kritiek", "richting_verschilt",
                f"Positie {ticket}: broker meldt {position.side}, database "
                f"{trade.side}. Een van beide klopt niet.",
                ticket,
            ))

    for ticket, trade in by_ticket.items():
        if ticket not in seen:
            audit.findings.append(Finding(
                "waarschuwing", "verdwenen_positie",
                f"Trade {ticket} staat open in de database maar niet bij de "
                "broker. Waarschijnlijk gesloten op een stop of doel; wordt "
                "alsnog afgerekend.",
                ticket,
            ))

    if expected_currency and account_currency:
        if expected_currency.upper() != account_currency.upper():
            audit.findings.append(Finding(
                "waarschuwing", "valuta_verschilt",
                f"Orders worden in {expected_currency} geplaatst terwijl het "
                f"account in {account_currency} staat. Resultaten en "
                "risicolimieten rekenen dan in verschillende eenheden; bij een "
                "koers rond 1,08 scheelt dat zo'n acht procent.",
            ))

    return audit
