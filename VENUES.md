# Databronnen

Drie keuzes bij het instellen. Ze verschillen in wat je ermee kunt vaststellen.

| | Simulator | **Marktdata** | OANDA |
|---|---|---|---|
| Account nodig | nee | **nee** | ja |
| Echte goudkoersen | nee | **ja** | ja |
| Echte spread | nee | nee (aanname) | ja |
| Kan handelen | nee | nee | ja |
| Poort naar live | nooit | nooit | mogelijk |

## Marktdata (standaard)

Echte goudkoersen van Yahoo Finance, uitvoering volledig op papier.

- `GC=F` — COMEX goudfutures, meestal de beste minuutgranulariteit
- `XAUUSD=X` — spot XAU/USD, dichter bij wat een CFD-broker quoteert

Minuutdata reikt ongeveer een week terug. Voor langere historie: hoger
tijdsframe.

### Wat je hiermee wél kunt

Zien of de strategie signalen vindt in echte marktbewegingen, hoe vaak de
filters afgaan, of de exits redelijk werken, en hoe de ATR van goud zich
verhoudt tot je winstdoelen.

### Wat je hiermee níet kunt

**De spread wordt aangenomen, niet gemeten.** Publieke bronnen leveren
transactieprijzen, geen bied- en laatprijs. Het getal dat je invult is een gok,
en bij scalping is dat de dominante kostenpost.

**De data is niet die van jouw broker.** Yahoo geeft de futures- of
interbancaire prijs; jouw broker quoteert daaromheen met eigen opslag.
Verschillen van enkele tienden zijn normaal — precies de orde van grootte waar
een scalper op leeft.

**Het endpoint is ongedocumenteerd.** Werkt al jaren, maar kan zonder
aankondiging veranderen.

## Kosten op nul

De aangenomen spread mag op 0 staan. Dan kost een round trip niets en zie je de
machinerie draaien zonder dat kosten alles wegvreten.

Twee dingen gebeuren dan automatisch.

**De kostenprojectie verschijnt in het rapport.** Hetzelfde tradepatroon,
doorgerekend bij realistische spreads. Uit een testrun van 120 trades:

| Scenario | Kosten | Netto |
|---|---|---|
| zoals gedraaid (0,00) | 0,00 | **+6,35** |
| bij spread 0,12 | 14,40 | −8,05 |
| bij spread 0,25 | 30,00 | −23,65 |
| bij spread 0,35 | 42,00 | −35,65 |

Winst slaat om in verlies bij de smalste realistische spread. Die tabel staat
er zodat je nooit naar een winstcijfer kijkt zonder te zien wat ervan
overblijft.

De projectie is nog optimistisch: bij een bredere spread zou de kostenpoort ook
strenger filteren en minder trades doorlaten. De echte uitkomst valt slechter
uit, niet beter.

**De poort naar live blijft dicht** op `kosten_meegerekend = false`, net als bij
de simulator. Een run zonder kosten kan niets bewijzen over echt handelen. Zie
`tests/test_public_data.py::test_gate_blocks_zero_cost_run`.

## Aanbevolen volgorde

1. **Marktdata met spread 0** — werkt alles, komen er signalen door
2. **Marktdata met spread 0,25** — realistisch; blijft er iets over?
3. **OANDA practice** — echte quotes, gemeten spread, gemeten slippage
4. **OANDA live** — alleen als de poort daadwerkelijk opengaat

Stap 2 is waar de meeste strategieën sneuvelen. Dat is geen tegenslag maar de
bedoeling van een bewijsfase.
