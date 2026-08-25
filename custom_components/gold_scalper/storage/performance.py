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
from datetime import datetime, timezone
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
        "cost_ratio": (
            round(cost_total / abs(gross_total), 3) if gross_total else None
        ),
        "cost_per_trade": round(_safe(cost_total, len(closed)), 4),

        # None in plaats van oneindig: Infinity is geen geldige JSON en breekt
        # strikte parsers, waaronder sommige dashboardtools. Zonder verliezers
        # ís er geen verhouding, en dat is eerlijker dan een oneindig getal.
        "profit_factor": (
            round(gross_profit / gross_loss, 3) if gross_loss > 0 else None
        ),
        "expectancy": round(mean_net, 4),
        "avg_win": round(_safe(sum(wins), len(wins)), 4),
        "avg_loss": round(_safe(sum(losses), len(losses)), 4),
        # Bij nul verliezers is er geen grootste verlies. min(nets) zou dan de
        # kleinste winst teruggeven en die als verlies presenteren.
        "largest_win": round(max(wins), 2) if wins else 0.0,
        "largest_loss": round(min(losses), 2) if losses else 0.0,

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
    if stats["cost_ratio"] is not None and stats["cost_ratio"] >= 1.0:
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
    if stats["profit_factor"] is not None and stats["profit_factor"] < 1.2:
        reasons.append(f"profit factor {stats['profit_factor']} is te laag voor marge")
    elif stats["profit_factor"] is None and stats["losses"] == 0:
        # Geen enkele verliezer over honderden trades is geen goede strategie
        # maar een waarschuwingssignaal: meestal een fout in de kostenboeking,
        # de exitlogica of de simulatie. Dit ongemerkt laten passeren zou de
        # poort openzetten op basis van een boekhoudfout.
        reasons.append(
            f"geen enkele verliezende trade over {trades} trades; dat wijst op "
            "een fout in de administratie of exitlogica, niet op een edge"
        )
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


def cost_projection(
    trades: Sequence[Trade], spreads: Sequence[float] = (0.12, 0.25, 0.35, 0.50)
) -> list[dict]:
    """Wat zou het resultaat zijn geweest bij deze spreads?

    Bestaat omdat een run met kosten uitgeschakeld anders een winstcijfer
    oplevert dat nergens op slaat. Door hetzelfde tradepatroon door te rekenen
    bij realistische spreads blijft zichtbaar hoeveel van die winst zou
    overleven - en meestal is dat weinig.

    De aanname is dat de trades identiek zouden zijn geweest. In werkelijkheid
    zou een hogere spread ook de kostenpoort strenger maken en dus minder
    trades opleveren, dus dit is een *optimistische* schatting. De echte
    uitkomst valt slechter uit, niet beter.
    """
    closed = [t for t in trades if t.gross_pnl is not None]
    if not closed:
        return []

    ounces = sum((t.volume or 0) * 100.0 for t in closed)
    gross = sum(t.gross_pnl or 0.0 for t in closed)
    actual_costs = sum(t.total_cost or 0.0 for t in closed)

    out = [{
        "spread": None,
        "label": "zoals gedraaid",
        "costs": round(actual_costs, 2),
        "net_pnl": round(gross - actual_costs, 2),
        "profitable": (gross - actual_costs) > 0,
    }]
    for spread in spreads:
        # Spread wordt één keer per round trip betaald, over het aantal ounces.
        costs = spread * ounces
        out.append({
            "spread": spread,
            "label": f"bij spread {spread:.2f}",
            "costs": round(costs, 2),
            "net_pnl": round(gross - costs, 2),
            "profitable": (gross - costs) > 0,
        })
    return out


def compute_for_run(
    db: TradeDatabase, run_id: int, trades: Sequence[Trade] | None = None
) -> dict:
    """Metrieken voor één run, inclusief de signaalstatistiek.

    ``trades`` kan meegegeven worden als de aanroeper ze al heeft. Zonder dat
    werden ze hier twee keer ingelezen, en omdat de coordinator deze functie
    elke cyclus aanroept liep dat bij duizenden trades op tot honderden
    milliseconden per twintig seconden - aan het herhaaldelijk inlezen van
    precies dezelfde rijen.
    """
    run = db.get_run(run_id)
    if not run:
        return {"trades": 0, "verdict": "no_data", "verdict_text": "Run bestaat niet."}
    if trades is None:
        trades = db.closed_trades(run_id)
    stats = compute(trades, run["starting_balance"])
    stats["run_id"] = run_id
    stats["mode"] = run["mode"]
    stats["strategy_version"] = run["strategy_version"]
    stats["started_at"] = run["started_at"]
    stats["signals"] = db.signal_stats(run_id)
    stats["cost_projection"] = cost_projection(trades)

    from ..learning.postmortem import analyse_losses
    stats["losses"] = analyse_losses(trades).as_dict()

    # Markeer expliciet als er zonder kosten gedraaid is. Zonder dit ziet een
    # nulkosten-run er in het rapport uit als een gewone winstgevende run.
    if stats.get("trades") and (stats.get("total_costs") or 0) <= 0:
        stats["costs_disabled"] = True
        stats["verdict_text"] = (
            "Deze run draaide zonder transactiekosten. Het resultaat is "
            "daardoor fictief: in de echte markt betaal je bij elke trade de "
            "spread. Zie de kostenprojectie voor wat er zou overblijven."
        )
    return stats


def daily_breakdown(trades: Sequence[Trade], tz=None) -> list[dict]:
    """Resultaat per dag. Nuttig om te zien of winst uit één uitschieter komt.

    Groepeert op lokale kalenderdag als er een tijdzone is meegegeven. Op UTC
    groeperen zou een trade van 01:30 Nederlandse tijd op de vorige dag zetten,
    en dan telt je 'handelsdagen'-criterium verkeerd.
    """
    buckets: dict[str, list[Trade]] = {}
    for t in trades:
        if not t.close_time or t.net_pnl is None:
            continue
        moment = datetime.fromisoformat(t.close_time)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        if tz is not None:
            moment = moment.astimezone(tz)
        day = moment.date().isoformat()
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
