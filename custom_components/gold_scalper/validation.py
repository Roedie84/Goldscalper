"""Controles op instellingen die technisch geldig zijn maar zelden bedoeld.

Los van de config flow gehouden. Die klasse erft van Home Assistant en is
daardoor niet importeerbaar zonder draaiende HA, terwijl dit pure rekenkunde is
die je juist wél wilt kunnen testen - dezelfde reden waarom de statustekst een
eigen module heeft.
"""

from __future__ import annotations


def reward_risk_warning(user_input: dict, atr: float | None) -> str | None:
    """Waarschuw als de stop groter is dan het doel.

    Dat mag: er zijn strategieën die van een hoge trefkans leven. Maar de
    gevolgen zijn niet vanzelfsprekend. Een doel van 5 tegen een stop van 6,5
    vereist 57% winnaars om quitte te spelen, en met kosten erbij 64% - tegen
    45% bij de standaardverhouding. Wie die keuze maakt hoort dat getal te zien
    in plaats van het te ontdekken na tweehonderd trades.
    """
    target_usd = user_input.get("take_profit_usd") or 0.0
    stop_usd = user_input.get("stop_loss_usd") or 0.0
    target_atr = user_input.get("take_profit_atr") or 0.0
    stop_atr = user_input.get("stop_loss_atr") or 0.0

    # Een vast bedrag en een ATR-multiplier zijn onvergelijkbaar zonder de ATR.
    # Dan liever niets zeggen dan iets verkeerds.
    mixed = (target_usd > 0) != (stop_usd > 0)
    if mixed and not atr:
        return None

    target = target_usd or (atr or 0.0) * target_atr
    stop = stop_usd or (atr or 0.0) * stop_atr
    if target <= 0 or stop <= 0:
        return None

    ratio = target / stop
    if ratio >= 1.0:
        return None

    needed = stop / (target + stop) * 100
    return (
        f"Let op: je doel ({target:.2f}) is kleiner dan je stop ({stop:.2f}), "
        f"een verhouding van {ratio:.2f} op 1. Je moet dan minstens "
        f"{needed:.0f}% van je trades winnen om quitte te spelen, nog vóór "
        "kosten. Bij de standaardverhouding van 1,5 op 1 is dat 40%. "
        "Dit kan een bewuste keuze zijn, maar het is er wel een."
    )
