"""Toestand die een herstart moet overleven.

Twee dingen stonden tot nu toe alleen in het geheugen, en dat waren precies de
twee die er het meest toe doen.

**De hoofdschakelaar.** Wie hem aanzette en daarna Home Assistant herstartte -
voor een update, een herconfiguratie, wat dan ook - kwam terug met een bot die
stilstond zonder dat iets dat meldde. Dat is niet alleen onhandig maar ook
misleidend: je gaat ervan uit dat er gehandeld wordt.

**De noodstop.** Dit is de ernstiger van de twee. Een noodstop wordt gezet
omdat er iets grondig mis is: dagverlies overschreden, equity onder de
ondergrens, dataverbinding dood. Als een herstart die toestand wist, is de
noodrem precies zo betrouwbaar als de vraag of iemand toevallig herstart heeft.
Een bot die na een verliesdag vanzelf weer begint omdat er een update
langskwam, is gevaarlijker dan een bot zonder noodrem, want je vertrouwt op
bescherming die er niet is.

Opgeslagen in ``.storage/gold_scalper_state``, per config entry.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "gold_scalper_state"
STORAGE_VERSION = 1


@dataclass(slots=True)
class RuntimeState:
    """Wat er bewaard blijft tussen herstarts."""

    enabled: bool = False
    halted: bool = False
    halt_reason: str | None = None
    #: Verliesreeks blijft staan: drie verliezers gevolgd door een herstart
    #: zijn nog steeds drie verliezers.
    consecutive_losses: int = 0
    #: Handelsdag en dagstartsaldo, zodat de dagverlieslimiet niet reset bij
    #: een herstart halverwege de dag.
    day: str | None = None
    day_start_balance: float | None = None
    trades_today: int = 0
    run_id: int | None = None
    #: Zelfgebouwde bars, zodat de opwarmfase een herstart overleeft. Zonder
    #: dit kost elke update opnieuw uren voordat de analyse iets kan zeggen.
    bars: dict | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class StateStore:
    """Leest en schrijft de toestand per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._entry_id = entry_id
        self._all: dict[str, Any] = {}

    async def async_load(self) -> RuntimeState:
        self._all = await self._store.async_load() or {}
        raw = self._all.get(self._entry_id) or {}
        fields = {f for f in RuntimeState.__dataclass_fields__}
        state = RuntimeState(**{k: v for k, v in raw.items() if k in fields})

        if state.halted:
            _LOGGER.warning(
                "Noodstop uit een eerdere sessie blijft actief: %s. "
                "Gebruik gold_scalper.resume nadat je de oorzaak hebt vastgesteld.",
                state.halt_reason,
            )
        elif state.enabled:
            _LOGGER.info("Handel was ingeschakeld vóór de herstart; hervat.")
        return state

    async def async_save(self, state: RuntimeState) -> None:
        self._all[self._entry_id] = state.as_dict()
        await self._store.async_save(self._all)

    async def async_remove(self) -> None:
        """Opruimen als de entry verwijderd wordt."""
        self._all.pop(self._entry_id, None)
        await self._store.async_save(self._all)
