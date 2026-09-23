"""Wat had elke indicator opgeleverd?

Indicatoren hoeven niet verzameld te worden: het zijn berekeningen op de prijs,
en die prijzen staan in het barsarchief. Deze module rekent ze achteraf uit
voor elke bar, en toetst per indicator of hij de uitkomst had kunnen
voorspellen.

**Het gevaar van deze vraag.** Twintig indicatoren toetsen op dezelfde data
vindt er altijd een paar die "werken" - puur door toeval. Bij twintig toetsen
op t = 2,0 is de kans op minstens één vals positief ongeveer 64 procent. Een
lijst met winnaars uit zo'n zoektocht is een lijst met toevalstreffers.

Daarom twee beveiligingen die in de code vastliggen, niet in een instelling:

1. **Ontdekken en bevestigen zijn gescheiden.** De eerste 70% van de bars
   bepaalt per indicator de drempels en de richting. De laatste 30% toetst die
   vaste regel, zonder er iets aan te veranderen. Een indicator die alleen in
   het eerste deel werkt, is aangepast aan dat deel.

2. **De lat stijgt met het aantal indicatoren.** Wie er twintig toetst, moet
   voor elk een t van ongeveer 3,0 halen in plaats van 2,0 (Bonferroni). Dat
   is streng, en dat hoort zo: het is de prijs van veel proberen.

Wat hier wél mag en parameteroptimalisatie niet: er wordt niets afgesteld op de
bevestigingsperiode. De indicator-instellingen zijn de standaardwaarden uit de
literatuur, vooraf vastgelegd.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Callable

from .signals import Candles
from . import momentum, trend, volatility

#: Deel van de reeks voor ontdekken; de rest is voor bevestigen.
DISCOVERY_SHARE = 0.70

#: Minimaal aantal trades in de bevestigingsperiode voor een oordeel.
MIN_HOLDOUT_TRADES = 30


def _laatste(reeks) -> list:
    """Een reeks naar een gewone lijst, met None waar geen waarde is."""
    return [None if v is None else float(v) for v in reeks]


def _verschil(a, b) -> list:
    return [
        None if x is None or y is None else float(x) - float(y)
        for x, y in zip(a, b)
    ]


def _positie(close, onder, boven) -> list:
    """Waar staat de koers binnen een band: 0 onderaan, 1 bovenaan."""
    uit = []
    for c, lo, hi in zip(close, onder, boven):
        if c is None or lo is None or hi is None or hi == lo:
            uit.append(None)
        else:
            uit.append((c - lo) / (hi - lo))
    return uit


def _atr_verhouding(candles: Candles) -> list:
    a = volatility.atr(candles, 14)
    uit = []
    for i in range(len(a)):
        venster = [x for x in a[max(0, i - 59):i + 1] if x is not None]
        if len(venster) < 20 or a[i] is None:
            uit.append(None)
        else:
            m = statistics.median(venster)
            uit.append(a[i] / m if m else None)
    return uit


def _psar_kant(candles: Candles) -> list:
    sar = volatility_safe(trend.parabolic_sar, candles)
    return [
        None if s is None else (1.0 if c > s else -1.0)
        for c, s in zip(candles.close, sar)
    ]


def volatility_safe(functie, *args):
    try:
        return functie(*args)
    except Exception:  # noqa: BLE001
        return [None] * len(args[0])


#: De indicatoren, met standaardinstellingen uit de literatuur.
#:
#: Volumegebaseerde indicatoren ontbreken bewust: bij een CFD is het volume van
#: de broker een tikteller en geen werkelijk handelsvolume. Een indicator op
#: een nepgetal levert een nepresultaat.
INDICATOREN: dict[str, Callable[[Candles], list]] = {
    "rsi_14": lambda c: _laatste(momentum.rsi(c.close, 14)),
    "williams_r_14": lambda c: _laatste(momentum.williams_r(c, 14)),
    "cci_20": lambda c: _laatste(momentum.cci(c, 20)),
    "stochastic_k": lambda c: _laatste(momentum.stochastic(c)[0]),
    "stoch_rsi_k": lambda c: _laatste(momentum.stoch_rsi(c.close)[0]),
    "chande_14": lambda c: _laatste(momentum.chande_momentum(c.close, 14)),
    "tsi": lambda c: _laatste(momentum.true_strength_index(c.close)),
    "ultimate": lambda c: _laatste(momentum.ultimate_oscillator(c)),
    "awesome": lambda c: _laatste(momentum.awesome_oscillator(c)),
    "ppo": lambda c: _laatste(momentum.ppo(c.close)),
    "macd_hist": lambda c: _laatste(trend.macd(c.close)[2]),
    "trix_15": lambda c: _laatste(trend.trix(c.close, 15)),
    "adx_14": lambda c: _laatste(trend.adx(c, 14)[0]),
    "di_verschil": lambda c: _verschil(*trend.adx(c, 14)[1:3]),
    "aroon_osc": lambda c: _verschil(*trend.aroon(c, 25)),
    "vortex_verschil": lambda c: _verschil(*trend.vortex(c, 14)),
    "psar_kant": _psar_kant,
    "bollinger_pos": lambda c: _positie(
        c.close, volatility.bollinger(c.close)[2], volatility.bollinger(c.close)[0]
    ),
    "keltner_pos": lambda c: _positie(
        c.close, volatility.keltner(c)[2], volatility.keltner(c)[0]
    ),
    "donchian_pos": lambda c: _positie(
        c.close, volatility.donchian(c)[2], volatility.donchian(c)[0]
    ),
    "atr_verhouding": _atr_verhouding,
    "hist_volatiliteit": lambda c: _laatste(
        volatility.historical_volatility(c.close)
    ),
}


def drempel_t(aantal_toetsen: int, alpha: float = 0.05) -> float:
    """De t-waarde die bij dit aantal toetsen nodig is (Bonferroni).

    Bij één toets is dat 2,0; bij twintig ongeveer 3,0.
    """
    doel = alpha / max(1, aantal_toetsen)
    t = 1.5
    while t < 6.0:
        if math.erfc(t / math.sqrt(2)) <= doel:
            return round(t, 2)
        t += 0.01
    return 6.0


# ---------------------------------------------------------------------------
# uitkomsten
# ---------------------------------------------------------------------------

def _uitkomst(
    candles: Candles, i: int, richting: int, atr: float,
    doel: float, stop: float, max_bars: int,
) -> tuple[float, int]:
    """Resultaat per ounce van een trade op de slotkoers van bar i.

    Geeft (resultaat, aantal bars) terug. Raken stop en doel dezelfde bar,
    dan telt de stop: binnen een bar is de volgorde onbekend, en de gunstige
    aanname zou elke uitkomst te rooskleurig maken.
    """
    instap = candles.close[i]
    doelprijs = instap + richting * doel * atr
    stopprijs = instap - richting * stop * atr
    einde = min(len(candles) - 1, i + max_bars)
    for j in range(i + 1, einde + 1):
        hoog, laag = candles.high[j], candles.low[j]
        stop_geraakt = laag <= stopprijs if richting > 0 else hoog >= stopprijs
        doel_geraakt = hoog >= doelprijs if richting > 0 else laag <= doelprijs
        if stop_geraakt:
            return -stop * atr, j - i
        if doel_geraakt:
            return doel * atr, j - i
    return (candles.close[einde] - instap) * richting, einde - i


# ---------------------------------------------------------------------------
# resultaten
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class IndicatorResult:
    naam: str
    richting: str = ""
    trades: int = 0
    trefkans: float = 0.0
    per_trade: float = 0.0
    t: float = 0.0
    ontdekking_per_trade: float = 0.0
    geslaagd: bool = False
    reden: str = ""

    def as_dict(self) -> dict:
        return {
            "indicator": self.naam,
            "regel": self.richting,
            "trades": self.trades,
            "trefkans": round(self.trefkans, 1),
            "per_trade": round(self.per_trade, 3),
            "t": round(self.t, 2),
            "ontdekking_per_trade": round(self.ontdekking_per_trade, 3),
            "geslaagd": self.geslaagd,
            "reden": self.reden,
        }


@dataclass(slots=True)
class LabReport:
    bars: int = 0
    ontdekking_bars: int = 0
    bevestiging_bars: int = 0
    toetsen: int = 0
    drempel: float = 0.0
    kosten: float = 0.0
    nullijn: dict = field(default_factory=dict)
    resultaten: list = field(default_factory=list)
    conclusie: str = ""

    def as_dict(self) -> dict:
        return {
            "bars": self.bars,
            "ontdekking_bars": self.ontdekking_bars,
            "bevestiging_bars": self.bevestiging_bars,
            "toetsen": self.toetsen,
            "t_drempel": self.drempel,
            "kosten_per_trade": self.kosten,
            "nullijn": self.nullijn,
            "resultaten": [r.as_dict() for r in self.resultaten],
            "geslaagd": [r.naam for r in self.resultaten if r.geslaagd],
            "conclusie": self.conclusie,
        }


def _simuleer(
    candles, atr, waarden, start, eind, regel, doel, stop, max_bars, kosten,
) -> list[float]:
    """Achtereenvolgende trades volgens een vaste regel, zonder overlap.

    Zonder overlap, omdat elkaar overlappende trades bijna dezelfde uitkomst
    hebben en de t-waarde dan kunstmatig opblazen: twintig keer dezelfde
    beweging telt dan als twintig onafhankelijke bewijzen.
    """
    laag, hoog, kant_hoog = regel
    uitkomsten = []
    i = start
    while i < eind:
        w, a = waarden[i], atr[i]
        if w is None or not a:
            i += 1
            continue
        richting = 0
        if w >= hoog:
            richting = kant_hoog
        elif w <= laag:
            richting = -kant_hoog
        if not richting:
            i += 1
            continue
        res, duur = _uitkomst(candles, i, richting, a, doel, stop, max_bars)
        uitkomsten.append(res - kosten)
        i += max(1, duur)
    return uitkomsten


def _t(reeks: list[float]) -> float:
    if len(reeks) < 2:
        return 0.0
    sd = statistics.stdev(reeks)
    if sd == 0:
        return 0.0
    return statistics.mean(reeks) / (sd / math.sqrt(len(reeks)))


def run_lab(
    candles: Candles,
    *,
    doel: float = 1.5,
    stop: float = 1.0,
    max_bars: int = 16,
    kosten: float = 0.75,
) -> LabReport:
    """Toets elke indicator op het archief.

    `kosten` is per ounce per trade, heen en terug. De standaard van 0,75 komt
    uit de gemeten live-kosten: 1,20 per trade bij ongeveer 1,6 ounce.
    """
    rapport = LabReport(bars=len(candles), kosten=kosten)
    if len(candles) < 400:
        rapport.conclusie = (
            f"{len(candles)} bars in het archief; minstens 400 nodig voor een "
            "ontdekkings- en bevestigingsperiode die elk iets zeggen."
        )
        return rapport

    grens = int(len(candles) * DISCOVERY_SHARE)
    rapport.ontdekking_bars = grens
    rapport.bevestiging_bars = len(candles) - grens
    rapport.toetsen = len(INDICATOREN)
    rapport.drempel = drempel_t(rapport.toetsen)

    atr = _laatste(volatility.atr(candles, 14))

    # De nullijn: altijd long, altijd short. Zonder dit ijkpunt is niet te zien
    # of een indicator iets toevoegt, of alleen meelift op een markt die de
    # hele periode één kant op ging.
    for naam, kant in (("altijd_long", 1), ("altijd_short", -1)):
        uit = []
        i = grens
        while i < len(candles) - 1:
            if atr[i]:
                res, duur = _uitkomst(candles, i, kant, atr[i], doel, stop, max_bars)
                uit.append(res - kosten)
                i += max(1, duur)
            else:
                i += 1
        rapport.nullijn[naam] = {
            "trades": len(uit),
            "per_trade": round(statistics.mean(uit), 3) if uit else 0.0,
            "t": round(_t(uit), 2),
        }

    for naam, functie in INDICATOREN.items():
        res = IndicatorResult(naam=naam)
        try:
            waarden = functie(candles)
        except Exception as err:  # noqa: BLE001
            res.reden = f"niet te berekenen: {err}"
            rapport.resultaten.append(res)
            continue

        ontdekking = [w for w in waarden[:grens] if w is not None]
        verschillend = sorted(set(ontdekking))
        if len(ontdekking) < 100 or len(verschillend) < 2:
            res.reden = "te weinig of te weinig verschillende waarden"
            rapport.resultaten.append(res)
            continue

        if len(verschillend) == 2:
            # Een binaire indicator (boven of onder, aan of uit): de twee
            # waarden zijn zelf de groepen. Derden bestaan hier niet.
            laag, hoog = verschillend
        else:
            # Drempels uit de ontdekkingsperiode: bovenste en onderste derde.
            gesorteerd = sorted(ontdekking)
            laag = gesorteerd[len(gesorteerd) // 3]
            hoog = gesorteerd[2 * len(gesorteerd) // 3]

        # De richting kiezen in de ontdekkingsperiode: meegaan met een hoge
        # waarde, of ertegenin. Dat is één keuze per indicator, en die telt
        # mee in de toetsing omdat de bevestiging hem daarna vast houdt.
        mee = _simuleer(candles, atr, waarden, 0, grens,
                        (laag, hoog, 1), doel, stop, max_bars, kosten)
        tegen = _simuleer(candles, atr, waarden, 0, grens,
                          (laag, hoog, -1), doel, stop, max_bars, kosten)
        gem_mee = statistics.mean(mee) if mee else -1e9
        gem_tegen = statistics.mean(tegen) if tegen else -1e9
        kant = 1 if gem_mee >= gem_tegen else -1
        res.ontdekking_per_trade = max(gem_mee, gem_tegen)
        res.richting = (
            "hoog = long, laag = short" if kant == 1
            else "hoog = short, laag = long"
        )

        bevestiging = _simuleer(candles, atr, waarden, grens, len(candles) - 1,
                                (laag, hoog, kant), doel, stop, max_bars, kosten)
        res.trades = len(bevestiging)
        if bevestiging:
            res.per_trade = statistics.mean(bevestiging)
            res.trefkans = sum(1 for x in bevestiging if x > 0) / len(bevestiging) * 100
            res.t = _t(bevestiging)

        if res.trades < MIN_HOLDOUT_TRADES:
            res.reden = (
                f"{res.trades} trades in de bevestiging; minstens "
                f"{MIN_HOLDOUT_TRADES} nodig"
            )
        elif res.per_trade <= 0:
            res.reden = "negatief na kosten in de bevestiging"
        elif res.t < rapport.drempel:
            res.reden = (
                f"t = {res.t:.2f}, onder de drempel van {rapport.drempel:.2f} "
                f"die bij {rapport.toetsen} toetsen hoort"
            )
        else:
            res.geslaagd = True
            res.reden = "positief na kosten en boven de drempel, in de bevestiging"

        rapport.resultaten.append(res)

    rapport.resultaten.sort(key=lambda r: r.t, reverse=True)
    rapport.conclusie = _oordeel(rapport)
    return rapport


def _oordeel(rapport: LabReport) -> str:
    geslaagd = [r for r in rapport.resultaten if r.geslaagd]
    dagen = rapport.bars * 15 / 60 / 24
    if not geslaagd:
        beste = next((r for r in rapport.resultaten if r.trades), None)
        tekst = (
            f"Geen van de {rapport.toetsen} indicatoren haalt in de "
            f"bevestigingsperiode een positief resultaat na kosten met "
            f"t >= {rapport.drempel:.2f}."
        )
        if beste:
            tekst += (
                f" De beste was {beste.naam} met t = {beste.t:.2f} over "
                f"{beste.trades} trades."
            )
        tekst += (
            f" Het archief beslaat ongeveer {dagen:.0f} dagen; bij een langer "
            "archief kan dit veranderen, maar een uitschieter nu is geen "
            "reden om te handelen."
        )
        return tekst

    namen = ", ".join(r.naam for r in geslaagd)
    return (
        f"{len(geslaagd)} indicator(en) geslaagd in de bevestiging: {namen}. "
        f"Dat is een reden om verder te meten, niet om te handelen: het archief "
        f"beslaat ongeveer {dagen:.0f} dagen en één marktperiode. De volgende "
        "stap is een vooraf vastgelegde bewijsfase op demo."
    )
