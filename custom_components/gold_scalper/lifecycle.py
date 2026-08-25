"""Levenscyclus: netjes stoppen, en veilig weer opstarten.

Home Assistant herstart regelmatig - updates, herconfiguratie, een herstart van
de host. Voor de meeste integraties is dat onschuldig: een sensor mist een
meting en pikt de draad weer op. Bij een tradebot ligt dat anders, want er kan
echt geld openstaan op het moment dat het proces verdwijnt.

Er zijn drie scenario's, met oplopend risico.

**Geplande herstart.** Je weet het van tevoren. De bot stopt met nieuwe posities
openen, wacht tot de lopende posities gesloten zijn, en meldt dat het veilig is.
Dat is ``drain()``.

**Ongeplande herstart.** HA valt om, of de host wordt gereboot. Er is geen tijd
om iets af te handelen. Hiervoor bestaat de dead man's switch in de bridge: die
sluit posities als hij te lang niets van HA hoort.

**Herstart met openstaande stand.** Na het opstarten weet de database niet meer
wat er werkelijk bij de broker staat. ``reconcile()`` vergelijkt beide en
weigert te handelen als ze uiteenlopen.

De belangrijkste veiligheidsmaatregel staat niet in dit bestand maar in de
manier waarop posities geopend worden: **elke live positie krijgt vanaf het
eerste moment een stop-loss bij de broker zelf**. Die blijft staan als HA,
het netwerk en de Windows-machine allemaal wegvallen. Alle logica hieronder is
een aanvulling daarop, geen vervanging.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

_LOGGER = logging.getLogger(__name__)


class LifecycleState(str, Enum):
    STARTING = "starting"
    RECONCILING = "reconciling"
    RUNNING = "running"
    DRAINING = "draining"        # geen nieuwe posities, lopende afwikkelen
    SAFE_TO_RESTART = "safe_to_restart"
    STOPPED = "stopped"
    DIVERGED = "diverged"        # database en broker zijn het oneens


class DrainPolicy(str, Enum):
    """Wat er met open posities gebeurt bij een drain."""

    CLOSE_NOW = "close_now"
    """Direct sluiten tegen marktprijs. Snelst veilig, kost de spread."""

    WAIT_FOR_EXIT = "wait_for_exit"
    """Wachten tot SL of TP raakt. Geen extra kosten, maar onbepaalde duur."""

    WAIT_THEN_CLOSE = "wait_then_close"
    """Wachten met een tijdslimiet, daarna alsnog sluiten. De standaard."""


@dataclass(slots=True)
class DrainResult:
    completed: bool
    state: LifecycleState
    closed_positions: list[int] = field(default_factory=list)
    remaining_positions: int = 0
    elapsed_seconds: float = 0.0
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "completed": self.completed,
            "state": self.state.value,
            "closed_positions": self.closed_positions,
            "remaining_positions": self.remaining_positions,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "message": self.message,
        }


@dataclass(slots=True)
class ReconcileResult:
    """Uitkomst van de vergelijking tussen database en broker."""

    consistent: bool
    broker_positions: list[dict] = field(default_factory=list)
    database_open: list[str] = field(default_factory=list)
    orphaned_at_broker: list[dict] = field(default_factory=list)
    """Posities die bij de broker staan maar niet in de database. Gevaarlijk:
    niemand bewaakt ze."""
    missing_at_broker: list[str] = field(default_factory=list)
    """Trades die de database open acht maar die de broker niet kent. Meestal
    gesloten door een stop terwijl HA uit stond."""
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "consistent": self.consistent,
            "broker_position_count": len(self.broker_positions),
            "database_open_count": len(self.database_open),
            "orphaned_at_broker": self.orphaned_at_broker,
            "missing_at_broker": self.missing_at_broker,
            "message": self.message,
        }


class LifecycleController:
    """Bewaakt de overgangen rond opstarten en afsluiten."""

    def __init__(
        self,
        drain_policy: DrainPolicy = DrainPolicy.WAIT_THEN_CLOSE,
        drain_timeout_seconds: float = 120.0,
    ) -> None:
        self.state = LifecycleState.STARTING
        self.drain_policy = drain_policy
        self.drain_timeout = drain_timeout_seconds
        self.last_transition = datetime.now(timezone.utc)
        self.history: list[str] = []

    def _transition(self, new_state: LifecycleState, reason: str = "") -> None:
        old = self.state
        self.state = new_state
        self.last_transition = datetime.now(timezone.utc)
        entry = (
            f"{self.last_transition.isoformat(timespec='seconds')} "
            f"{old.value} -> {new_state.value}" + (f" ({reason})" if reason else "")
        )
        self.history.append(entry)
        self.history = self.history[-50:]
        _LOGGER.info("Levenscyclus: %s", entry)

    @property
    def accepts_new_positions(self) -> bool:
        """Alleen in RUNNING mogen er posities bij."""
        return self.state is LifecycleState.RUNNING

    @property
    def safe_to_restart(self) -> bool:
        return self.state in (LifecycleState.SAFE_TO_RESTART, LifecycleState.STOPPED)

    # -- opstarten ---------------------------------------------------------- #

    async def reconcile(
        self,
        broker_positions: list[dict],
        database_open_tickets: list[str],
    ) -> ReconcileResult:
        """Vergelijk de werkelijkheid bij de broker met onze administratie.

        Dit gebeurt vóór er ook maar één order wordt overwogen. Een bot die
        niet weet welke posities hij heeft, hoort niet te handelen: hij kan
        dubbel openen, of een stop plaatsen op een positie die niet bestaat.
        """
        self._transition(LifecycleState.RECONCILING)

        # Als tekst vergelijken: brokers gebruiken uiteenlopende formaten en
        # een getal naast een string levert altijd 'niet gevonden' op.
        broker_tickets = {str(p["ticket"]) for p in broker_positions}
        db_tickets = {str(t) for t in database_open_tickets}

        # Als tekst vergelijken: een getal uit de database naast een string
        # van de broker levert anders altijd 'onbekende positie' op, en dat
        # blokkeert de handel om een verschil dat er niet is.
        orphaned = [
            p for p in broker_positions if str(p["ticket"]) not in db_tickets
        ]
        missing = sorted(db_tickets - broker_tickets)

        if orphaned:
            result = ReconcileResult(
                consistent=False,
                broker_positions=broker_positions,
                database_open=sorted(db_tickets),
                orphaned_at_broker=orphaned,
                missing_at_broker=missing,
                message=(
                    f"{len(orphaned)} positie(s) staan open bij de broker maar zijn "
                    "onbekend in de database. Deze worden door niemand bewaakt. "
                    "Sluit ze handmatig in MT5 of gebruik /close_all, en start daarna "
                    "opnieuw."
                ),
            )
            self._transition(LifecycleState.DIVERGED, "onbekende posities bij broker")
            _LOGGER.error(result.message)
            return result

        if missing:
            # Minder ernstig: waarschijnlijk gesloten door een stop terwijl HA
            # uit stond. De administratie moet worden bijgewerkt, maar er staat
            # geen onbewaakt geld open.
            result = ReconcileResult(
                consistent=True,
                broker_positions=broker_positions,
                database_open=sorted(db_tickets),
                missing_at_broker=missing,
                message=(
                    f"{len(missing)} trade(s) stonden in de database als open maar "
                    "zijn bij de broker al gesloten, waarschijnlijk door een stop "
                    "terwijl HA uit stond. Administratie wordt bijgewerkt."
                ),
            )
            _LOGGER.warning(result.message)
            self._transition(LifecycleState.RUNNING, "bijgewerkt na herstart")
            return result

        self._transition(LifecycleState.RUNNING)
        return ReconcileResult(
            consistent=True,
            broker_positions=broker_positions,
            database_open=sorted(db_tickets),
            message="Database en broker komen overeen.",
        )

    # -- afsluiten ---------------------------------------------------------- #

    async def drain(
        self,
        get_open_positions,
        close_position,
        poll_interval: float = 1.0,
        timeout: float | None = None,
    ) -> DrainResult:
        """Stop met nieuwe posities en wikkel de lopende af.

        Roep dit aan vóórdat je HA herstart. De HA-shutdown-hook zelf is
        hiervoor ongeschikt: die krijgt maar beperkt tijd, en een positie
        afwikkelen kan minuten duren.
        """
        if self.state is LifecycleState.DRAINING:
            return DrainResult(False, self.state, message="Drain loopt al")

        self._transition(LifecycleState.DRAINING, self.drain_policy.value)
        started = asyncio.get_event_loop().time()
        limit = self.drain_timeout if timeout is None else timeout
        closed: list[int] = []

        if self.drain_policy is DrainPolicy.CLOSE_NOW:
            for position in list(await get_open_positions()):
                await close_position(position, "drain")
                closed.append(getattr(position, "id", None) or position.get("ticket"))
        else:
            while True:
                open_now = list(await get_open_positions())
                if not open_now:
                    break
                elapsed = asyncio.get_event_loop().time() - started
                if elapsed >= limit:
                    if self.drain_policy is DrainPolicy.WAIT_THEN_CLOSE:
                        _LOGGER.warning(
                            "Drain-timeout na %.0fs; %d positie(s) worden alsnog gesloten",
                            elapsed, len(open_now),
                        )
                        for position in open_now:
                            await close_position(position, "drain_timeout")
                            closed.append(
                                getattr(position, "id", None) or position.get("ticket")
                            )
                        break
                    # WAIT_FOR_EXIT: niet forceren, wel eerlijk melden.
                    elapsed = asyncio.get_event_loop().time() - started
                    return DrainResult(
                        completed=False,
                        state=self.state,
                        closed_positions=closed,
                        remaining_positions=len(open_now),
                        elapsed_seconds=elapsed,
                        message=(
                            f"Nog {len(open_now)} positie(s) open na {elapsed:.0f}s. "
                            "Beleid is WAIT_FOR_EXIT, dus er wordt niet geforceerd "
                            "gesloten. Herstart HA nu níet, of sluit handmatig."
                        ),
                    )
                await asyncio.sleep(poll_interval)

        elapsed = asyncio.get_event_loop().time() - started
        self._transition(LifecycleState.SAFE_TO_RESTART)
        return DrainResult(
            completed=True,
            state=self.state,
            closed_positions=closed,
            remaining_positions=0,
            elapsed_seconds=elapsed,
            message=(
                f"Alle posities afgewikkeld in {elapsed:.0f}s. Veilig om te herstarten."
                if closed
                else "Geen open posities. Veilig om te herstarten."
            ),
        )

    async def emergency_shutdown(self, flush_callbacks: list) -> None:
        """Laatste redmiddel bij een HA-stop-event.

        Hier is weinig tijd voor - HA gunt shutdown-handlers maar een korte
        periode. Dus geen posities meer proberen te sluiten (dat kan seconden
        duren en zou halverwege afgekapt worden), alleen de administratie
        veiligstellen. De posities zelf worden gedekt door de stops bij de
        broker en door de dead man's switch in de bridge.
        """
        _LOGGER.warning(
            "HA sluit af. Administratie wordt weggeschreven; open posities blijven "
            "gedekt door de broker-stops en de dead man's switch."
        )
        for callback in flush_callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Flush bij afsluiten mislukt")
        self._transition(LifecycleState.STOPPED, "HA stop-event")

    def as_dict(self) -> dict:
        return {
            "state": self.state.value,
            "accepts_new_positions": self.accepts_new_positions,
            "safe_to_restart": self.safe_to_restart,
            "drain_policy": self.drain_policy.value,
            "drain_timeout_seconds": self.drain_timeout,
            "last_transition": self.last_transition.isoformat(timespec="seconds"),
            "recent_history": self.history[-10:],
        }
