"""Kostenprofielen per broker.

Deze presets zijn een startpunt, geen waarheid. Spreads wijzigen, en de enige
cijfers die echt tellen zijn die van jouw eigen account op jouw eigen
handelstijden. Gebruik het ``/spread_stats``-endpoint van de bridge om de
preset te vervangen door gemeten waarden voordat je conclusies trekt uit de
bewijsfase.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .paper import BrokerCosts


@dataclass(slots=True, frozen=True)
class BrokerProfile:
    """Kostenprofiel plus de eigenaardigheden die de strategie moet kennen."""

    key: str
    name: str
    costs: BrokerCosts
    typical_spread: float
    #: Uren (UTC) waarin de spread structureel breder is en je beter niet handelt.
    wide_spread_hours: tuple[tuple[int, int], ...] = ()
    #: Vaste of variabele spread. Bij vaste spreads is de kostenkant
    #: voorspelbaar, wat scalping-planning makkelijker maakt.
    fixed_spread: bool = False
    #: Is dit een market maker (dealing desk)? Zo ja, dan is de broker jouw
    #: tegenpartij en niet slechts een doorgeefluik.
    market_maker: bool = False
    notes: str = ""


#: AvaTrade: commissievrij, vaste spreads, market maker.
#:
#: Het ontbreken van commissie klinkt gunstig maar is het niet: de kosten
#: zitten volledig in een spread die breder is dan bij raw-spread brokers met
#: commissie. Voor scalping telt alleen de som, en die valt hier hoger uit.
AVATRADE = BrokerProfile(
    key="avatrade",
    name="AvaTrade",
    costs=BrokerCosts(
        commission_per_lot_per_side=0.0,
        base_slippage=0.02,
        volatility_slippage_factor=0.05,
        size_slippage_per_lot=0.01,
        swap_long_per_lot=-8.0,
        swap_short_per_lot=-2.0,
        leverage=20.0,   # ESMA-plafond voor goud, particuliere klant
    ),
    typical_spread=0.35,
    wide_spread_hours=((22, 24), (0, 2)),
    fixed_spread=True,
    market_maker=True,
    notes=(
        "Commissievrij; alle kosten zitten in de spread (circa 0,34-0,37 USD/oz). "
        "Goud- en zilverspreads lopen uit tussen ongeveer 22:00 en 02:00 GMT. "
        "Scalping, hedging en Expert Advisors zijn toegestaan. "
        "Controleer trade_stops_level via de bridge: market makers hanteren vaak "
        "een minimale stopafstand die tight scalp-stops onmogelijk maakt."
    ),
)

#: Generiek raw-spread profiel, ter vergelijking.
RAW_SPREAD = BrokerProfile(
    key="raw_spread",
    name="Raw spread (generiek)",
    costs=BrokerCosts(commission_per_lot_per_side=3.50),
    typical_spread=0.12,
    fixed_spread=False,
    market_maker=False,
    notes="Smalle spread plus commissie. Round trip circa 0,19 USD/oz.",
)

PROFILES: dict[str, BrokerProfile] = {p.key: p for p in (AVATRADE, RAW_SPREAD)}


def round_trip_cost(profile: BrokerProfile, spread: float | None = None) -> float:
    """Totale kosten van een round trip in USD per ounce.

    Dit is het getal dat je strategie per trade moet overtreffen. Bij AvaTrade
    ligt het rond de 0,39; bij een raw-spread broker rond de 0,19. Dat verschil
    van een factor twee bepaalt of een scalpingstrategie kans maakt.
    """
    effective_spread = profile.typical_spread if spread is None else spread
    commission_per_oz = profile.costs.commission_per_lot_per_side * 2 / 100.0
    slippage = profile.costs.base_slippage * 2
    return effective_spread + commission_per_oz + slippage


def with_measured_spread(profile: BrokerProfile, measured: float) -> BrokerProfile:
    """Vervang de aangenomen spread door een gemeten waarde uit /spread_stats."""
    return replace(profile, typical_spread=measured)
