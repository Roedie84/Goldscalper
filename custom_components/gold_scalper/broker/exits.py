"""Exitbeheer: winst pakken zonder hem terug te geven.

Een vaste take-profit is de eenvoudigste exit en zelden de beste. Hij laat geld
liggen wanneer de beweging doorzet, en geeft alles terug wanneer de koers vlak
vóór het doel omdraait. Deze module beheert lopende posities actief.

Vier mechanismen, in volgorde waarin ze doorgaans afgaan:

1. **Break-even.** Zodra de positie een vooraf bepaalde afstand in de winst
   staat, schuift de stop naar het instappunt plus de kosten. Vanaf dat moment
   kan de trade niet meer verliezen. Dit is het belangrijkste mechanisme bij
   scalping: het haalt het staartrisico eruit zonder de opwaartse kant te
   beperken.

2. **Gedeeltelijk sluiten.** Bij het eerste doel gaat een deel van de positie
   dicht. De rest loopt door met een stop op break-even. Zo wordt winst
   gerealiseerd terwijl de mogelijkheid op een grotere beweging openblijft.

3. **Trailing stop.** De stop volgt de koers op ATR-afstand, maar beweegt
   nooit terug. Vangt de doorloop mee zonder een vast doel te hoeven raden.

4. **Tijdstop.** Een scalp die na X seconden nog rond het instappunt hangt, is
   geen scalp meer maar een gok waar kosten op lopen. Die wordt gesloten.

Een belangrijk detail dat in veel implementaties fout gaat: alle afstanden
worden getoetst tegen de prijs waarop je daadwerkelijk uitstapt (bid voor een
long, ask voor een short), niet tegen de mid. Anders lijkt de winst groter dan
hij is en schuift de break-even-stop te vroeg, waardoor je structureel wordt
uitgestopt op posities die nog niet eens quitte stonden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

# Uit const.py: één definitie, zodat de twee helften van de code
# niet met verschillende eenheden kunnen gaan rekenen.
from ..const import CONTRACT_SIZE


@dataclass(slots=True)
class ExitConfig:
    """Instellingen voor exitbeheer. Afstanden zijn veelvouden van de ATR."""

    #: Verplaats de stop naar break-even zodra deze winst is bereikt.
    #: Break-even bij 0,8 x ATR.
    #:
    #: Ik heb geprobeerd dit naar 1,2 te verhogen met de redenering dat een
    #: vroege break-even winnaars afkapt. Die redenering klopte niet: gemeten
    #: verkleint 0,8 het gemiddelde verlies van 48,00 naar 39,62 zonder dat de
    #: gemiddelde winst verandert - de trade die break-even raakt en daarna
    #: terugvalt, zou zonder break-even zijn volle stop hebben gelopen.
    #:
    #: Het staat hier vermeld omdat het contra-intuïtief is en anders bij de
    #: volgende herziening opnieuw "verbeterd" wordt.
    breakeven_trigger_atr: float = 0.8
    #: Buffer bovenop het instappunt bij break-even, in veelvouden van de
    #: round-trip kosten. 1.0 betekent: de trade kan hierna niet meer verliezen.
    breakeven_buffer_cost_multiple: float = 1.2

    #: Sluit dit deel van de positie bij het eerste doel.
    partial_close_fraction: float = 0.5
    partial_close_trigger_atr: float = 1.0
    enable_partial_close: bool = False

    #: Trailing stop op deze ATR-afstand, actief vanaf deze winst.
    trailing_distance_atr: float = 1.2
    trailing_activate_atr: float = 1.5
    enable_trailing: bool = True

    #: Sluit als de positie na deze tijd nog binnen de dode zone hangt.
    time_stop_seconds: int = 240
    time_stop_deadzone_atr: float = 0.3

    #: Harde bovengrens op de positieduur, ongeacht resultaat.
    max_hold_seconds: int = 900


@dataclass(slots=True)
class ExitAction:
    """Wat er met een positie moet gebeuren."""

    kind: str  # "hold" | "modify_stop" | "partial_close" | "close"
    new_stop: float | None = None
    close_fraction: float | None = None
    reason: str = ""

    @property
    def is_noop(self) -> bool:
        return self.kind == "hold"


class ExitManager:
    """Bepaalt per tick wat er met een open positie moet gebeuren.

    Bewust zonder eigen toestand: alles wordt afgeleid uit de positie en de
    huidige prijs. Dat betekent dat een herstart van Home Assistant geen
    exitlogica kwijtraakt - na `reconcile()` pakt de manager de positie op
    alsof er niets gebeurd is.
    """

    def __init__(self, config: ExitConfig | None = None) -> None:
        self.config = config or ExitConfig()

    @staticmethod
    def _exit_price(side: str, bid: float, ask: float) -> float:
        """De prijs waarop je nú zou uitstappen."""
        return bid if side == "buy" else ask

    @staticmethod
    def _direction(side: str) -> float:
        return 1.0 if side == "buy" else -1.0

    def evaluate(
        self,
        side: str,
        volume: float,
        open_price: float,
        current_stop: float | None,
        bid: float,
        ask: float,
        atr: float,
        opened_at: datetime,
        now: datetime,
        round_trip_cost_per_oz: float,
        partial_taken: bool = False,
    ) -> ExitAction:
        if atr <= 0:
            return ExitAction("hold", reason="geen bruikbare ATR")

        direction = self._direction(side)
        exit_price = self._exit_price(side, bid, ask)
        profit_per_oz = (exit_price - open_price) * direction
        profit_atr = profit_per_oz / atr
        age = (now - opened_at).total_seconds()

        # --- Harde tijdslimiet ------------------------------------------------
        if age >= self.config.max_hold_seconds:
            return ExitAction(
                "close",
                reason=(
                    f"maximale positieduur van {self.config.max_hold_seconds}s bereikt "
                    f"bij {profit_per_oz:+.3f} USD/oz"
                ),
            )

        # --- Tijdstop voor een positie die nergens heen gaat ------------------
        if (
            age >= self.config.time_stop_seconds
            and abs(profit_atr) < self.config.time_stop_deadzone_atr
        ):
            return ExitAction(
                "close",
                reason=(
                    f"na {age:.0f}s nog binnen {self.config.time_stop_deadzone_atr}xATR "
                    "van het instappunt; geen scalp meer maar een kostenpost"
                ),
            )

        # --- Trailing stop ----------------------------------------------------
        # Staat vóór break-even in de volgorde omdat hij op dit punt altijd
        # verder in de winst ligt; anders zou break-even hem terugtrekken.
        if self.config.enable_trailing and profit_atr >= self.config.trailing_activate_atr:
            trail = exit_price - direction * atr * self.config.trailing_distance_atr
            if current_stop is None or (trail - current_stop) * direction > 0:
                return ExitAction(
                    "modify_stop",
                    new_stop=round(trail, 3),
                    reason=(
                        f"trailing stop op {self.config.trailing_distance_atr}xATR; "
                        f"winst staat op {profit_atr:.2f}xATR"
                    ),
                )

        # --- Gedeeltelijk sluiten bij het eerste doel -------------------------
        if (
            self.config.enable_partial_close
            and not partial_taken
            and profit_atr >= self.config.partial_close_trigger_atr
        ):
            return ExitAction(
                "partial_close",
                close_fraction=self.config.partial_close_fraction,
                reason=(
                    f"eerste doel geraakt op {profit_atr:.2f}xATR; "
                    f"{self.config.partial_close_fraction:.0%} van de positie gesloten, "
                    "de rest loopt door"
                ),
            )

        # --- Break-even -------------------------------------------------------
        #
        # Ook meteen ná een deelsluiting, en niet alleen op de eigen drempel.
        # Zodra je de helft hebt afgeroomd is de rest gratis geworden; die
        # daarna alsnog met verlies laten sluiten is de slechtste van beide
        # werelden - je hebt je winst begrensd én je verlies niet.
        trigger = (
            0.0 if partial_taken else self.config.breakeven_trigger_atr
        )
        if profit_atr >= trigger:
            buffer = round_trip_cost_per_oz * self.config.breakeven_buffer_cost_multiple
            breakeven = open_price + direction * buffer
            if current_stop is None or (breakeven - current_stop) * direction > 0:
                return ExitAction(
                    "modify_stop",
                    new_stop=round(breakeven, 3),
                    reason=(
                        f"stop naar break-even plus kosten ({buffer:.3f} USD/oz); "
                        "deze trade kan vanaf nu niet meer verliezen"
                        + (" (winst is al deels genomen)" if partial_taken else "")
                    ),
                )

        return ExitAction(
            "hold",
            reason=f"winst {profit_atr:+.2f}xATR, nog geen trigger geraakt",
        )

    def realised_profit(
        self, side: str, volume: float, open_price: float, exit_price: float
    ) -> float:
        """Bruto resultaat in accountvaluta bij deze exit."""
        return (exit_price - open_price) * self._direction(side) * volume * CONTRACT_SIZE

    def describe(self) -> dict:
        """Leesbare samenvatting voor het dashboard."""
        c = self.config
        return {
            "break_even": (
                f"stop naar instap +{c.breakeven_buffer_cost_multiple:.1f}x kosten "
                f"zodra winst {c.breakeven_trigger_atr}xATR is"
            ),
            "gedeeltelijk_sluiten": (
                f"{c.partial_close_fraction:.0%} dicht bij {c.partial_close_trigger_atr}xATR"
                if c.enable_partial_close
                else "uit"
            ),
            "trailing": (
                f"{c.trailing_distance_atr}xATR afstand vanaf {c.trailing_activate_atr}xATR winst"
                if c.enable_trailing
                else "uit"
            ),
            "tijdstop": (
                f"sluiten na {c.time_stop_seconds}s als winst binnen "
                f"±{c.time_stop_deadzone_atr}xATR blijft"
            ),
            "harde_limiet": f"{c.max_hold_seconds}s",
        }
