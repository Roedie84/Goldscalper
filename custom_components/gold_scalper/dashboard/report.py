"""Genereert een zelfstandig HTML-rapport uit de tradedatabase.

Geen externe afhankelijkheden, geen CDN, geen internet nodig. De uitvoer is één
HTML-bestand met alle data en grafieken erin; je kunt het openen, mailen of
archiveren zonder dat het ooit stukgaat.

Vormgeving volgt het keuringsrapport van een goudsmid. Dat is geen versiering:
goud keuren is vaststellen of het echt is, en fijnheid wordt uitgedrukt in
duizendsten. Diezelfde maat past hier precies - hoeveel duizendsten van de
gevangen marktbeweging overleven de kosten. Een strategie die 64% aan spread en
commissie kwijtraakt is 360 duizendsten fijn, en dat is een eerlijker getal dan
een groene pijl omhoog.

De grafieken zijn met de hand geschreven SVG. Dat is meer werk dan een
chartbibliotheek, maar het rapport blijft daardoor één bestand zonder
afhankelijkheden die over twee jaar verdwenen zijn.
"""

from __future__ import annotations

import html
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from ..storage import performance
from ..storage.database import TradeDatabase

# --------------------------------------------------------------------------- #
# Vormgeving
# --------------------------------------------------------------------------- #

TOKENS = {
    "ground": "#E4E7E2",      # keuringspapier, koel grijsgroen
    "ground_deep": "#D5DAD3",
    "ink": "#22271F",         # olijfzwart
    "ink_soft": "#5A6156",
    "rule": "#B2BAAE",
    "assay": "#5E7A52",       # salie, goedgekeurd
    "reject": "#A6483A",      # oxiderood, afgekeurd
    "metal": "#8F7334",       # dof antiekgoud, alleen voor het metaal zelf
    "cost": "#8A6E6A",        # verweerd bruinrood, kosten
}


def _local(iso: str | None, tz=None, fmt: str = "%d-%m-%Y %H:%M") -> str:
    """Toon een UTC-tijdstempel in de lokale tijdzone.

    De database bewaart alles in UTC - dat is de enige zinnige keuze voor een
    reeks die over zomertijdwissels heen loopt. Maar een rapport dat je om
    17:31 opent en 15:31 toont, is verwarrend en maakt het lastig om een trade
    terug te vinden in je eigen herinnering.
    """
    if not iso:
        return "—"
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return _esc(iso)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if tz is not None:
        moment = moment.astimezone(tz)
    return moment.strftime(fmt)


def _fmt(value, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
        return "∞"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}".replace(",", " ") + suffix
    return html.escape(str(value))


def _esc(text) -> str:
    return html.escape(str(text if text is not None else "—"))


# --------------------------------------------------------------------------- #
# SVG-grafieken
# --------------------------------------------------------------------------- #


def _downsample(values: list[float], target: int) -> list[float]:
    """Dun een reeks uit tot ``target`` punten met behoud van de vorm.

    De grafiek is 720 pixels breed; meer punten dan dat leveren geen zichtbaar
    detail maar wel een enorme SVG. Bij 5000 trades scheelt dit de helft van de
    opbouwtijd en tweederde van de bestandsgrootte.

    Er wordt bewust *niet* gemiddeld maar gesampled met behoud van de uitersten
    per blok: een middeling zou de drawdowns gladstrijken, en dat is precies
    wat je wél wilt zien.
    """
    if len(values) <= target or target < 3:
        return list(values)
    block = len(values) / target
    out = [values[0]]
    for i in range(1, target - 1):
        chunk = values[int(i * block):int((i + 1) * block)] or [out[-1]]
        # Neem het punt dat het verst van het vorige ligt: behoudt pieken en dalen.
        out.append(max(chunk, key=lambda v: abs(v - out[-1])))
    out.append(values[-1])
    return out


def _line_chart(
    series: list[float],
    costs: list[float] | None = None,
    width: int = 720,
    height: int = 220,
) -> str:
    """Equitycurve met de cumulatieve kosten als tweede lijn.

    Die tweede lijn is het punt van de grafiek. Een stijgende equity zegt weinig
    zolang je niet ziet hoeveel er onderweg aan de broker is betaald.
    """
    if len(series) < 2:
        return _empty_chart(width, height, "Nog geen equitydata")

    if len(series) > width:
        if costs and len(costs) == len(series):
            costs = _downsample(costs, width)
        series = _downsample(series, width)

    pad_l, pad_r, pad_t, pad_b = 56, 16, 14, 24
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b

    lo, hi = min(series), max(series)
    if costs:
        hi = max(hi, max(series[0] + c for c in costs))
    span = (hi - lo) or 1.0
    lo -= span * 0.08
    hi += span * 0.08
    span = hi - lo

    def x(i: int, n: int) -> float:
        return pad_l + (i / max(1, n - 1)) * plot_w

    def y(v: float) -> float:
        return pad_t + plot_h - ((v - lo) / span) * plot_h

    equity_pts = " ".join(f"{x(i, len(series)):.1f},{y(v):.1f}" for i, v in enumerate(series))
    baseline = y(series[0])

    area = (
        f'<polygon points="{pad_l:.1f},{baseline:.1f} {equity_pts} '
        f'{x(len(series) - 1, len(series)):.1f},{baseline:.1f}" '
        f'fill="{TOKENS["metal"]}" opacity="0.13"/>'
    )

    cost_line = ""
    if costs and len(costs) >= 2:
        cost_pts = " ".join(
            f"{x(i, len(costs)):.1f},{y(series[0] + c):.1f}" for i, c in enumerate(costs)
        )
        cost_line = (
            f'<polyline points="{cost_pts}" fill="none" stroke="{TOKENS["cost"]}" '
            f'stroke-width="1.5" stroke-dasharray="4 3"/>'
        )

    ticks = ""
    for frac in (0.0, 0.5, 1.0):
        value = lo + span * frac
        yy = y(value)
        ticks += (
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width - pad_r}" y2="{yy:.1f}" '
            f'stroke="{TOKENS["rule"]}" stroke-width="0.5" opacity="0.6"/>'
            f'<text x="{pad_l - 8}" y="{yy + 3:.1f}" text-anchor="end" '
            f'class="tick">{value:,.0f}</text>'
        )

    return f"""<svg viewBox="0 0 {width} {height}" class="chart" role="img"
     aria-label="Equitycurve met cumulatieve kosten">
  {ticks}
  {area}
  <polyline points="{equity_pts}" fill="none" stroke="{TOKENS['metal']}" stroke-width="2"/>
  {cost_line}
  <line x1="{pad_l}" y1="{baseline:.1f}" x2="{width - pad_r}" y2="{baseline:.1f}"
        stroke="{TOKENS['ink_soft']}" stroke-width="0.75" stroke-dasharray="2 4"/>
</svg>"""


def _bar_chart(
    labels: list[str], values: list[float], width: int = 720, height: int = 160
) -> str:
    """Dagresultaten. Toont of winst uit één uitschieter komt."""
    if not values:
        return _empty_chart(width, height, "Nog geen handelsdagen")

    pad_l, pad_r, pad_t, pad_b = 56, 16, 12, 30
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    extent = max(abs(min(values)), abs(max(values)), 1e-9)
    zero_y = pad_t + plot_h / 2
    slot = plot_w / max(1, len(values))
    bar_w = max(2.0, slot * 0.62)

    bars = ""
    for i, value in enumerate(values):
        h = abs(value) / extent * (plot_h / 2)
        cx = pad_l + slot * i + slot / 2
        top = zero_y - h if value >= 0 else zero_y
        colour = TOKENS["assay"] if value >= 0 else TOKENS["reject"]
        bars += (
            f'<rect x="{cx - bar_w / 2:.1f}" y="{top:.1f}" width="{bar_w:.1f}" '
            f'height="{max(1.0, h):.1f}" fill="{colour}" opacity="0.85">'
            f'<title>{_esc(labels[i])}: {value:+.2f}</title></rect>'
        )

    step = max(1, len(labels) // 8)
    tick_labels = "".join(
        f'<text x="{pad_l + slot * i + slot / 2:.1f}" y="{height - 8}" '
        f'text-anchor="middle" class="tick">{_esc(labels[i][5:])}</text>'
        for i in range(0, len(labels), step)
    )

    return f"""<svg viewBox="0 0 {width} {height}" class="chart" role="img"
     aria-label="Nettoresultaat per dag">
  <line x1="{pad_l}" y1="{zero_y:.1f}" x2="{width - pad_r}" y2="{zero_y:.1f}"
        stroke="{TOKENS['ink_soft']}" stroke-width="0.75"/>
  <text x="{pad_l - 8}" y="{zero_y + 3:.1f}" text-anchor="end" class="tick">0</text>
  {bars}{tick_labels}
</svg>"""


def _scatter_mae_mfe(trades: list, width: int = 340, height: int = 240) -> str:
    """MAE tegen MFE: hoe ver ging een trade tegen je in voordat hij goed liep.

    Zitten winnaars structureel dicht bij de stop, dan staat die te krap.
    Zitten verliezers met een hoge MFE, dan wordt winst te laat gepakt.
    """
    points = [(t.mae or 0.0, t.mfe or 0.0, t.net_pnl or 0.0) for t in trades if t.mfe is not None]
    if not points:
        return _empty_chart(width, height, "Nog geen MAE/MFE-data")

    pad = 34
    plot_w, plot_h = width - pad * 2, height - pad * 2
    max_mfe = max((p[1] for p in points), default=1.0) or 1.0
    max_mae = max((abs(p[0]) for p in points), default=1.0) or 1.0

    dots = ""
    for mae, mfe, net in points[-400:]:
        px = pad + (abs(mae) / max_mae) * plot_w
        py = pad + plot_h - (mfe / max_mfe) * plot_h
        colour = TOKENS["assay"] if net >= 0 else TOKENS["reject"]
        dots += (
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.6" fill="{colour}" opacity="0.55">'
            f'<title>MAE {mae:.3f} · MFE {mfe:.3f} · netto {net:+.2f}</title></circle>'
        )

    return f"""<svg viewBox="0 0 {width} {height}" class="chart" role="img"
     aria-label="MAE tegen MFE per trade">
  <line x1="{pad}" y1="{pad + plot_h}" x2="{pad + plot_w}" y2="{pad + plot_h}"
        stroke="{TOKENS['rule']}" stroke-width="0.75"/>
  <line x1="{pad}" y1="{pad}" x2="{pad}" y2="{pad + plot_h}"
        stroke="{TOKENS['rule']}" stroke-width="0.75"/>
  {dots}
  <text x="{pad + plot_w / 2:.0f}" y="{height - 6}" text-anchor="middle"
        class="tick">tegen je in (MAE) →</text>
  <text x="10" y="{pad + plot_h / 2:.0f}" class="tick"
        transform="rotate(-90 10 {pad + plot_h / 2:.0f})" text-anchor="middle">mee (MFE) →</text>
</svg>"""


def _funnel(signal_stats: dict, width: int = 340) -> str:
    """Signaaltrechter: van evaluatie naar trade, met de afwijzingsredenen.

    Dit is bij deze strategie het meest informatieve overzicht. Als er van
    duizenden evaluaties een handvol trades overblijft, ligt daar het verhaal.
    """
    total = signal_stats.get("evaluations", 0)
    acted = signal_stats.get("acted", 0)
    rejections = signal_stats.get("rejections", {})
    if not total:
        return '<p class="empty">Nog geen signalen vastgelegd.</p>'

    rows = ""
    ordered = sorted(rejections.items(), key=lambda kv: kv[1], reverse=True)
    for reason, count in ordered:
        pct = count / total * 100
        rows += f"""<div class="funnel-row">
      <div class="funnel-bar" style="width:{max(1.5, pct):.1f}%"></div>
      <span class="funnel-label">{_esc(reason)}</span>
      <span class="funnel-count">{count:,}</span>
    </div>""".replace(",", " ")

    acted_pct = acted / total * 100 if total else 0
    return f"""<div class="funnel">
  <div class="funnel-head">
    <span>{total:,}</span> evaluaties → <span>{acted:,}</span> trades
    ({acted_pct:.2f}%)
  </div>
  {rows}
</div>""".replace(",", " ")


def _empty_chart(width: int, height: int, message: str) -> str:
    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart"><text x="{width/2}" '
        f'y="{height/2}" text-anchor="middle" class="empty-text">{_esc(message)}</text></svg>'
    )


# --------------------------------------------------------------------------- #
# Fijnheid: het kerncijfer
# --------------------------------------------------------------------------- #


def _cost_projection_block(stats: dict) -> str:
    """Toon wat hetzelfde tradepatroon zou opleveren bij echte spreads.

    Staat bovenaan wanneer er zonder kosten gedraaid is, want dan is het
    winstcijfer erboven fictief en moet die context niet onderaan de pagina
    verstopt zitten.
    """
    rows = stats.get("cost_projection") or []
    if not rows:
        return ""

    disabled = bool(stats.get("costs_disabled"))
    body = ""
    for row in rows:
        cls = "pos" if row["profitable"] else "neg"
        body += (
            f"<tr><td>{_esc(row['label'])}</td>"
            f"<td class='num'>{_fmt(row['costs'])}</td>"
            f"<td class='num {cls}'>{_fmt(row['net_pnl'])}</td></tr>"
        )

    banner = (
        '<p class="note" style="color:%s"><strong>Deze run draaide zonder '
        'transactiekosten.</strong> Het resultaat hierboven is fictief; in de '
        'echte markt betaal je bij elke trade de spread.</p>' % TOKENS["reject"]
        if disabled else
        '<p class="note">Wat hetzelfde tradepatroon zou opleveren bij andere '
        'spreads. Optimistisch: een bredere spread zou ook minder trades '
        'doorlaten.</p>'
    )

    return f"""<section>
  <h3>Kostenprojectie</h3>
  {banner}
  <div class="scroller"><table><thead><tr><th>Scenario</th>
    <th class="num">Kosten</th><th class="num">Netto</th>
  </tr></thead><tbody>{body}</tbody></table></div>
</section>"""


def _run_history(db, current_run_id: int, tz=None) -> str:
    """Overzicht van alle runs, met de huidige gemarkeerd.

    Bij een wijziging in de opzet - andere databron, andere drempel - begint
    een nieuwe run, want de resultaten zijn dan niet meer vergelijkbaar. Zonder
    dit overzicht lijkt de eerdere data verdwenen, terwijl hij gewoon in de
    database staat.
    """
    try:
        runs = db.run_totals()
    except Exception:  # noqa: BLE001 - oudere database zonder deze query
        return ""
    if len(runs) < 2:
        return ""

    rows = ""
    for run in runs[:12]:
        current = run["id"] == current_run_id
        try:
            config = json.loads(run.get("config_json") or "{}")
        except (TypeError, ValueError):
            config = {}
        bron = config.get("venue", "—")
        if config.get("simulated"):
            bron += " (fictief)"
        elif config.get("costs_disabled"):
            bron += " (geen kosten)"
        net = run["net_pnl"] or 0.0
        rows += (
            f"<tr{' class=\'current\'' if current else ''}>"
            f"<td>{run['id']}{' ●' if current else ''}</td>"
            f"<td>{_local(run['started_at'], tz, '%d-%m %H:%M')}</td>"
            f"<td>{_esc(bron)}</td>"
            f"<td>{_esc(run['strategy_version'])}</td>"
            f"<td class='num'>{run['trades']}</td>"
            f"<td class='num'>{_fmt(run['costs'])}</td>"
            f"<td class='num {'pos' if net >= 0 else 'neg'}'>{_fmt(net)}</td>"
            "</tr>"
        )

    return f"""<section>
  <h3>Eerdere runs</h3>
  <p class="note">Een nieuwe run begint zodra de opzet verandert - andere
     databron, andere drempel, andere spread - omdat de resultaten dan niet meer
     vergelijkbaar zijn. Een herstart of een gewijzigde risicolimiet begint
     géén nieuwe run. De huidige staat gemarkeerd.</p>
  <div class="scroller"><table><thead><tr>
    <th>Run</th><th>Gestart</th><th>Bron</th><th>Strategie</th>
    <th class="num">Trades</th><th class="num">Kosten</th><th class="num">Netto</th>
  </tr></thead><tbody>{rows}</tbody></table></div>
</section>"""


def _fineness(stats: dict) -> int:
    """Duizendsten van de bruto beweging die de kosten overleven.

    1000 zou betekenen: geen kosten. 0 betekent: de kosten aten alles op. Een
    negatieve nettouitkomst geeft ook 0 - onder nul bestaat geen fijnheid, net
    als bij metaal.
    """
    gross = stats.get("gross_pnl") or 0.0
    net = stats.get("net_pnl") or 0.0
    if gross <= 0 or net <= 0:
        return 0
    return max(0, min(1000, round(net / gross * 1000)))


def _stamp(stats: dict, gate: dict | None) -> str:
    """Het keurmerk. Eén element waar de hele uitkomst in samenvalt."""
    verdict = stats.get("verdict", "no_data")
    unlocked = bool(gate and gate.get("unlocked"))
    if verdict == "passed" and unlocked:
        label, sub, colour = "GEKEURD", "vrijgegeven", TOKENS["assay"]
    elif verdict in ("insufficient_data", "no_data"):
        label, sub, colour = "IN KEURING", "onvoldoende data", TOKENS["ink_soft"]
    else:
        label, sub, colour = "AFGEKEURD", "vergrendeld", TOKENS["reject"]

    fineness = _fineness(stats)
    return f"""<div class="stamp" style="--stamp:{colour}">
  <svg viewBox="0 0 160 160" aria-hidden="true">
    <circle cx="80" cy="80" r="74" fill="none" stroke="{colour}" stroke-width="2.5"/>
    <circle cx="80" cy="80" r="66" fill="none" stroke="{colour}" stroke-width="0.75"/>
  </svg>
  <div class="stamp-text">
    <span class="stamp-label">{label}</span>
    <span class="stamp-fineness">{fineness}</span>
    <span class="stamp-sub">{sub}</span>
  </div>
</div>"""


# --------------------------------------------------------------------------- #
# Rapport
# --------------------------------------------------------------------------- #

_CSS = """
*,*::before,*::after{box-sizing:border-box}

/* Scrollen in een iframe.
   Het paneel toont dit rapport in een iframe. Zonder expliciete hoogte en
   overflow schaalt iOS Safari het iframe naar de inhoudshoogte en scrollt hij
   niet; de pagina staat dan vast. */
html{height:100%%;-webkit-text-size-adjust:100%%}
body{margin:0;background:%(ground)s;color:%(ink)s;
  min-height:100%%;overflow-x:hidden;overflow-y:auto;
  -webkit-overflow-scrolling:touch;
  font:14px/1.55 ui-monospace,"SF Mono","Cascadia Mono",Menlo,Consolas,monospace;
  -webkit-font-smoothing:antialiased}
.prose{font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif}
.wrap{max-width:1080px;margin:0 auto;padding:32px 22px 72px}

/* Tabellen mogen de pagina niet breder maken dan het scherm.
   Zodra dat gebeurt kantelt de hele weergave horizontaal en werkt verticaal
   scrollen in een iframe niet meer. Elke tabel krijgt daarom een eigen
   scrollbare houder. */
.scroller{overflow-x:auto;-webkit-overflow-scrolling:touch;
  margin:0 -4px;padding:0 4px}
.scroller table{min-width:100%%}

header{display:flex;justify-content:space-between;align-items:flex-start;gap:28px;
  border-bottom:2px solid %(ink)s;padding-bottom:18px;margin-bottom:6px;flex-wrap:wrap}
h1{margin:0;font-size:19px;font-weight:600;letter-spacing:.22em;text-transform:uppercase}
.subtitle{margin:6px 0 0;color:%(ink_soft)s;font-size:12px;letter-spacing:.08em}
.meta{color:%(ink_soft)s;font-size:11px;letter-spacing:.06em;margin-top:10px}

.stamp{position:relative;width:160px;height:160px;flex-shrink:0;
  transform:rotate(-6deg);opacity:.92}
.stamp svg{position:absolute;inset:0;width:100%%;height:100%%}
.stamp-text{position:absolute;inset:0;display:flex;flex-direction:column;
  align-items:center;justify-content:center;color:var(--stamp);text-align:center}
.stamp-label{font-size:13px;font-weight:700;letter-spacing:.16em}
.stamp-fineness{font-size:38px;font-weight:600;line-height:1.05;margin:2px 0}
.stamp-sub{font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;opacity:.8}

.verdict{border-left:3px solid var(--vc,%(ink_soft)s);padding:14px 18px;margin:26px 0;
  background:%(ground_deep)s}
.verdict h2{margin:0 0 8px;font-size:11px;letter-spacing:.18em;text-transform:uppercase;
  color:%(ink_soft)s;font-weight:600}
.verdict p{margin:0;font-size:14px}
.verdict ul{margin:10px 0 0;padding-left:18px}
.verdict li{margin:3px 0;font-size:13px}

.fineness-bar{margin:22px 0 30px}
.fineness-bar .track{height:26px;background:%(ground_deep)s;position:relative;
  border:1px solid %(rule)s}
.fineness-bar .fill{height:100%%;background:%(metal)s;opacity:.85}
.fineness-bar .caption{display:flex;justify-content:space-between;
  font-size:11px;color:%(ink_soft)s;margin-top:7px;letter-spacing:.05em}

section{margin:34px 0}
h3{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:%(ink_soft)s;
  font-weight:600;margin:0 0 4px;padding-bottom:6px;border-bottom:1px solid %(rule)s}
.note{font-size:12px;color:%(ink_soft)s;margin:8px 0 14px}

.grid{display:grid;grid-template-columns:1fr 300px;gap:26px;align-items:start}
.duo{display:grid;grid-template-columns:1fr 1fr;gap:26px;align-items:start}
.chart{width:100%%;height:auto;display:block}
.tick{font-size:9.5px;fill:%(ink_soft)s;font-family:inherit}
.empty-text{font-size:12px;fill:%(ink_soft)s}
.empty{color:%(ink_soft)s;font-size:12px}

.legend{display:flex;gap:18px;font-size:11px;color:%(ink_soft)s;margin-top:10px}
.legend i{display:inline-block;width:16px;height:2px;vertical-align:middle;margin-right:6px}

table{width:100%%;border-collapse:collapse;font-size:12px}
th{text-align:left;font-weight:600;color:%(ink_soft)s;font-size:10px;
  letter-spacing:.12em;text-transform:uppercase;padding:7px 8px;
  border-bottom:1px solid %(rule)s;white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid rgba(178,186,174,.4);white-space:nowrap}
tbody tr:hover{background:%(ground_deep)s}
tr.current td{background:%(ground_deep)s;font-weight:600}
.num{text-align:right;font-variant-numeric:tabular-nums}
.pos{color:%(assay)s}.neg{color:%(reject)s}

.metrics{list-style:none;margin:0;padding:0}
.metrics li{display:flex;justify-content:space-between;gap:14px;padding:7px 0;
  border-bottom:1px solid rgba(178,186,174,.45)}
.metrics dt{color:%(ink_soft)s;font-size:11.5px;margin:0}
.metrics dd{margin:0;font-variant-numeric:tabular-nums;font-size:13px}

.funnel-head{font-size:12px;color:%(ink_soft)s;margin-bottom:12px}
.funnel-head span{color:%(ink)s;font-weight:600}
.funnel-row{position:relative;padding:5px 0;margin-bottom:3px}
.funnel-bar{position:absolute;inset:0 auto 0 0;background:%(cost)s;opacity:.2}
.funnel-label{position:relative;font-size:11.5px}
.funnel-count{position:relative;float:right;font-size:11.5px;color:%(ink_soft)s;
  font-variant-numeric:tabular-nums}

footer{margin-top:52px;padding-top:16px;border-top:1px solid %(rule)s;
  font-size:11px;color:%(ink_soft)s;line-height:1.7}

@media (max-width:820px){
  .grid,.duo{grid-template-columns:1fr}
  header{flex-direction:column-reverse}
  .stamp{align-self:flex-start}
}

@media (max-width:560px){
  .wrap{padding:18px 12px 56px}
  h1{font-size:15px;letter-spacing:.14em}
  .stamp{width:118px;height:118px}
  .stamp-fineness{font-size:28px}
  table{font-size:11px}
  th,td{padding:5px 6px}

  /* Kolommen die op een telefoon niet passen en die je op een groter scherm
     alsnog ziet. De essentie - tijd, kant, netto - blijft staan. */
  .trades th:nth-child(4),.trades td:nth-child(4),
  .trades th:nth-child(5),.trades td:nth-child(5),
  .trades th:nth-child(6),.trades td:nth-child(6){display:none}

  .metrics dt{font-size:11px}
  .metrics dd{font-size:12px}
  .funnel-label,.funnel-count{font-size:10.5px}
  .note{font-size:11px}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
""" % TOKENS


def build_report(
    db: TradeDatabase,
    run_id: int,
    gate: dict | None = None,
    title: str = "Gold Scalper",
    tz=None,
    refresh_seconds: int = 0,
) -> str:
    """Bouw het volledige HTML-rapport voor één run."""
    run = db.get_run(run_id) or {}
    stats = performance.compute_for_run(db, run_id)
    trades = db.closed_trades(run_id)
    daily = performance.daily_breakdown(trades)
    curve = db.equity_curve(run_id)

    equity = [row["equity"] for row in curve] or [run.get("starting_balance", 10000.0)]
    costs = [row["cumulative_cost"] for row in curve]
    fineness = _fineness(stats)

    verdict = stats.get("verdict", "no_data")
    vc = (
        TOKENS["assay"] if verdict == "passed"
        else TOKENS["reject"] if verdict == "failed"
        else TOKENS["ink_soft"]
    )
    reasons = stats.get("blocking_reasons") or []
    if gate and not gate.get("unlocked"):
        reasons = list(dict.fromkeys(reasons + (gate.get("blocking_reasons") or [])))

    reason_list = (
        "<ul>" + "".join(f"<li>{_esc(r)}</li>" for r in reasons) + "</ul>" if reasons else ""
    )

    metrics = [
        ("Trades", _fmt(stats.get("trades"), 0)),
        ("Winstpercentage", _fmt(stats.get("win_rate"), 1, "%")),
        ("Bruto resultaat", _fmt(stats.get("gross_pnl"))),
        ("Kosten", _fmt(stats.get("total_costs"))),
        ("Netto resultaat", _fmt(stats.get("net_pnl"))),
        ("Kostenratio", _fmt(stats.get("cost_ratio"), 3)),
        ("Profit factor", _fmt(stats.get("profit_factor"), 3)),
        ("Verwachting per trade", _fmt(stats.get("expectancy"), 4)),
        ("t-statistiek", _fmt(stats.get("t_statistic"), 2)),
        ("Max. drawdown", _fmt(stats.get("max_drawdown_pct"), 2, "%")),
        ("Nodig per ounce", _fmt(stats.get("breakeven_edge_per_oz"), 4)),
        ("Gevangen per ounce", _fmt(stats.get("avg_excursion_per_oz"), 4)),
        ("Overschot", _fmt(stats.get("edge_surplus_per_oz"), 4)),
        ("Gem. duur", _fmt(stats.get("avg_duration_seconds"), 0, " s")),
    ]
    metric_rows = "".join(
        f"<li><dt>{_esc(k)}</dt><dd>{v}</dd></li>" for k, v in metrics
    )

    trade_rows = ""
    for t in reversed(trades[-60:]):
        cls = "pos" if (t.net_pnl or 0) >= 0 else "neg"
        trade_rows += f"""<tr>
      <td>{_local(t.close_time, tz, "%d-%m %H:%M")}</td>
      <td>{_esc(t.side)}</td>
      <td class="num">{_fmt(t.volume, 2)}</td>
      <td class="num">{_fmt(t.open_price, 2)}</td>
      <td class="num">{_fmt(t.close_price, 2)}</td>
      <td class="num">{_fmt(t.gross_pnl)}</td>
      <td class="num">{_fmt(t.total_cost)}</td>
      <td class="num {cls}">{_fmt(t.net_pnl)}</td>
      <td>{_esc(t.close_reason)}</td>
    </tr>"""
    if not trade_rows:
        trade_rows = '<tr><td colspan="9" class="empty">Nog geen gesloten trades.</td></tr>'

    now = datetime.now(tz or timezone.utc)
    label = now.tzname() or "UTC"
    generated = now.strftime(f"%d-%m-%Y %H:%M:%S {label}")

    # Automatisch verversen via meta-refresh in plaats van JavaScript: het
    # rapport blijft daarmee scriptvrij, wat van belang is omdat het paneel
    # zonder authenticatie bereikbaar is.
    #
    # Nul betekent uit; dan blijft het een momentopname en moet je zelf
    # verversen.
    refresh_tag = (
        f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'
        if refresh_seconds and refresh_seconds > 0 else ""
    )

    return f"""<!DOCTYPE html>
<html lang="nl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{refresh_tag}
<title>{_esc(title)} · keuringsrapport</title>
<style>{_CSS}</style></head><body><div class="wrap">

<header>
  <div>
    <h1>Keuringsrapport</h1>
    <p class="subtitle">{_esc(run.get('symbol', '—'))} · {_esc(run.get('mode', '—'))}modus
      · {_esc(run.get('strategy_version', '—'))}</p>
    <p class="meta">Run {run_id} · gestart {_local(run.get('started_at'), tz)}
      · opgemaakt {generated}
      {f"· ververst elke {int(refresh_seconds)}s" if refresh_seconds else ""}</p>
  </div>
  {_stamp(stats, gate)}
</header>

<div class="verdict" style="--vc:{vc}">
  <h2>Oordeel</h2>
  <p class="prose">{_esc(stats.get('verdict_text', 'Geen data.'))}</p>
  {reason_list}
</div>

<div class="fineness-bar">
  <div class="track"><div class="fill" style="width:{fineness / 10:.1f}%"></div></div>
  <div class="caption">
    <span>Rendementsfijnheid {fineness}/1000</span>
    <span>duizendsten van de gevangen beweging die de kosten overleven</span>
  </div>
</div>

{_cost_projection_block(stats)}

<section>
  <h3>Equity en kosten</h3>
  <p class="note">De onderbroken lijn is wat er cumulatief naar de broker ging.
     Loopt die boven de equitylijn, dan verdient de broker aan deze strategie en jij niet.</p>
  {_line_chart(equity, costs)}
  <div class="legend">
    <span><i style="background:{TOKENS['metal']}"></i>equity</span>
    <span><i style="background:{TOKENS['cost']}"></i>cumulatieve kosten</span>
  </div>
</section>

<section class="grid">
  <div>
    <h3>Resultaat per dag</h3>
    <p class="note">Draagt één dag het hele resultaat, dan is er geen strategie maar een gelukje.</p>
    {_bar_chart([d['date'] for d in daily], [d['net_pnl'] for d in daily])}
  </div>
  <div>
    <h3>Kerncijfers</h3>
    <ul class="metrics">{metric_rows}</ul>
  </div>
</section>

<section class="duo">
  <div>
    <h3>Signaaltrechter</h3>
    <p class="note">Waarom er niet gehandeld werd.</p>
    {_funnel(stats.get('signals', {}))}
  </div>
  <div>
    <h3>Uitslag per trade</h3>
    <p class="note">Winnaars dicht bij de stop betekent te krap; verliezers met veel
       MFE betekent te laat pakken.</p>
    {_scatter_mae_mfe(trades)}
  </div>
</section>

{_run_history(db, run_id, tz)}

<section>
  <h3>Trades</h3>
  <div class="scroller"><table class="trades"><thead><tr>
    <th>Gesloten</th><th>Kant</th><th class="num">Lots</th><th class="num">In</th>
    <th class="num">Uit</th><th class="num">Bruto</th><th class="num">Kosten</th>
    <th class="num">Netto</th><th>Reden</th>
  </tr></thead><tbody>{trade_rows}</tbody></table></div>
</section>

<footer>
  Technische indicatoranalyse, geen financieel advies. Papermodus simuleert geen
  requotes, geen spreadverbreding rond nieuws en geen storing in je eigen keten;
  live uitvoering valt daardoor structureel slechter uit dan dit rapport.
</footer>
</div></body></html>"""


def write_report(
    db: TradeDatabase,
    run_id: int,
    path: str | Path,
    gate: dict | None = None,
    tz=None,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_report(db, run_id, gate, tz=tz), encoding="utf-8")
    return target
