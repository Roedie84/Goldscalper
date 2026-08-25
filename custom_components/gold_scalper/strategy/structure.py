"""Marktstructuur: hogere toppen, lagere bodems, en het breken daarvan.

De bestaande signalen zijn allemaal gemiddelden - EMA's, een regressiehelling,
Bollinger-banden, RSI. Die zeggen iets over richting, maar niets over
structuur. Een markt kan boven zijn EMA liggen terwijl hij lagere toppen zet;
dat zijn twee verschillende uitspraken en de tweede is vaak de eerlijkere.

Wat hier wordt vastgesteld:

**De structuur zelf.** Opeenvolgende toppen en bodems worden vergeleken. Hogere
toppen én hogere bodems is een opgaande structuur; lagere toppen én lagere
bodems een dalende. Zijn ze het oneens - hogere toppen maar lagere bodems - dan
is er geen structuur en zit je in een verbreding of een range.

**Het breken ervan.** Zodra de koers voorbij de laatste relevante top of bodem
gaat, breekt de structuur. Dat is bruikbaarder dan een indicator die kruist,
omdat het aan een prijsniveau hangt dat je kunt aanwijzen.

**Karakterverandering.** Een break in de tegengestelde richting van de heersende
structuur is iets anders dan een break die hem voortzet. Het eerste kan een
omslag zijn, het tweede een bevestiging.

Een belangrijke beperking vooraf: pivots worden pas bevestigd als er aan beide
kanten genoeg candles liggen. Structuur wordt dus altijd met vertraging
vastgesteld. Dat is geen gebrek maar de prijs van niet elke wiebel als top
bestempelen; wie dat wel doet, ziet overal structuur die er niet is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..analysis.signals import Candles


class Structure(str, Enum):
    UPTREND = "uptrend"          # hogere toppen én hogere bodems
    DOWNTREND = "downtrend"      # lagere toppen én lagere bodems
    RANGE = "range"              # toppen en bodems spreken elkaar tegen
    UNKNOWN = "unknown"          # te weinig bevestigde pivots


@dataclass(slots=True)
class Pivot:
    index: int
    price: float
    is_high: bool


@dataclass(slots=True)
class StructureRead:
    """Uitkomst van de structuuranalyse."""

    structure: Structure
    score: float                      # -1 (dalend) tot +1 (stijgend)
    #: Break of structure: koers voorbij de laatste top of bodem.
    broke_up: bool = False
    broke_down: bool = False
    #: Break tegen de heersende structuur in: mogelijk een omslag.
    character_change: bool = False
    last_high: float | None = None
    last_low: float | None = None
    #: Niveaus waarop de structuur zou breken. Bruikbaar als stopniveau.
    invalidation: float | None = None
    highs: list[float] = field(default_factory=list)
    lows: list[float] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "structure": self.structure.value,
            "score": round(self.score, 3),
            "broke_up": self.broke_up,
            "broke_down": self.broke_down,
            "character_change": self.character_change,
            "last_high": self.last_high,
            "last_low": self.last_low,
            "invalidation": self.invalidation,
            "note": self.note,
        }


def find_pivots(candles: Candles, strength: int = 2) -> list[Pivot]:
    """Zoek bevestigde toppen en bodems.

    Een punt telt pas als pivot wanneer er ``strength`` candles aan beide
    kanten liggen die hem niet overtreffen. Hoe hoger die waarde, hoe minder
    pivots en hoe betrouwbaarder ze zijn - maar ook hoe later je ze ziet.
    """
    pivots: list[Pivot] = []
    n = len(candles)
    if n < strength * 2 + 1:
        return pivots

    for i in range(strength, n - strength):
        window = slice(i - strength, i + strength + 1)
        if candles.high[i] == max(candles.high[window]):
            # Bij gelijke waarden alleen de eerste nemen, anders krijg je een
            # rij identieke pivots op een vlak stuk.
            if not (pivots and pivots[-1].is_high and pivots[-1].index >= i - strength):
                pivots.append(Pivot(i, candles.high[i], True))
        if candles.low[i] == min(candles.low[window]):
            if not (pivots and not pivots[-1].is_high and pivots[-1].index >= i - strength):
                pivots.append(Pivot(i, candles.low[i], False))

    pivots.sort(key=lambda p: p.index)
    return pivots


def read_structure(
    candles: Candles, strength: int = 2, lookback: int = 60
) -> StructureRead:
    """Bepaal de marktstructuur en of hij zojuist gebroken is."""
    if len(candles) < strength * 2 + 4:
        return StructureRead(
            Structure.UNKNOWN, 0.0, note="te weinig candles voor structuur"
        )

    window = candles
    if len(candles) > lookback:
        start = len(candles) - lookback
        window = Candles(
            candles.timestamp[start:], candles.open[start:], candles.high[start:],
            candles.low[start:], candles.close[start:], candles.volume[start:],
        )

    pivots = find_pivots(window, strength)
    highs = [p.price for p in pivots if p.is_high]
    lows = [p.price for p in pivots if not p.is_high]

    if len(highs) < 2 or len(lows) < 2:
        return StructureRead(
            Structure.UNKNOWN, 0.0, highs=highs, lows=lows,
            last_high=highs[-1] if highs else None,
            last_low=lows[-1] if lows else None,
            note=f"slechts {len(highs)} toppen en {len(lows)} bodems bevestigd",
        )

    higher_high = highs[-1] > highs[-2]
    higher_low = lows[-1] > lows[-2]

    if higher_high and higher_low:
        structure, score = Structure.UPTREND, 1.0
        note = "hogere toppen en hogere bodems"
    elif not higher_high and not higher_low:
        structure, score = Structure.DOWNTREND, -1.0
        note = "lagere toppen en lagere bodems"
    else:
        structure, score = Structure.RANGE, 0.0
        note = (
            "hogere toppen maar lagere bodems: verbreding"
            if higher_high else
            "lagere toppen maar hogere bodems: samentrekking"
        )

    # Weeg mee hoe overtuigend de structuur is. Drie opeenvolgende hogere
    # toppen zegt meer dan twee, en een nipt verschil minder dan een duidelijk.
    if structure in (Structure.UPTREND, Structure.DOWNTREND) and len(highs) >= 3:
        consistent = (
            (highs[-2] > highs[-3]) if structure is Structure.UPTREND
            else (highs[-2] < highs[-3])
        )
        if not consistent:
            score *= 0.6
            note += ", maar de top daarvóór doorbrak dat patroon"

    price = candles.close[-1]
    last_high, last_low = highs[-1], lows[-1]

    broke_up = price > last_high
    broke_down = price < last_low
    character_change = (
        (broke_down and structure is Structure.UPTREND)
        or (broke_up and structure is Structure.DOWNTREND)
    )

    if broke_up:
        note += "; koers breekt boven de laatste top"
    elif broke_down:
        note += "; koers zakt onder de laatste bodem"
    if character_change:
        note += " — dat gaat tegen de heersende structuur in"

    # Waar de structuur ongeldig wordt: bruikbaar als stopniveau, want daar
    # klopt de aanname niet meer.
    if structure is Structure.UPTREND:
        invalidation = last_low
    elif structure is Structure.DOWNTREND:
        invalidation = last_high
    else:
        invalidation = None

    return StructureRead(
        structure=structure, score=score,
        broke_up=broke_up, broke_down=broke_down,
        character_change=character_change,
        last_high=last_high, last_low=last_low,
        invalidation=invalidation,
        highs=highs[-4:], lows=lows[-4:], note=note,
    )
