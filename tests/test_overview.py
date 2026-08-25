"""De overzichtspagina.

Het keuringsrapport is bedoeld om te bestuderen; deze pagina beantwoordt de
vraag "gebeurt er nog iets" op een telefoon, in één blik.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.dashboard.overview import build_overview

REPORT = "/api/gold_scalper/report"


def data(**over):
    base = {
        "status": ("wachtend", "Actief; wachten op een geschikt signaal."),
        "price": 4642.5, "spread": 0.6, "symbol": "CS.D.CFEGOLD.CEA.IP",
        "mode": "paper",
        "stats": {
            "net_pnl": 12.5, "total_costs": 3.2, "trades": 8, "run_id": 21,
            "signals": {"evaluations": 1098, "acted": 8,
                        "rejections": {"score_below_threshold": 900}},
        },
        "gate": {"checks": {"echte_marktdata": True, "genoeg_trades": False}},
        "warmup": None,
    }
    base.update(over)
    return base


def test_page_is_valid_and_self_contained():
    html = build_overview(data(), REPORT)
    assert html.startswith("<!DOCTYPE html>")
    assert "<script" not in html
    assert "cdn." not in html


def test_viewport_is_set_for_mobile():
    """Zonder viewport-tag rendert een telefoon de pagina op desktopbreedte
    en moet je knijpen om iets te lezen."""
    html = build_overview(data(), REPORT)
    assert 'name="viewport"' in html
    assert "width=device-width" in html


def test_body_can_scroll_in_an_iframe():
    """iOS Safari schaalt een iframe naar de inhoudshoogte en scrollt niet,
    tenzij de body dat expliciet mag."""
    html = build_overview(data(), REPORT)
    assert "overflow-y:auto" in html
    assert "-webkit-overflow-scrolling:touch" in html


def test_nothing_forces_horizontal_overflow():
    """Eén element breder dan het scherm kantelt de hele weergave."""
    html = build_overview(data(), REPORT)
    assert "overflow-x:hidden" in html
    assert "white-space:nowrap" not in html


def test_report_is_one_click_away():
    html = build_overview(data(), REPORT)
    assert f'href="{REPORT}"' in html
    assert "keuringsrapport" in html.lower()


def test_key_numbers_are_present():
    html = build_overview(data(), REPORT)
    for fragment in ("12.50", "8 trades", "4 642.50", "1098"):
        assert fragment in html, fragment


def test_negative_result_is_marked():
    html = build_overview(data(stats={
        "net_pnl": -4.2, "total_costs": 9.0, "trades": 5, "run_id": 1,
        "signals": {"evaluations": 100, "acted": 5, "rejections": {}},
    }), REPORT)
    assert 'class="neg"' in html


def test_warmup_shows_a_progress_bar():
    html = build_overview(data(
        status=("opwarmen", "Bars worden opgebouwd."),
        warmup={"bars": 30, "needed": 60, "ready": False,
                "remaining": 30, "eta_minutes": 150},
    ), REPORT)
    assert 'class="bar"' in html
    assert "50%" in html


def test_halt_uses_the_alarm_colour():
    html = build_overview(data(status=("noodstop", "Dagverlies bereikt.")), REPORT)
    assert "Noodstop" in html
    assert "A6483A" in html.upper()


def test_gate_checks_are_shown_with_marks():
    html = build_overview(data(), REPORT)
    assert "✓" in html and "✗" in html


def test_rejection_reasons_are_listed():
    html = build_overview(data(), REPORT)
    assert "score_below_threshold" in html


def test_missing_values_do_not_break_the_page():
    html = build_overview({"status": ("wachtend", ""), "stats": {}, "gate": {}}, REPORT)
    assert html.startswith("<!DOCTYPE html>")
    assert "—" in html


def test_new_run_is_explained():
    """Een teller die op nul springt hoort uitgelegd, anders lijkt het alsof
    er data kwijt is."""
    html = build_overview(data(run_changed_because=["entry_threshold"]), REPORT)
    assert "Nieuwe bewijsfase" in html
    assert "entry_threshold" in html


def test_adopted_defaults_are_explained():
    html = build_overview(data(adopted_defaults=["max_spread"]), REPORT)
    assert "voortgezet" in html
    assert "niet zelf ingesteld" in html


def test_no_notice_when_nothing_changed():
    assert 'class="notice"' not in build_overview(data(), REPORT)


def test_paper_mode_is_stated_plainly():
    """Wie een trade ziet verschijnen en zijn brokeraccount ongewijzigd vindt,
    moet niet hoeven zoeken naar de verklaring."""
    html = build_overview(data(uses_real_money=False), REPORT)
    assert "PAPIERHANDEL" in html
    assert "geen geld" in html
    assert "brokeraccount verandert niet" in html


def test_real_money_is_unmistakable():
    html = build_overview(data(uses_real_money=True), REPORT)
    assert "ECHT GELD" in html
    assert "orders bij je broker" in html
    # Alarmkleur, niet dezelfde rustige tint als papermodus
    assert "A6483A" in html.upper()


def test_default_is_paper_when_unknown():
    """Bij twijfel de veilige uitspraak doen, niet de alarmerende."""
    html = build_overview(data(), REPORT)
    assert "PAPIERHANDEL" in html


def test_money_banner_comes_before_the_status():
    html = build_overview(data(), REPORT)
    assert html.index("PAPIERHANDEL") < html.index('class="status"')


def test_demo_is_distinguished_from_paper():
    """'Geen geld' is niet hetzelfde als 'geen orders': op demo gaan er
    werkelijke orders naar de broker met gemeten kosten."""
    html = build_overview(
        data(places_orders=True, uses_real_money=False, mode="demo"), REPORT
    )
    assert "DEMO" in html
    assert "echte orders" in html
    assert "gemeten, niet berekend" in html
    assert "PAPIERHANDEL" not in html


def test_paper_when_no_orders_are_placed():
    html = build_overview(data(places_orders=False, uses_real_money=False), REPORT)
    assert "PAPIERHANDEL" in html
    assert "DEMO" not in html


def test_real_money_outranks_demo():
    html = build_overview(
        data(places_orders=True, uses_real_money=True, mode="live"), REPORT
    )
    assert "ECHT GELD" in html
    assert "DEMO &middot;" not in html
