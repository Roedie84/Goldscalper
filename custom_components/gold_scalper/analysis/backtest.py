"""Backtest: de strategie over historische candles laten lopen.

**Het belangrijkste ontwerpbesluit.** Deze module bouwt de strategie niet na.
Hij roept dezelfde ``evaluate()`` aan en dezelfde ``ExitManager`` als de live
handelslus. Een backtest die de strategie herimplementeert, toetst de
herimplementatie - en die is per definitie een andere. Elke afwijking die je
dan meet, kan net zo goed in de nabouw zitten als in de markt.

**Wat hier wél gemodelleerd wordt.** Instap op bid of ask, spread als kosten,
slippage als aanname, stops getoetst tegen de uitersten binnen de bar in plaats
van tegen de slotkoers, en bij twijfel de ongunstige volgorde.

**Wat níet.** Spread die verbreedt rond nieuws, requotes, partiële fills,
latency tussen signaal en fill, en het feit dat je broker je orderflow ziet.
Een backtest valt daardoor stelselmatig gunstiger uit dan de werkelijkheid.
Reken op minder, niet op meer.

**En de grootste valkuil is niet technisch.** Een backtest die je gebruikt om
instellingen te kiezen, meet daarna niets meer: je hebt de uitkomst in de
keuze gestopt. Draai hem één keer, noteer de uitkomst, en verander daarna niets
op grond van wat je zag. Wie twintig varianten probeert en de beste kiest, heeft
de beste van twintig ruisuitkomsten gekozen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..analysis.signals import Candles
from ..broker.exits import ExitConfig, ExitManager
from ..strategy.scalping import ScalpConfig, evaluate

_LOGGER = logging.getLogger(__name__)

#: Aantal candles dat de indicatoren nodig hebben voordat er gehandeld wordt.
WARMUP_BARS = 300


@dataclass(slots=True)
class BacktestTrade:
    opened_at: int
    closed_at: int
    side: str
    units: float
    entry: float
    exit: float
    entry_mid: float
    exit_mid: float
    gross: float
    cost: float
    net: float
    reason: str
    score: float
    regime: str | None = None
    mae: float = 0.0
    mfe: float = 0.0

    def as_dict(self) -> dict:
        return {
            "opened_at": self.opened_at, "closed_at": self.closed_at,
            "side": self.side, "units": round(self.units, 3),
            "entry": round(self.entry, 3), "exit": round(self.exit, 3),
            "gross": round(self.gross, 2), "cost": round(self.cost, 2),
            "net": round(self.net, 2), "reason": self.reason,
            "score": round(self.score, 3), "regime": self.regime,
        }


@dataclass(slots=True)
class BacktestResult:
    bars: int = 0
    evaluations: int = 0
    trades: list[BacktestTrade] = field(default_factory=list)
    rejections: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def net_pnl(self) -> float:
        return sum(t.net for t in self.trades)

    @property
    def gross_pnl(self) -> float:
        return sum(t.gross for t in self.trades)

    @property
    def total_costs(self) -> float:
        return sum(t.cost for t in self.trades)

    def summary(self) -> dict:
        wins = [t for t in self.trades if t.net > 0]
        losses = [t for t in self.trades if t.net <= 0]
        return {
            "bars": self.bars,
            "evaluations": self.evaluations,
            "trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (
                round(len(wins) / len(self.trades) * 100, 1)
                if self.trades else 0.0
            ),
            "gross_pnl": round(self.gross_pnl, 2),
            "total_costs": round(self.total_costs, 2),
            "net_pnl": round(self.net_pnl, 2),
            "cost_ratio": (
                round(self.total_costs / abs(self.gross_pnl), 3)
                if self.gross_pnl else None
            ),
            "rejections": dict(
                sorted(self.rejections.items(), key=lambda kv: -kv[1])
            ),
            "warnings": self.warnings,
        }


def run_backtest(
    candles: Candles,
    strategy: ScalpConfig,
    exits: ExitConfig | None = None,
    *,
    spread: float = 0.60,
    slippage: float = 0.02,
    units: float = 1.0,
    bar_seconds: int = 300,
) -> BacktestResult:
    """Loop bar voor bar door de historie met de échte strategiecode."""
    result = BacktestResult(bars=len(candles))
    if len(candles) < WARMUP_BARS + 10:
        result.warnings.append(
            f"{len(candles)} bars; er zijn er minstens {WARMUP_BARS + 10} nodig "
            "voordat de indicatoren iets zeggen."
        )
        return result

    manager = ExitManager(exits or ExitConfig())
    half = spread / 2.0
    round_trip = spread + 2 * slippage

    open_trade: dict | None = None

    for i in range(WARMUP_BARS, len(candles) - 1):
        window = Candles(
            candles.timestamp[:i + 1], candles.open[:i + 1],
            candles.high[:i + 1], candles.low[:i + 1],
            candles.close[:i + 1], candles.volume[:i + 1],
        )
        mid = candles.close[i]
        bid, ask = mid - half, mid + half
        moment = datetime.fromtimestamp(candles.timestamp[i], timezone.utc)

        # --- lopende positie beheren --------------------------------------- #
        if open_trade is not None:
            nxt = i + 1
            high, low = candles.high[nxt], candles.low[nxt]
            long = open_trade["side"] == "buy"
            direction = 1.0 if long else -1.0

            # Uitersten binnen de volgende bar, niet de slotkoers. Op de
            # slotkoers toetsen mist stops die geraakt werden en daarna
            # herstelden - allemaal in je voordeel, wat het resultaat
            # stelselmatig te mooi maakt.
            worst = (low - half) if long else (high + half)
            best = (high - half) if long else (low + half)
            open_trade["mae"] = min(
                open_trade["mae"], (worst - open_trade["entry"]) * direction
            )
            open_trade["mfe"] = max(
                open_trade["mfe"], (best - open_trade["entry"]) * direction
            )

            stop_hit = open_trade["stop"] is not None and (
                worst <= open_trade["stop"] if long else worst >= open_trade["stop"]
            )
            target_hit = open_trade["target"] is not None and (
                best >= open_trade["target"] if long else best <= open_trade["target"]
            )

            # Zijn beide binnen dezelfde bar geraakt, dan valt uit een candle
            # niet af te leiden welke eerst kwam. De stop aannemen is de enige
            # verdedigbare keuze; gokken op de gunstige volgorde is precies hoe
            # een backtest zichzelf rijk rekent.
            # De mid die bij de exitprijs hoort, niet de slotkoers van de bar.
            # Die twee door elkaar halen maakte de kosten negatief: bruto werd
            # op een heel ander prijsniveau berekend dan netto.
            if stop_hit:
                level = open_trade["stop"]
                _close(result, open_trade, level, "stop_loss",
                       candles.timestamp[nxt], level + (half if long else -half),
                       units, round_trip)
                open_trade = None
            elif target_hit:
                level = open_trade["target"]
                _close(result, open_trade, level, "take_profit",
                       candles.timestamp[nxt], level + (half if long else -half),
                       units, round_trip)
                open_trade = None
            else:
                action = manager.evaluate(
                    side=open_trade["side"], volume=units / 100.0,
                    open_price=open_trade["entry"],
                    current_stop=open_trade["stop"],
                    bid=bid, ask=ask,
                    atr=open_trade["atr"],
                    opened_at=open_trade["opened"],
                    now=moment,
                    round_trip_cost_per_oz=round_trip,
                    partial_taken=open_trade["partial"],
                )
                if action.kind == "close":
                    exit_price = bid if long else ask
                    _close(result, open_trade, exit_price, action.reason[:40],
                           candles.timestamp[i], mid, units, round_trip)
                    open_trade = None
                elif action.kind == "modify_stop":
                    open_trade["stop"] = action.new_stop
                elif action.kind == "partial_close":
                    open_trade["partial"] = True

        # --- signaal zoeken ------------------------------------------------- #
        if open_trade is None:
            result.evaluations += 1
            signal = evaluate(
                window, bid, ask, strategy, moment.hour, 0, 1e9
            )
            if not signal.should_trade:
                key = signal.reject_reason or "onbekend"
                result.rejections[key] = result.rejections.get(key, 0) + 1
                continue

            long = signal.direction == 1
            entry = (ask if long else bid) + (slippage if long else -slippage)
            open_trade = {
                "side": "buy" if long else "sell",
                "entry": entry,
                "entry_mid": mid,
                "stop": signal.stop_loss,
                "target": signal.take_profit,
                "atr": signal.components.get("atr", 1.0),
                "opened": moment,
                "opened_ts": candles.timestamp[i],
                "score": signal.score,
                "regime": signal.components.get("regime"),
                "partial": False,
                "mae": 0.0,
                "mfe": 0.0,
            }

    if open_trade is not None:
        result.warnings.append(
            "De laatste positie stond bij het einde van de data nog open en is "
            "niet meegeteld."
        )
    return result


def _close(
    result: BacktestResult, trade: dict, exit_price: float, reason: str,
    closed_ts: int, exit_mid: float, units: float, round_trip: float,
) -> None:
    direction = 1.0 if trade["side"] == "buy" else -1.0
    gross = (exit_mid - trade["entry_mid"]) * direction * units
    net = (exit_price - trade["entry"]) * direction * units
    result.trades.append(BacktestTrade(
        opened_at=trade["opened_ts"], closed_at=closed_ts,
        side=trade["side"], units=units,
        entry=trade["entry"], exit=exit_price,
        entry_mid=trade["entry_mid"], exit_mid=exit_mid,
        gross=gross, cost=gross - net, net=net,
        reason=reason, score=trade["score"], regime=trade["regime"],
        mae=trade["mae"], mfe=trade["mfe"],
    ))
