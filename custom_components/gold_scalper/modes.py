"""Handelsmodi en de poort tussen papier en echt geld.

Er zijn drie modi, en de overgang naar de derde is bewust moeilijk gemaakt.

``BACKTEST``
    Draait op historische data zo snel als de CPU toelaat. Geen verbinding met
    de markt, geen wachttijd. Voor het uitproberen van ideeën.

``PAPER``
    Draait op live marktdata maar met gesimuleerde uitvoering. Dit is de
    bewijsfase. Kosten worden volledig doorbelast, zodat het resultaat
    vergelijkbaar is met wat live zou gebeuren.

``LIVE``
    Stuurt echte orders. Vergrendeld tot ``LiveGate`` opengaat.

De poort is niet vanuit de UI te overrulen. Dat is geen betutteling maar de
implementatie van je eigen eis: je vroeg om een systeem dat zich eerst bewijst.
Een drempel die je kunt verlagen op een moment van ongeduld, bewijst niets. De
enige manier om hem te passeren is de criteria daadwerkelijk halen, of de
broncode aanpassen - en dat laatste is dan tenminste een bewuste daad die je
in je git-historie terugvindt.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

_LOGGER = logging.getLogger(__name__)


class TradingMode(str, Enum):
    #: Echte orders op een demo-account.
    #:
    #: Bestaat omdat de papersimulatie de kosten *modelleert* en een model
    #: fout kan zijn - zoals bleek toen een terugkoppelingslus de slippage
    #: verzesvoudigde en een winstgevende reeks in een verlies veranderde.
    #: Op een demo-account worden spread, slippage en fills *gemeten* in
    #: plaats van berekend, zonder dat er geld op het spel staat.
    #:
    #: Dit is de enige modus waarin je de aannames van de simulatie kunt
    #: toetsen. De poort naar echt geld blijft er los van staan.
    DEMO = "demo"

    #: Nog niet geïmplementeerd; gedraagt zich als PAPER. Wordt daarom niet
    #: aangeboden in de configuratie. Blijft bestaan zodat bestaande entries
    #: met deze waarde niet stukgaan bij het laden.
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"

    @property
    def uses_real_money(self) -> bool:
        return self is TradingMode.LIVE

    @property
    def places_orders(self) -> bool:
        """Worden er werkelijk orders verstuurd? Ook demo doet dat."""
        return self in (TradingMode.DEMO, TradingMode.LIVE)

    @property
    def needs_market_data(self) -> bool:
        return self is not TradingMode.BACKTEST


#: Minimale eisen voordat live handel wordt vrijgegeven.
#:
#: De duureis staat er los van het aantal trades omdat die twee verschillende
#: dingen meten. Duizend trades in twee dagen zeggen alleen iets over die twee
#: dagen; een markt kan weken lang één karakter hebben. Zonder duureis meet je
#: hoe goed de strategie past bij het weer van vorige week.
MIN_PAPER_TRADES = 500
MIN_PAPER_DAYS = 30
MIN_TRADING_DAYS_WITH_ACTIVITY = 15


@dataclass(slots=True)
class GateResult:
    """Uitkomst van de toets. ``unlocked`` is het enige dat telt."""

    unlocked: bool
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    summary: str = ""

    def as_dict(self) -> dict:
        return {
            "unlocked": self.unlocked,
            "checks": self.checks,
            "blocking_reasons": self.reasons,
            "summary": self.summary,
        }


class LiveGate:
    """Toetst of de bewijsfase geslaagd is.

    De criteria komen uit ``storage.performance.verdict`` aangevuld met eisen
    die specifiek over de *bewijsfase zelf* gaan: genoeg trades, genoeg
    verstreken tijd, en genoeg verschillende handelsdagen.
    """

    def __init__(
        self,
        min_trades: int = MIN_PAPER_TRADES,
        min_days: int = MIN_PAPER_DAYS,
        min_active_days: int = MIN_TRADING_DAYS_WITH_ACTIVITY,
    ) -> None:
        self.min_trades = min_trades
        self.min_days = min_days
        self.min_active_days = min_active_days

    def evaluate(
        self, stats: dict, run: dict, daily: list[dict],
        robustness: dict | None = None,
    ) -> GateResult:
        checks: dict[str, bool] = {}
        reasons: list[str] = []

        # Simulatordata diskwalificeert een run onmiddellijk.
        #
        # Synthetische koersen hebben geen marktstructuur: geen nieuws, geen
        # orderflow, geen deelnemers die op elkaar reageren. Winst op zulke data
        # is een eigenschap van de ruisgenerator, niet van goud. Deze toets
        # staat vooraan zodat er geen enkele route bestaat waarlangs een
        # geslaagde simulatie echt geld kan gaan uitgeven.
        simulated = False
        config = run.get("config_json")
        if config:
            try:
                simulated = bool(json.loads(config).get("simulated"))
            except (TypeError, ValueError):
                simulated = "simulat" in str(config).lower()
        checks["echte_marktdata"] = not simulated
        if simulated:
            reasons.append(
                "deze run gebruikte de simulator; synthetische data kan de "
                "strategie niet bewijzen, hoe goed het resultaat er ook uitziet"
            )

        # Een run zonder transactiekosten kan niets bewijzen. De spread is bij
        # scalping de dominante kostenpost; hem op nul zetten draait het teken
        # van vrijwel elk resultaat om.
        costs_disabled = bool(stats.get("costs_disabled"))
        if not costs_disabled and stats.get("trades"):
            costs_disabled = (stats.get("total_costs") or 0) <= 0
        checks["kosten_meegerekend"] = not costs_disabled
        if costs_disabled:
            reasons.append(
                "deze run draaide zonder transactiekosten; zonder spread is elk "
                "resultaat fictief"
            )

        trades = stats.get("trades", 0)
        checks["genoeg_trades"] = trades >= self.min_trades
        if not checks["genoeg_trades"]:
            reasons.append(
                f"{trades} trades in de bewijsfase; minimaal {self.min_trades} vereist"
            )

        started = run.get("started_at")
        elapsed_days = 0
        if started:
            elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(started)
            elapsed_days = elapsed.days
        checks["genoeg_verstreken_tijd"] = elapsed_days >= self.min_days
        if not checks["genoeg_verstreken_tijd"]:
            reasons.append(
                f"bewijsfase loopt {elapsed_days} dagen; minimaal {self.min_days} vereist"
            )

        active_days = len([d for d in daily if d.get("trades", 0) > 0])
        checks["genoeg_handelsdagen"] = active_days >= self.min_active_days
        if not checks["genoeg_handelsdagen"]:
            reasons.append(
                f"gehandeld op {active_days} verschillende dagen; "
                f"minimaal {self.min_active_days} vereist"
            )

        # Het inhoudelijke oordeel over de prestaties.
        checks["prestatie_oordeel"] = bool(stats.get("ready_for_live"))
        if not checks["prestatie_oordeel"]:
            for reason in stats.get("blocking_reasons", []) or ["prestaties onvoldoende"]:
                reasons.append(reason)

        # Winst mag niet uit één uitschieter komen. Als de beste dag meer dan
        # de helft van het totaal is, is er geen strategie maar een gelukje.
        if daily and stats.get("net_pnl", 0) > 0:
            best = max(d["net_pnl"] for d in daily)
            share = best / stats["net_pnl"] if stats["net_pnl"] else 1.0
            checks["winst_goed_verdeeld"] = share <= 0.5
            if not checks["winst_goed_verdeeld"]:
                reasons.append(
                    f"de beste dag leverde {share * 100:.0f}% van de totale winst; "
                    "het resultaat leunt op één uitschieter"
                )
        else:
            checks["winst_goed_verdeeld"] = False

        # Consistentie over de tijd. Vijfhonderd trades die allemaal in
        # dezelfde marktsituatie zijn genomen bewijzen niets over een andere:
        # de aantalseis meet hoeveelheid, deze toets meet betekenis.
        verdict = (robustness or {}).get("verdict")
        if verdict == "houdbaar":
            checks["houdt_stand_over_tijd"] = True
        else:
            checks["houdt_stand_over_tijd"] = False
            if verdict is None:
                reasons.append(
                    "consistentie over de tijd nog niet vastgesteld; daarvoor "
                    "zijn minstens 90 gesloten trades nodig"
                )
            else:
                toelichting = (robustness or {}).get("explanation", "")
                reasons.append(
                    f"consistentietoets: {verdict}. "
                    + toelichting.split("\n")[0]
                )

        unlocked = all(checks.values())
        summary = (
            "Bewijsfase geslaagd. Dat betekent dat de strategie op papier "
            "standhoudt, niet dat live handel winstgevend zal zijn: papier kent "
            "geen requotes, geen spreadverbreding rond nieuws en geen storing in "
            "je eigen keten."
            if unlocked
            else f"Live handel vergrendeld. {len(reasons)} punt(en) open."
        )
        return GateResult(unlocked=unlocked, reasons=reasons, checks=checks, summary=summary)


class ModeLockedError(Exception):
    """Poging tot live handel terwijl de poort dicht is."""


def require_live_unlocked(mode: TradingMode, gate: GateResult | dict) -> None:
    """Roep dit aan vóór elke order met echt geld. Faalt luid in plaats van stil.

    Alleen voor LIVE. De poort beschermt tegen geldverlies, en op een
    demo-account is er geen geld te verliezen - daar is juist het meten het
    doel, en de poort eist metingen die je zonder handelen nooit krijgt.

    Accepteert zowel een GateResult als het dict eruit. Eerder werd op de
    aanroepplek met ``type("G", (), self.gate)()`` een klasse uit het dict
    gefabriceerd; dat leverde attributen op met de dict-namen, waardoor
    ``gate.reasons`` niet bestond en de foutmelding zelf een fout opgooide.
    """
    if not mode.uses_real_money:
        return

    if isinstance(gate, dict):
        unlocked = bool(gate.get("unlocked"))
        reasons = list(gate.get("blocking_reasons") or [])
    else:
        unlocked = gate.unlocked
        reasons = list(gate.reasons)

    if not unlocked:
        raise ModeLockedError(
            "Live handel is vergrendeld:\n  - "
            + "\n  - ".join(reasons or ["reden onbekend"])
            + "\n\nDe bewijsfase moet eerst slagen."
        )
