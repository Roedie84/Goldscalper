"""Overzichtspagina: het antwoord op "hoe staat het ervoor".

Het keuringsrapport is dicht beschreven en bedoeld om te bestuderen. Dat is de
verkeerde eerste indruk voor iemand die even op zijn telefoon kijkt of er nog
iets gebeurt. Deze pagina beantwoordt vier vragen - wat doet hij, wat leverde
het op, wat kost het, hoe ver is de bewijsfase - en zet het rapport één klik
verderop.

Mobiel als uitgangspunt, niet als bijzaak: één kolom, grote cijfers, en geen
tabel die breder is dan het scherm. Dezelfde vormgeving als het rapport, want
twee stijlen naast elkaar maakt het geheel rommeliger, niet duidelijker.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

from .report import TOKENS, _fmt, _local


def _esc(value) -> str:
    return html.escape(str(value if value is not None else "—"))


_STATUS_COLOURS = {
    "noodstop": TOKENS["reject"],
    "afgestemd_probleem": TOKENS["reject"],
    "gepauzeerd": TOKENS["cost"],
    "uitgeschakeld": TOKENS["ink_soft"],
    "markt_gesloten": TOKENS["ink_soft"],
    "opwarmen": TOKENS["metal"],
    "positie_open": TOKENS["assay"],
    "wachtend": TOKENS["assay"],
    "afwikkelen": TOKENS["cost"],
}

_STATUS_LABELS = {
    "noodstop": "Noodstop",
    "afgestemd_probleem": "Posities kloppen niet",
    "gepauzeerd": "Gepauzeerd",
    "uitgeschakeld": "Handel staat uit",
    "markt_gesloten": "Markt gesloten",
    "opwarmen": "Opwarmen",
    "positie_open": "Positie open",
    "wachtend": "Actief",
    "afwikkelen": "Afwikkelen",
}

_CSS = """
*,*::before,*::after{box-sizing:border-box}
html{height:100%%;-webkit-text-size-adjust:100%%}
body{margin:0;background:%(ground)s;color:%(ink)s;min-height:100%%;
  overflow-x:hidden;overflow-y:auto;-webkit-overflow-scrolling:touch;
  font:15px/1.5 ui-monospace,"SF Mono","Cascadia Mono",Menlo,Consolas,monospace}
.wrap{max-width:620px;margin:0 auto;padding:20px 14px 48px}

.head{display:flex;justify-content:space-between;align-items:baseline;
  border-bottom:2px solid %(ink)s;padding-bottom:10px;margin-bottom:18px}
.head h1{margin:0;font-size:13px;letter-spacing:.2em;text-transform:uppercase}
.head span{font-size:11px;color:%(ink_soft)s}

.status{border-left:4px solid var(--c);background:%(ground_deep)s;
  padding:14px 16px;margin-bottom:20px}
.status .label{font-size:19px;font-weight:600;color:var(--c)}
.status .detail{font-size:12.5px;color:%(ink_soft)s;margin-top:6px;line-height:1.5}

.cards{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px}
.card{background:%(ground_deep)s;padding:13px 14px}
.card dt{font-size:10px;letter-spacing:.13em;text-transform:uppercase;
  color:%(ink_soft)s;margin:0 0 5px}
.card dd{margin:0;font-size:22px;font-weight:600;font-variant-numeric:tabular-nums;
  line-height:1.15}
.card .sub{font-size:11px;color:%(ink_soft)s;font-weight:400;margin-top:3px}
.pos{color:%(assay)s}.neg{color:%(reject)s}

.bar{height:8px;background:%(ground_deep)s;border:1px solid %(rule)s;margin:4px 0 6px}
.bar i{display:block;height:100%%;background:%(metal)s}

h2{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:%(ink_soft)s;
  font-weight:600;margin:22px 0 8px;padding-bottom:5px;
  border-bottom:1px solid %(rule)s}

.rows{list-style:none;margin:0;padding:0}
.rows li{display:flex;justify-content:space-between;gap:12px;padding:8px 0;
  border-bottom:1px solid rgba(178,186,174,.4);font-size:13px}
.rows .k{color:%(ink_soft)s}
.rows .v{font-variant-numeric:tabular-nums;text-align:right}

.check{display:flex;gap:9px;align-items:flex-start;padding:6px 0;font-size:12.5px}
.check .mark{flex-shrink:0;width:16px;font-weight:600}
.ok{color:%(assay)s}.no{color:%(reject)s}

a.button{display:block;text-align:center;text-decoration:none;
  background:%(ink)s;color:%(ground)s;padding:14px;margin-top:24px;
  font-size:13px;letter-spacing:.1em;text-transform:uppercase;font-weight:600}
a.button:active{opacity:.75}

.money{padding:10px 13px;margin-bottom:16px;font-size:11.5px;
  letter-spacing:.04em;line-height:1.5}
.money.paper{background:%(ground_deep)s;border-left:4px solid %(assay)s;
  color:%(ink_soft)s}
.money.paper::first-line{font-weight:600;color:%(assay)s}
.money.real{background:%(reject)s;color:%(ground)s;font-weight:600;
  border-left:4px solid %(ink)s}
.notice{background:%(ground_deep)s;border-left:3px solid %(metal)s;
  padding:11px 13px;font-size:12px;line-height:1.5;margin-bottom:18px}
footer{margin-top:26px;font-size:10.5px;color:%(ink_soft)s;line-height:1.6}

@media (max-width:400px){
  .cards{grid-template-columns:1fr}
  .card dd{font-size:20px}
}
""" % TOKENS


def _card(label: str, value: str, sub: str = "", cls: str = "") -> str:
    extra = f'<div class="sub">{_esc(sub)}</div>' if sub else ""
    return (
        f'<div class="card"><dt>{_esc(label)}</dt>'
        f'<dd class="{cls}">{value}{extra}</dd></div>'
    )


def build_overview(
    data: dict, report_url: str, tz=None, refresh_seconds: int = 60
) -> str:
    """Compacte samenvatting van de huidige toestand."""
    stats = data.get("stats") or {}
    gate = data.get("gate") or {}
    warmup = data.get("warmup")

    state, detail = data.get("status", ("wachtend", ""))
    colour = _STATUS_COLOURS.get(state, TOKENS["ink_soft"])
    label = _STATUS_LABELS.get(state, state)

    # Voortgangsbalk tijdens het opwarmen: dan is dat het enige dat telt.
    progress = ""
    if warmup and not warmup.get("ready"):
        done = warmup["bars"] / max(1, warmup["needed"]) * 100
        progress = (
            f'<div class="bar"><i style="width:{min(100, done):.0f}%"></i></div>'
        )

    net = stats.get("net_pnl")
    trades = stats.get("trades") or 0
    signals = stats.get("signals") or {}

    cards = "".join([
        _card(
            "Netto", _fmt(net) if net is not None else "—",
            f"{trades} trades",
            "pos" if (net or 0) >= 0 else "neg",
        ),
        _card("Kosten", _fmt(stats.get("total_costs")), "spread en slippage"),
        _card(
            "Koers", _fmt(data.get("price")),
            f"spread {_fmt(data.get('spread'), 3)}",
        ),
        _card(
            "Signalen", f"{signals.get('acted', 0)}",
            f"van {signals.get('evaluations', 0)} evaluaties",
        ),
    ])

    # Waarom er niet gehandeld wordt: de meest voorkomende reden volstaat.
    rejections = signals.get("rejections") or {}
    reason_rows = ""
    for reason, count in sorted(rejections.items(), key=lambda kv: -kv[1])[:4]:
        reason_rows += (
            f'<li><span class="k">{_esc(reason)}</span>'
            f'<span class="v">{count}</span></li>'
        )
    reasons_block = (
        f'<h2>Waarom niet gehandeld</h2><ul class="rows">{reason_rows}</ul>'
        if reason_rows else ""
    )

    # Een nieuwe run die op nul begint hoort uitgelegd te worden, anders lijkt
    # het alsof er data kwijt is.
    notice = ""
    changed = data.get("run_changed_because") or []
    adopted = data.get("adopted_defaults") or []
    if changed:
        notice = (
            f'<div class="notice">Nieuwe bewijsfase begonnen omdat je '
            f'<b>{_esc(", ".join(changed))}</b> hebt gewijzigd. Eerdere runs '
            'staan onderaan het keuringsrapport.</div>'
        )
    elif adopted:
        notice = (
            f'<div class="notice">Bewijsfase voortgezet ondanks gewijzigde '
            f'standaardwaarden ({_esc(", ".join(adopted))}). Die heb je niet '
            'zelf ingesteld, dus de teller loopt door.</div>'
        )

    checks = gate.get("checks") or {}
    check_rows = ""
    for name, passed in checks.items():
        mark = "✓" if passed else "✗"
        cls = "ok" if passed else "no"
        check_rows += (
            f'<div class="check"><span class="mark {cls}">{mark}</span>'
            f'<span>{_esc(name.replace("_", " "))}</span></div>'
        )

    # Papermodus of echt geld: dit hoort de eerste regel te zijn die je leest.
    # Wie een trade ziet verschijnen en zijn brokeraccount ongewijzigd vindt,
    # moet niet hoeven zoeken naar de verklaring.
    real_money = bool(data.get("uses_real_money"))
    if real_money:
        money_banner = (
            '<div class="money real">ECHT GELD &middot; deze bot plaatst orders '
            'bij je broker</div>'
        )
    else:
        money_banner = (
            '<div class="money paper">PAPIERHANDEL &middot; koersen zijn echt, '
            'trades zijn gesimuleerd. Er gaat geen geld om en je brokeraccount '
            'verandert niet.</div>'
        )

    now = datetime.now(tz or timezone.utc)
    refresh = (
        f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'
        if refresh_seconds else ""
    )

    return f"""<!DOCTYPE html>
<html lang="nl"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
{refresh}
<title>Gold Scalper</title>
<style>{_CSS}</style></head><body><div class="wrap">

<div class="head">
  <h1>Gold Scalper</h1>
  <span>{now.strftime("%H:%M")}</span>
</div>

{money_banner}

<div class="status" style="--c:{colour}">
  <div class="label">{_esc(label)}</div>
  {progress}
  <div class="detail">{_esc(detail)}</div>
</div>

<div class="cards">{cards}</div>

{notice}

{reasons_block}

<h2>Bewijsfase</h2>
{check_rows or '<p class="detail">Nog geen gegevens.</p>'}

<a class="button" href="{report_url}">Volledig keuringsrapport →</a>

<footer>
  {_esc(data.get("symbol") or "")} · {_esc(data.get("mode") or "")}modus ·
  run {_esc(stats.get("run_id"))}<br>
  Technische indicatoranalyse, geen financieel advies.
</footer>
</div></body></html>"""
