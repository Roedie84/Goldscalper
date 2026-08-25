"""Meldingen naar je telefoon.

Twee soorten, met een heel verschillend karakter.

**Het uurbericht** is een samenvatting: wat is er dit uur gebeurd, wat staat er
onder de streep. Rustig, samenvattend, en overslaan als er niets te melden valt
- een melding elk uur die "nul trades" zegt, leer je binnen een dag negeren, en
dan mis je ook de berichten die er wél toe doen.

**De waarschuwing** gaat direct en één keer per gebeurtenis. Noodstop,
onbeschermde positie, dataprobleem. Op iOS wordt die als kritieke melding
verstuurd zodat hij door een stille stand heen komt; bij een noodstop op een
handelsbot is dat gepast.

De dubbeldetectie zit hier en niet in een automatisering: dezelfde toestand
duurt vaak vele cycli, en zonder onderdrukking krijg je elke tien seconden
hetzelfde bericht.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

#: Minimale tijd tussen twee identieke waarschuwingen.
REPEAT_AFTER = timedelta(hours=4)


@dataclass(slots=True)
class NotifierConfig:
    service: str | None = None          # bv. "mobile_app_iphone_van_ruud"
    hourly: bool = True
    critical: bool = True
    #: Uurbericht overslaan als er geen trades en geen bijzonderheden waren.
    skip_quiet_hours: bool = True


@dataclass(slots=True)
class _Sent:
    """Wat er al verstuurd is, om herhaling te voorkomen."""

    keys: dict[str, datetime] = field(default_factory=dict)
    last_hourly: datetime | None = None
    last_trade_count: int = 0
    last_net: float = 0.0


class Notifier:
    """Verstuurt meldingen via de notify-dienst die je hebt gekozen."""

    def __init__(self, hass: HomeAssistant, config: NotifierConfig) -> None:
        self.hass = hass
        self.config = config
        self._sent = _Sent()

    @property
    def enabled(self) -> bool:
        return bool(self.config.service)

    async def _send(
        self, title: str, message: str, *, critical: bool = False,
        tag: str | None = None,
    ) -> None:
        if not self.enabled:
            return

        data: dict = {"title": title, "message": message}
        extra: dict = {}
        if tag:
            # Een tag laat een nieuwe melding de vorige vervangen in plaats van
            # ernaast te komen staan. Anders staat je scherm vol met dezelfde
            # waarschuwing.
            extra["tag"] = tag
        if critical and self.config.critical:
            # Komt door een stille stand heen. Bij een noodstop op een
            # handelsbot is dat gepast; bij een uurbericht niet.
            extra["push"] = {
                "sound": {"name": "default", "critical": 1, "volume": 0.8}
            }
            extra["priority"] = "high"
            extra["ttl"] = 0
        if extra:
            data["data"] = extra

        try:
            await self.hass.services.async_call(
                "notify", self.config.service, data, blocking=False
            )
        except Exception as err:  # noqa: BLE001 - een melding mag nooit de lus slopen
            _LOGGER.warning(
                "Melding versturen via notify.%s mislukte: %s",
                self.config.service, err,
            )

    # -- waarschuwingen ------------------------------------------------------ #

    async def alert(
        self, key: str, title: str, message: str, *, critical: bool = True
    ) -> None:
        """Eén melding per gebeurtenis, niet per cyclus.

        Dezelfde toestand duurt vaak vele cycli. Zonder onderdrukking krijg je
        elke tien seconden hetzelfde bericht, en dan zet je meldingen uit.
        """
        now = datetime.now(timezone.utc)
        previous = self._sent.keys.get(key)
        if previous and now - previous < REPEAT_AFTER:
            return
        self._sent.keys[key] = now
        await self._send(title, message, critical=critical, tag=key)

    def clear(self, key: str) -> None:
        """Meld dat een toestand voorbij is, zodat hij opnieuw kan waarschuwen."""
        self._sent.keys.pop(key, None)

    # -- uurbericht ---------------------------------------------------------- #

    def hourly_due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if not (self.enabled and self.config.hourly):
            return False
        if self._sent.last_hourly is None:
            self._sent.last_hourly = now
            return False
        return (now - self._sent.last_hourly) >= timedelta(hours=1)

    async def send_hourly(self, data: dict, now: datetime | None = None) -> bool:
        """Samenvatting van het afgelopen uur. Geeft False als hij is overgeslagen."""
        now = now or datetime.now(timezone.utc)
        self._sent.last_hourly = now

        stats = data.get("stats") or {}
        trades = stats.get("trades") or 0
        net = stats.get("net_pnl") or 0.0

        new_trades = trades - self._sent.last_trade_count
        delta = net - self._sent.last_net
        self._sent.last_trade_count = trades
        self._sent.last_net = net

        state, detail = data.get("status", ("wachtend", ""))
        quiet = new_trades == 0 and state in ("wachtend", "markt_gesloten")

        if self.config.skip_quiet_hours and quiet:
            # Een uurbericht dat elke keer "nul trades" zegt, leer je negeren.
            # Dan mis je ook de berichten die er wél toe doen.
            _LOGGER.debug("Uurbericht overgeslagen: niets gebeurd")
            return False

        signals = stats.get("signals") or {}
        lines = [
            f"{new_trades} trades dit uur ({trades} totaal)",
            f"Dit uur: {delta:+.2f}   Totaal: {net:+.2f}",
        ]
        if stats.get("total_costs"):
            lines.append(f"Kosten totaal: {stats['total_costs']:.2f}")
        if stats.get("win_rate") is not None and trades:
            lines.append(f"Trefkans: {stats['win_rate']:.0f}%")
        if signals.get("evaluations"):
            lines.append(
                f"Signalen: {signals.get('acted', 0)} van "
                f"{signals['evaluations']} evaluaties"
            )
        lines.append(f"Status: {detail or state}")

        await self._send(
            f"Gold Scalper · {delta:+.2f} dit uur",
            "\n".join(lines),
            critical=False,
            tag="gold_scalper_hourly",
        )
        return True
