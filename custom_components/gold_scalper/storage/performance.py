"""Prestatieanalyse over de tradedatabase.

De metrieken zijn zo gekozen dat ze moeilijk te flatteren zijn. Drie ervan
verdienen toelichting, omdat ze in de meeste trading-dashboards ontbreken juist
omdat ze onaangenaam zijn:

``cost_ratio``
    Totale kosten gedeeld door de absolute bruto winst. Boven de 1,0 betekent
    dat de kosten groter zijn dan alles wat de strategie aan marktbeweging
    heeft gevangen — de strategie kan dan gelijk hebben over de richting en
    tóch structureel verliezen.

``breakeven_edge``
    Hoeveel USD per ounce je gemiddeld per trade moet vangen om quitte te
    spelen. Vergelijk dit met ``avg_gross_excursion``: als de gemiddelde
    beweging die je vangt kleiner is dan deze drempel, is de strategie
    wiskundig kansloos, ongeacht de winstpercentage.

``t_statistic``
    Of het resultaat te onderscheiden is van toeval. Bij honderden trades per
    dag levert willekeur al snel indrukwekkend ogende reeksen op. Onder de 2,0
    is er geen statistisch bewijs van een edge.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Sequence

from .database import Trade, TradeDatabase

#: Minimaal aantal trades voordat statistieken betekenis krijgen.
MIN_TRADES_FOR_SIGNIFICANCE = 100
#: Drempel waarboven we het resultaat niet meer aan toeval toeschrijven.
T_STAT_THRESHOLD = 2.0


def _safe(numerator: float, denominator: float, default: float = 0.0) -> float:
    return numerator / denominator if denominator else default


def compute(trades: Sequence[Trade], starting_balance: float = 10_000.0) -> dict:
    """Bereken alle prestatiemetrieken over een reeks gesloten trades."""
    closed = [t for t in trades if t.net_pnl is not None]
    if not closed:
        return {
            "trades": 0,
            "verdict": "no_data",
            "verdict_text": "Nog geen gesloten trades.",
        }

    nets = [t.net_pnl for t in closed]
    grosses = [t.gross_pnl or 0.0 for t in closed]
    costs = [t.total_cost or 0.0 for t in closed]

    wins = [p for p in nets if p > 0]
    losses = [p for p in nets if p < 0]

    net_total = sum(nets)
    gross_total = sum(grosses)
    cost_total = sum(costs)

    gross_profit = sum(p for p in nets if p > 0)
    gross_loss = abs(sum(p for p in nets if p < 0))

    # Equity curve en drawdown, op basis van de volgorde van sluiten.
    equity = starting_balance
    curve = [equity]
    for pnl in nets:
        equity += pnl
        curve.append(equity)
    peak = curve[0]
    max_dd = 0.0
    max_dd_pct = 0.0
    for value in curve:
        peak = max(peak, value)
        drawdown = peak - value
        if drawdown > max_dd:
            max_dd = drawdown
            max_dd_pct = _safe(drawdown, peak) * 100.0

    mean_net = net_total / len(nets)
    if len(nets) > 1:
        variance = sum((p - mean_net) ** 2 for p in nets) / (len(nets) - 1)
        sd = math.sqrt(variance)
    else:
        sd = 0.0

    # t-statistiek voor "is het gemiddelde resultaat te onderscheiden van nul".
    t_stat = _safe(mean_net, sd / math.sqrt(len(nets))) if sd > 0 else 0.0

    # Gemiddelde beweging die per trade gevangen wordt, in USD per ounce.
    volumes = [t.volume * 100.0 for t in closed]
    avg_excursion = _safe(
        sum(abs(g) for g in grosses), sum(volumes)
    )
    breakeven_edge = _safe(cost_total, sum(volumes))

    durations = [t.duration_seconds for t in closed if t.duration_seconds is not None]
    reasons: dict[str, int] = {}
    for t in closed:
        if t.close_reason:
            reasons[t.close_reason] = reasons.get(t.close_reason, 0) + 1

    result = {
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(_safe(len(wins), len(closed)) * 100.0, 2),

        "net_pnl": round(net_total, 2),
        "gross_pnl": round(gross_total, 2),
        "total_costs": round(cost_total, 2),
        "cost_ratio": round(_safe(cost_total, abs(gross_total), float("inf")), 3),
        "cost_per_trade": round(_safe(cost_total, len(closed)), 4),

        "profit_factor": round(_safe(gross_profit, gross_loss, float("inf")), 3),
        "expectancy": round(mean_net, 4),
        "avg_win": round(_safe(sum(wins), len(wins)), 4),
        "avg_loss": round(_safe(sum(losses), len(losses)), 4),
        "largest_win": round(max(nets), 2),
        "largest_loss": round(min(nets), 2),

        "return_pct": round(_safe(net_total, starting_balance) * 100.0, 3),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "final_balance": round(starting_balance + net_total, 2),

        "avg_excursion_per_oz": round(avg_excursion, 4),
        "breakeven_edge_per_oz": round(breakeven_edge, 4),
        "edge_surplus_per_oz": round(avg_excursion - breakeven_edge, 4),

        "t_statistic": round(t_stat, 3),
        "std_dev": round(sd, 4),
        "avg_duration_seconds": round(_safe(sum(durations), len(durations)), 1),
        "close_reasons": reasons,
    }
    result.update(verdict(result))
    return result


def verdict(stats: dict) -> dict:
    """Vertaal de cijfers naar een expliciet oordeel over de bewijsfase.

    Dit is bewust streng en bewust niet onderhandelbaar vanuit de UI. Het punt
    van een bewijsfase is dat de uitkomst kan zijn dat er niet gehandeld moet
    worden; een drempel die je kunt verlagen tot hij groen wordt, bewijst niets.
    """
    trades = stats["trades"]
    if trades < MIN_TRADES_FOR_SIGNIFICANCE:
        return {
            "verdict": "insufficient_data",
            "verdict_text": (
                f"{trades} trades: te weinig om iets te concluderen. "
                f"Minimaal {MIN_TRADES_FOR_SIGNIFICANCE} nodig, en bij "
                "sub-minuut handel liever een paar duizend."
            ),
            "ready_for_live": False,
        }

    reasons: list[str] = []
    if stats["net_pnl"] <= 0:
        reasons.append("netto resultaat is niet positief")
    if stats["cost_ratio"] >= 1.0:
        reasons.append(
            f"kosten ({stats['total_costs']:.0f}) overtreffen de bruto marktbeweging "
            f"die is gevangen ({abs(stats['gross_pnl']):.0f})"
        )
    if stats["edge_surplus_per_oz"] <= 0:
        reasons.append(
            f"de gemiddeld gevangen beweging ({stats['avg_excursion_per_oz']:.3f} USD/oz) "
            f"ligt onder de break-evendrempel ({stats['breakeven_edge_per_oz']:.3f} USD/oz)"
        )
    if stats["t_statistic"] < T_STAT_THRESHOLD:
        reasons.append(
            f"t-statistiek {stats['t_statistic']:.2f} ligt onder {T_STAT_THRESHOLD}: "
            "het resultaat is niet te onderscheiden van toeval"
        )
    if stats["profit_factor"] < 1.2:
        reasons.append(f"profit factor {stats['profit_factor']} is te laag voor marge")
    if stats["max_drawdown_pct"] > 25:
        reasons.append(f"drawdown van {stats['max_drawdown_pct']}% is te groot")

    if not reasons:
        return {
            "verdict": "passed",
            "verdict_text": (
                f"Over {trades} trades: netto {stats['net_pnl']:+.2f}, "
                f"profit factor {stats['profit_factor']}, t={stats['t_statistic']}. "
                "Statistisch houdbaar op papier. Dat is geen garantie voor live "
                "uitvoering: paper kent geen requotes, geen spreadverbreding rond "
                "nieuws en geen latency van jouw eigen infrastructuur."
            ),
            "ready_for_live": True,
            "blocking_reasons": [],
        }

    return {
        "verdict": "failed",
        "verdict_text": (
            f"Over {trades} trades voldoet de strategie niet: " + "; ".join(reasons) + "."
        ),
        "ready_for_live": False,
        "blocking_reasons": reasons,
    }


def compute_for_run(db: TradeDatabase, run_id: int) -> dict:
    """Metrieken voor één run, inclusief de signaalstatistiek."""
    run = db.get_run(run_id)
    if not run:
        return {"trades": 0, "verdict": "no_data", "verdict_text": "Run bestaat niet."}
    stats = compute(db.closed_trades(run_id), run["starting_balance"])
    stats["run_id"] = run_id
    stats["mode"] = run["mode"]
    stats["strategy_version"] = run["strategy_version"]
    stats["started_at"] = run["started_at"]
    stats["signals"] = db.signal_stats(run_id)
    return stats


def daily_breakdown(trades: Sequence[Trade]) -> list[dict]:
    """Resultaat per dag. Nuttig om te zien of winst uit één uitschieter komt."""
    buckets: dict[str, list[Trade]] = {}
    for t in trades:
        if not t.close_time or t.net_pnl is None:
            continue
        day = datetime.fromisoformat(t.close_time).date().isoformat()
        buckets.setdefault(day, []).append(t)

    out = []
    for day in sorted(buckets):
        group = buckets[day]
        nets = [t.net_pnl for t in group]
        out.append({
            "date": day,
            "trades": len(group),
            "net_pnl": round(sum(nets), 2),
            "costs": round(sum(t.total_cost or 0 for t in group), 2),
            "win_rate": round(_safe(len([p for p in nets if p > 0]), len(nets)) * 100, 1),
        })
    return out
