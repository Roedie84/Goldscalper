"""Resultaat per handelssessie en rond publicatietijden.

Onderzoek naar goudfutures vindt dat niet-geïnformeerde handel overheerst in de
Aziatische sessie en geïnformeerde handel domineert in de New Yorkse. Of dat
voor deze strategie uitmaakt valt niet te beredeneren maar wel te meten.

Uitsluitend observatie: er wordt niets gefilterd. Bij vijftig trades per sessie
is elk verschil ruis, en een uitsplitsing in drieën vindt bijna altijd een
'beste' groep - ook in zuivere ruis.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.learning.sessions import (
    MIN_PER_SESSION, build_news_impact, build_sessions, in_news_window,
    session_of,
)
from gold_scalper.storage.database import Trade


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 16, hour, minute, tzinfo=timezone.utc)


def _trade(uur: int, net: float, minuut: int = 0):
    moment = _at(uur, minuut)
    return Trade(
        run_id=1, mode="demo", symbol="GOLD", side="buy", volume=0.013,
        open_time=moment.isoformat(), open_price=4400.0, open_mid=4400.0,
        open_spread=0.6, close_time=(moment + timedelta(minutes=20)).isoformat(),
        net_pnl=net, gross_pnl=net + 1.0, total_cost=1.0,
    )


# ---------------- sessie-indeling ----------------

@pytest.mark.parametrize("uur,verwacht", [
    (0, "azie"), (3, "azie"), (6, "azie"),
    (7, "londen"), (10, "londen"), (12, "londen"),
    (13, "newyork"), (18, "newyork"), (22, "newyork"),
    (23, "azie"),
])
def test_sessions_cover_the_clock(uur, verwacht):
    assert session_of(_at(uur, 30)) == verwacht


def test_sessions_are_in_utc():
    """Sessies volgen de beurzen, niet de Nederlandse klok, en schuiven dus
    niet mee met onze zomertijd."""
    from zoneinfo import ZoneInfo

    ams = datetime(2026, 9, 16, 15, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
    assert session_of(ams) == "newyork"     # 13:00 UTC


# ---------------- uitsplitsing ----------------

def test_trades_land_in_the_right_session():
    report = build_sessions([
        _trade(3, 5.0), _trade(10, -2.0), _trade(15, 3.0),
    ])
    per_naam = {s.name: s for s in report.sessions}
    assert per_naam["azie"].trades == 1
    assert per_naam["londen"].trades == 1
    assert per_naam["newyork"].trades == 1


def test_thin_sessions_get_no_verdict():
    """Bij vijftig trades per sessie is elk verschil ruis."""
    report = build_sessions([_trade(3, 5.0) for _ in range(10)])
    assert "nodig" in report.conclusion


def test_the_report_warns_against_acting_on_noise():
    """Een uitsplitsing in drieën vindt bijna altijd een 'beste' groep."""
    import random

    random.seed(4)
    trades = []
    for uur in (3, 10, 15):
        for i in range(MIN_PER_SESSION + 5):
            trades.append(_trade(uur, random.gauss(0.05, 11.0), i % 60))

    report = build_sessions(trades)
    assert "toeval" in report.conclusion or "ruis" in report.conclusion


def test_sessions_report_their_own_t_statistic():
    """Zonder die waarde is een verschil tussen sessies niet te wegen."""
    report = build_sessions([_trade(3, 5.0, i % 60) for i in range(40)])
    data = report.as_dict()["sessies"][0]
    assert "t" in data and data["betrouwbaar"] is True


# ---------------- nieuwsvensters ----------------

@pytest.mark.parametrize("uur,minuut,verwacht", [
    (13, 30, True),    # Amerikaanse macrocijfers
    (13, 20, False),
    (19, 0, True),     # rentebesluit
    (19, 30, True),    # persconferentie
    (10, 0, False),
])
def test_news_windows_are_clock_based(uur, minuut, verwacht):
    """Geen externe agenda: die vraagt een sleutel, een netwerkverbinding en
    een dienst die kan omvallen, terwijl het venster met de klok af te bakenen
    is."""
    assert bool(in_news_window(_at(uur, minuut))) is verwacht


def test_news_impact_splits_on_the_entry():
    """Het venster gaat over de omstandigheden waaronder je de positie
    opende, niet waaronder hij sloot."""
    impact = build_news_impact([_trade(13, 5.0, 30), _trade(10, -2.0)])
    assert impact.in_window.trades == 1
    assert impact.outside.trades == 1


def test_news_impact_needs_enough_trades():
    impact = build_news_impact([_trade(13, 5.0, 30)])
    assert "nodig" in impact.as_dict()["conclusie"]


def test_a_small_difference_is_called_small():
    """Voorkomt dat een verschil van een paar cent als bevinding wordt
    gepresenteerd."""
    trades = (
        [_trade(13, 0.10, i % 20 + 25) for i in range(MIN_PER_SESSION + 5)]
        + [_trade(10, 0.05, i % 60) for i in range(MIN_PER_SESSION + 5)]
    )
    impact = build_news_impact(trades)
    assert "Te klein" in impact.as_dict()["conclusie"]


def test_nothing_is_filtered():
    """De hele module is observatie. Vangt een toekomstige poging om hier een
    filter van te maken."""
    from pathlib import Path

    bron = (Path(__file__).resolve().parent.parent / "custom_components"
            / "gold_scalper" / "learning" / "sessions.py").read_text(encoding="utf-8")
    for verboden in ("reject", "should_trade", "blocked", "skip_session"):
        assert verboden not in bron
