"""Gemeenschappelijk datamodel voor indicatoruitkomsten."""

from __future__ import annotations

from dataclasses import dataclass, field

CATEGORY_TREND = "trend"
CATEGORY_MOMENTUM = "momentum"
CATEGORY_VOLATILITY = "volatility"
CATEGORY_VOLUME = "volume"
CATEGORY_PATTERN = "pattern"
CATEGORY_STATISTICAL = "statistical"

ALL_CATEGORIES = (
    CATEGORY_TREND,
    CATEGORY_MOMENTUM,
    CATEGORY_VOLATILITY,
    CATEGORY_VOLUME,
    CATEGORY_PATTERN,
    CATEGORY_STATISTICAL,
)


@dataclass(slots=True)
class Signal:
    """Uitkomst van één indicator.

    ``score`` loopt van -1 (sterk bearish) tot +1 (sterk bullish). ``weight``
    bepaalt hoe zwaar de indicator meetelt in de samengestelde score; die wordt
    door de regime-detectie dynamisch aangepast.

    ``rationale`` is bewust verplicht. Een score zonder onderbouwing is precies
    het soort black box waar je niets aan hebt als je achteraf wilt begrijpen
    waarom een signaal afging.
    """

    key: str
    category: str
    label: str
    value: float | str | None
    score: float
    weight: float
    rationale: str
    extra: dict[str, float | str | None] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "category": self.category,
            "label": self.label,
            "value": self.value,
            "score": round(self.score, 4),
            "weight": round(self.weight, 3),
            "contribution": round(self.score * self.weight, 4),
            "rationale": self.rationale,
            **({"extra": self.extra} if self.extra else {}),
        }


@dataclass(slots=True)
class Candles:
    """OHLCV-reeks, oplopend in tijd. Laatste element is de meest recente."""

    timestamp: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]

    def __len__(self) -> int:
        return len(self.close)

    @property
    def hlc3(self) -> list[float]:
        """Typical price."""
        return [(h + l + c) / 3.0 for h, l, c in zip(self.high, self.low, self.close)]

    @property
    def hl2(self) -> list[float]:
        return [(h + l) / 2.0 for h, l in zip(self.high, self.low)]

    @property
    def ohlc4(self) -> list[float]:
        return [
            (o + h + l + c) / 4.0
            for o, h, l, c in zip(self.open, self.high, self.low, self.close)
        ]

    def validate(self) -> None:
        """Basisintegriteit. Slechte data van een exchange is geen zeldzaamheid."""
        lengths = {
            len(self.timestamp),
            len(self.open),
            len(self.high),
            len(self.low),
            len(self.close),
            len(self.volume),
        }
        if len(lengths) != 1:
            raise ValueError(f"OHLCV-kolommen hebben ongelijke lengtes: {lengths}")
        for i in range(len(self.close)):
            if self.high[i] < self.low[i]:
                raise ValueError(f"Candle {i}: high < low")
            if not (self.low[i] <= self.close[i] <= self.high[i]):
                raise ValueError(f"Candle {i}: close ligt buiten de high/low range")
        for i in range(1, len(self.timestamp)):
            if self.timestamp[i] <= self.timestamp[i - 1]:
                raise ValueError(f"Candle {i}: timestamps niet strikt oplopend")
