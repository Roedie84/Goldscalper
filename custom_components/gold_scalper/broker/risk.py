"""Risicolimieten en noodremmen.

Het uitgangspunt van deze module: de bot draait onbeheerd. Er kijkt niemand
mee. Wat er dus toe doet is niet hoe goed hij handelt op een goede dag, maar
hoeveel schade hij kan aanrichten op een slechte dag terwijl jij op je werk zit.

Elke limiet hier is een *harde* stop, geen waarschuwing. Bij overschrijding
gaat de bot naar ``HALTED`` en handelt niet meer tot een mens hem handmatig
herstart. Dat is bewust: een automatische hervatting na een noodstop betekent
dat dezelfde storing zich in een lus kan herhalen.

Belangrijk: deze limieten beschermen tegen *runaway*-gedrag, niet tegen een
verliesgevende strategie. Een bot die netjes binnen alle limieten elke dag 1%
verliest, wordt hier niet tegengehouden. Daar is de bewijsfase voor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum

_LOGGER = logging.getLogger(__name__)


class TradingState(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"      # tijdelijk, hervat vanzelf
    HALTED = "halted"      # vereist handmatig ingrijpen


@dataclass(slots=True)
class RiskLimits:
    """Grenzen waarbinnen de bot mag opereren.

    De defaults zijn streng. Bij 200:1 hefboom op goud kan een positie van
    0,10 lot bij een beweging van 30 dollar al een derde van een account van
    1.000 euro wegnemen; de standaardwaarden gaan daarom uit van kleine
    posities en een lage dagelijkse verlieslimiet.
    """

    #: Maximaal verlies per dag, als percentage van de startbalans van die dag.
    max_daily_loss_pct: float = 2.0
    #: Absolute ondergrens voor de equity. Daaronder stopt alles.
    equity_floor_pct: float = 80.0
    #: Maximaal aantal trades per dag. Vangt een vastgelopen lus af.
    max_trades_per_day: int = 100
    #: Maximaal aantal verliezers achter elkaar voordat de bot pauzeert.
    max_consecutive_losses: int = 5
    #: Duur van de pauze na een reeks verliezers, in minuten.
    cooldown_minutes: int = 60
    #: Maximale positiegrootte in lots.
    max_volume: float = 0.10
    #: Maximaal aantal gelijktijdige posities.
    max_open_positions: int = 1
    #: Maximale duur van een positie. Vangt een positie af die blijft hangen
    #: doordat de bot is vastgelopen.
    max_position_age_seconds: int = 900
    #: Weiger bij een spread boven dit deel van de ATR.
    #:
    #: Bewust ruimer dan de grens in de strategie (0,35): dit is een vangnet
    #: tegen nieuwsmomenten waarop de spread vervijfvoudigt, niet het filter
    #: dat bepaalt of een trade de moeite waard is. Die twee door elkaar halen
    #: leverde een absolute grens van 0,60 op die IG's normale spread van 0,80
    #: al weigerde.
    max_spread_atr_ratio: float = 0.75
    #: Absolute bovengrens als laatste vangnet, in prijs-eenheden. Hoog gezet:
    #: bij goud rond 4600 is een spread van 0,80 normaal, bij goud op 3300 was
    #: dat 0,25. Een vast getal deugt hier niet als primaire grens.
    max_spread: float = 10.0
    #: Maximale tijd zonder nieuwe tick voordat de bot de dataverbinding
    #: als dood beschouwt en posities sluit.
    max_data_staleness_seconds: int = 30


@dataclass(slots=True)
class RiskState:
    """Lopende toestand. Reset per dag, behalve ``HALTED``."""

    state: TradingState = TradingState.RUNNING
    day: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    day_start_balance: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    paused_until: datetime | None = None
    halt_reason: str | None = None
    triggered: list[str] = field(default_factory=list)


class RiskManager:
    """Bewaakt de limieten en blokkeert nieuwe posities bij overschrijding."""

    def __init__(
        self,
        limits: RiskLimits,
        starting_balance: float,
        now: datetime | None = None,
    ) -> None:
        self.limits = limits
        # ``now`` is expliciet meegeefbaar zodat de handelsdag niet stilzwijgend
        # van de wandklok afhangt; dat maakt het gedrag rond middernacht
        # testbaar in plaats van afhankelijk van wanneer je de test draait.
        moment = now or datetime.now(timezone.utc)
        self.state = RiskState(day=moment.date(), day_start_balance=starting_balance)

    # -- dagwissel ---------------------------------------------------------- #

    def _roll_day(self, now: datetime, balance: float) -> None:
        # Alleen vooruit rollen. Bij ``!=`` zou een klok die terugspringt - een
        # NTP-correctie, een tijdzonewissel, een herstart met verkeerde tijd -
        # de dagverliesteller op nul zetten. Dat is precies de limiet die moet
        # blijven staan als er iets vreemds aan de hand is.
        if now.date() > self.state.day:
            _LOGGER.info(
                "Nieuwe handelsdag; teller op nul (gisteren %d trades)",
                self.state.trades_today,
            )
            self.state.day = now.date()
            self.state.day_start_balance = balance
            self.state.trades_today = 0
            self.state.consecutive_losses = 0
            # HALTED overleeft de dagwissel bewust: een noodstop hoort niet
            # om middernacht vanzelf op te lossen.
            if self.state.state is TradingState.PAUSED:
                self.state.state = TradingState.RUNNING
                self.state.paused_until = None

    # -- toetsen ------------------------------------------------------------ #

    def can_open(
        self,
        now: datetime,
        balance: float,
        equity: float,
        starting_balance: float,
        open_positions: int,
        volume: float,
        spread: float,
        last_tick_age: float,
        market_open: bool = True,
        atr: float | None = None,
    ) -> tuple[bool, str | None]:
        """Mag er nu een positie open? Geeft (toegestaan, reden bij weigering)."""
        self._roll_day(now, balance)

        if self.state.state is TradingState.HALTED:
            return False, f"noodstop actief: {self.state.halt_reason}"

        if self.state.state is TradingState.PAUSED:
            if self.state.paused_until and now < self.state.paused_until:
                remaining = (self.state.paused_until - now).total_seconds() / 60
                return False, f"pauze nog {remaining:.0f} minuten"
            self.state.state = TradingState.RUNNING
            self.state.paused_until = None

        # Gesloten markt is geen storing. Goud handelt niet in het weekend en
        # kent een dagelijkse onderbreking; de laatste koers is dan uren oud
        # zonder dat er iets mis is. Zonder dit onderscheid legt de bot zichzelf
        # de eerste vrijdagavond permanent stil met een noodstop die handmatige
        # interventie vereist.
        if not market_open:
            return False, "markt gesloten"

        # Dode dataverbinding tijdens handelsuren is wél het gevaarlijkste
        # scenario: de bot denkt te weten wat de prijs is terwijl die verouderd is.
        if last_tick_age > self.limits.max_data_staleness_seconds:
            self.halt(f"geen tickdata gedurende {last_tick_age:.0f}s tijdens handelsuren")
            return False, "dataverbinding dood"

        equity_pct = equity / starting_balance * 100.0 if starting_balance else 100.0
        if equity_pct < self.limits.equity_floor_pct:
            self.halt(
                f"equity op {equity_pct:.1f}% van de start, ondergrens is "
                f"{self.limits.equity_floor_pct:.0f}%"
            )
            return False, "equity onder de ondergrens"

        # Op equity rekenen, niet op balance. Balance bevat alleen gesloten
        # trades; open posities met een fors onrealiseerd verlies telden dus
        # niet mee. In de praktijk liep het onrealiseerde verlies op tot ruim
        # het dubbele van de daglimiet zonder dat er iets afging, omdat er
        # simpelweg nog niets was afgerekend.
        #
        # De strengste van de twee wint: een gerealiseerd verlies dat al boven
        # de limiet ligt mag niet gemaskeerd worden door een open positie die
        # toevallig in de plus staat.
        worst = min(balance, equity)
        day_loss_pct = (
            (self.state.day_start_balance - worst) / self.state.day_start_balance * 100.0
            if self.state.day_start_balance
            else 0.0
        )
        if day_loss_pct >= self.limits.max_daily_loss_pct:
            unrealised = equity - balance
            self.halt(
                f"dagverlies {day_loss_pct:.2f}% bereikt de limiet van "
                f"{self.limits.max_daily_loss_pct:.2f}%"
                + (
                    f" (waarvan {unrealised:.2f} nog niet gerealiseerd)"
                    if abs(unrealised) > 0.01 else ""
                )
            )
            return False, "daglimiet bereikt"

        if self.state.trades_today >= self.limits.max_trades_per_day:
            self.halt(f"{self.state.trades_today} trades vandaag; limiet bereikt")
            return False, "dagelijkse trade-limiet bereikt"

        if open_positions >= self.limits.max_open_positions:
            return False, "maximaal aantal posities open"

        if volume > self.limits.max_volume:
            return False, (
                f"volume {volume} boven de limiet {self.limits.max_volume}"
            )

        if spread > self.limits.max_spread:
            return False, (
                f"spread {spread:.3f} boven de absolute vangnetgrens "
                f"{self.limits.max_spread:.3f}"
            )

        # Relatief aan de beweging: een spread van 0,80 is krap bij een ATR van
        # 1,0 en verwaarloosbaar bij een ATR van 4,1.
        if atr and atr > 0:
            ratio = spread / atr
            if ratio > self.limits.max_spread_atr_ratio:
                return False, (
                    f"spread {spread:.3f} is {ratio:.0%} van de ATR ({atr:.2f}); "
                    f"vangnetgrens ligt op {self.limits.max_spread_atr_ratio:.0%}"
                )

        return True, None

    # -- terugkoppeling ----------------------------------------------------- #

    def record_open(self) -> None:
        self.state.trades_today += 1

    def record_close(self, net_pnl: float, now: datetime) -> None:
        if net_pnl < 0:
            self.state.consecutive_losses += 1
            if self.state.consecutive_losses >= self.limits.max_consecutive_losses:
                self.pause(now, f"{self.state.consecutive_losses} verliezers achter elkaar")
        else:
            self.state.consecutive_losses = 0

    def positions_to_force_close(self, now: datetime, open_trades: list) -> list:
        """Posities die te lang openstaan. Vangt een vastgelopen bot af."""
        stale = []
        for trade in open_trades:
            opened = datetime.fromisoformat(trade.open_time)
            if (now - opened).total_seconds() > self.limits.max_position_age_seconds:
                stale.append(trade)
        return stale

    # -- toestandsovergangen ------------------------------------------------ #

    def pause(self, now: datetime, reason: str) -> None:
        from datetime import timedelta

        self.state.state = TradingState.PAUSED
        self.state.paused_until = now + timedelta(minutes=self.limits.cooldown_minutes)
        self.state.triggered.append(f"{now.isoformat(timespec='seconds')} pauze: {reason}")
        _LOGGER.warning("Handel gepauzeerd tot %s: %s", self.state.paused_until, reason)

    def halt(self, reason: str) -> None:
        """Noodstop. Hervat alleen na handmatig ingrijpen."""
        if self.state.state is TradingState.HALTED:
            return
        self.state.state = TradingState.HALTED
        self.state.halt_reason = reason
        self.state.triggered.append(
            f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} NOODSTOP: {reason}"
        )
        _LOGGER.error("NOODSTOP: %s. Handmatige herstart vereist.", reason)

    def manual_resume(self) -> None:
        """Alleen aan te roepen door een mens die weet wat er gebeurd is."""
        _LOGGER.warning("Handmatige hervatting na: %s", self.state.halt_reason)
        self.state.state = TradingState.RUNNING
        self.state.halt_reason = None
        self.state.consecutive_losses = 0

    def as_dict(self) -> dict:
        return {
            "state": self.state.state.value,
            "trades_today": self.state.trades_today,
            "consecutive_losses": self.state.consecutive_losses,
            "day_start_balance": round(self.state.day_start_balance, 2),
            "halt_reason": self.state.halt_reason,
            "paused_until": (
                self.state.paused_until.isoformat() if self.state.paused_until else None
            ),
            "recent_triggers": self.state.triggered[-10:],
        }
