"""Statustekst: waarom doet de bot wat hij doet.

Bewust een eigen module zonder Home Assistant-imports. De tekst die een
gebruiker leest als hij zich afvraagt waarom er niets gebeurt, hoort
onafhankelijk testbaar te zijn van het sensorplatform waarin hij toevallig
getoond wordt.
"""

from __future__ import annotations


def build_status(d: dict) -> tuple[str, str]:
    """Eén regel die zegt wat de bot doet en waarom.

    Bestaat omdat de informatie er wel was - verspreid over de signaalsensor,
    de risicosensor en de levenscyclus - maar je die drie moest combineren om
    te begrijpen waarom er niets gebeurde. Voor de meest voorkomende oorzaak,
    een uitgeschakelde hoofdschakelaar, stond het antwoord alleen in een
    attribuut van een andere entiteit.
    """
    lifecycle = (d.get("lifecycle") or {}).get("state")
    risk = (d.get("risk") or {}).get("state")

    if risk == "halted":
        return "noodstop", (
            f"Noodstop: {(d.get('risk') or {}).get('halt_reason')}. "
            "Roep gold_scalper.resume aan na controle."
        )
    if risk == "paused":
        return "gepauzeerd", "Tijdelijke pauze na een reeks verliezers."
    if lifecycle == "diverged":
        return "afgestemd_probleem", (
            "Database en broker zijn het oneens over open posities; handel geblokkeerd."
        )
    if lifecycle == "draining":
        return "afwikkelen", "Bezig met afwikkelen voor een herstart."
    if d.get("market_open") is False:
        return "markt_gesloten", (
            "De goudmarkt is gesloten. Handel hervat automatisch bij opening; "
            "dit is geen storing."
        )
    warmup = d.get("warmup")
    if warmup and not warmup.get("ready"):
        eta = warmup["eta_minutes"]
        duur = f"{eta // 60} uur en {eta % 60} minuten" if eta >= 60 else f"{eta} minuten"
        return "opwarmen", (
            f"Bars worden opgebouwd uit live koersen: {warmup['bars']} van "
            f"{warmup['needed']}. Nog ongeveer {duur}. Er wordt geen historie "
            "bij de broker opgevraagd, dus dit kost geen datapunten."
        )
    if not d.get("enabled"):
        return "uitgeschakeld", (
            "Handel staat uit. Zet de schakelaar 'Handel actief' aan om te beginnen."
        )

    positions = len(d.get("open_positions") or [])
    if positions:
        return "positie_open", f"{positions} positie(s) open; exits worden bewaakt."

    reason = d.get("reject_reason")
    if reason:
        readable = {
            "outside_hours": "Buiten het ingestelde handelsvenster.",
            "spread_too_wide": "Spread te breed om winstgevend te kunnen handelen.",
            "score_below_threshold": "Signaal te zwak; wachten op een betere kans.",
            "edge_below_cost": "Verwachte beweging dekt de kosten niet.",
            "cooldown": "Wachttijd na de vorige trade.",
            "volatility_regime": "Volatiliteit ongeschikt om te handelen.",
            "insufficient_data": "Nog te weinig historie voor een oordeel.",
        }.get(reason, reason)
        return "wachtend", readable
    return "wachtend", "Actief; wachten op een geschikt signaal."
