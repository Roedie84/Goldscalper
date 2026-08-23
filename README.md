# Gold Scalper

Home Assistant-integratie die XAU/USD analyseert, in papermodus handelt en elke
trade met volledige kostentoerekening vastlegt in een SQLite-database.

**Alles draait binnen Home Assistant.** Geen Windows, geen tweede machine, geen
tweede proces. `manifest.json` heeft een lege `requirements`-lijst; er wordt
alleen `aiohttp` gebruikt, dat HA al bevat.

**Live handel is vergrendeld tot de bewijsfase slaagt.**

---

## De rekensom die alles bepaalt

Bij XAU/USD betaal je per round trip de spread plus slippage. Bij goud rond de
$3.300 is een spread van $0,25 gelijk aan 0,0076% van de notional. Klinkt
verwaarloosbaar. Bij twee trades per minuut, acht uur per dag:

- 960 trades × 0,0076% = **7,3% van je notional per dag aan transactiekosten**
- Over een handelsmaand: ruim 150%

Je strategie moet dus gemiddeld meer dan 25 dollarcent per ounce netto pakken
op élke trade, verliezers meegerekend, alleen om quitte te spelen. Goud beweegt
in een actieve minuut ongeveer $1 tot $3.

Zelfs een gelukte scalp van 50 cent ziet er zo uit:

```
bruto (op mid-price)  +5,00 USD
spread                -2,50 USD
commissie             -0,70 USD
──────────────────────────────
netto                 +1,80 USD      64% ging op aan kosten
```

Daarom belast de papersimulatie spread, slippage en commissie volledig door.
Een simulator die op mid-price vult laat winst zien die niet bestaat, en dat is
gevaarlijker dan geen simulator.

### Wat de simulatie liet zien

| Spread | Trades uit 2.940 evaluaties |
|---|---|
| 0,35 (AvaTrade-niveau) | **0** |
| 0,12 (raw spread) | 35, netto nog steeds negatief |

Bij 0,35 werd elke kandidaat afgewezen op `edge_below_cost`. Met een doel van
1,5×ATR en de eis dat dat doel het dubbele van de kosten bedraagt, is een ATR
van minimaal 0,52 USD nodig. De M1-ATR van goud ligt daar doorgaans onder.

**M1-scalpen op goud met enkele orders per minuut is rekenkundig niet haalbaar
bij een brede spread.** De uitwegen: een smallere spread, een hoger tijdsframe
(M15 heeft een ATR van ruwweg 2-3 USD), of de conclusie dat het niet werkt. Dat
laatste is ook een geldige uitkomst van een bewijsfase.

---

## Installatie

### 1. OANDA-account

MetaTrader vereist Windows en AvaTrade biedt geen publieke REST-API, dus de
uitvoering loopt via OANDA.

1. Open een account — begin met **practice**, niet live.
2. Accountportaal → My Services → Manage API Access → token genereren.
3. Noteer je account-ID (formaat `001-004-1234567-001`).

Token en account-ID gaan in je HA-configuratie op je eigen machine. Ze verlaten
die machine niet en de diagnostics-export redigeert ze. Deel ze met niemand.

### 2. Plaatsen

```
config/custom_components/gold_scalper/
```

Herstart HA, dan Instellingen → Apparaten en diensten → Gold Scalper.

### 3. Papermodus

Zet `switch.gold_scalper_handel_actief` aan. Analyseren, papierhandel en
vastleggen kosten niets. Druk na een paar weken op **Keuringsrapport maken**.

---

## Architectuur

```
Home Assistant (Linux / Pi / HA OS)
├── coordinator          handelslus, elke 20 s
├── strategy/            signalen, incrementele indicatoren
├── broker/
│   ├── adapter.py       abstracte uitvoeringslaag
│   ├── oanda.py         REST, alleen aiohttp
│   ├── paper.py         simulatie met volledige kosten
│   ├── exits.py         break-even, trailing, tijdstops
│   └── risk.py          noodremmen
├── storage/             SQLite-ledger en prestatieanalyse
├── modes.py             poort tussen papier en live
├── lifecycle.py         afwikkelen en afstemmen
└── dashboard/           HTML-keuringsrapport
                             │
                             └── HTTPS ──► OANDA v20
```

`broker/adapter.py` definieert wat elke venue moet kunnen. Alles daarboven kent
alleen die interface; een andere REST-broker toevoegen raakt één bestand.

Twee vertalingen zitten bewust alleen in de adapter:

**Eenheden.** OANDA rekent XAU_USD in units van één ounce, MetaTrader in lots
van honderd. Naar buiten toe praat elke venue in ounces, zodat er nergens een
factor 100 kan wegvallen. Daar staat een aparte test op.

**Richting.** Bij OANDA is een short een negatief aantal units, geen aparte
ordersoort.

---

## Modi

| Modus | Data | Uitvoering | Vergrendeld? |
|---|---|---|---|
| `backtest` | historisch | gesimuleerd | nee |
| `paper` | live markt | gesimuleerd, volledige kosten | nee |
| `live` | live markt | **echte orders** | ja |

### De poort naar live

| Criterium | Eis |
|---|---|
| Trades | ≥ 500 |
| Verstreken kalendertijd | ≥ 30 dagen |
| Verschillende handelsdagen | ≥ 15 |
| Prestatie-oordeel | geslaagd |
| Winstverdeling | beste dag ≤ 50% van totaal |

De duureis staat los van het aantal trades: duizend trades in twee dagen zeggen
alleen iets over die twee dagen. De laatste eis vangt het geval af waarin één
gelukkige dag de statistiek draagt.

De poort is niet vanuit de UI te overrulen. Wie hem toch wil passeren past de
broncode aan — dan staat het in je git-historie.

---

## Onbeheerd draaien

De bot draait zonder toezicht. Wat telt is niet hoe goed hij handelt op een
goede dag, maar hoeveel schade hij aanricht op een slechte terwijl jij op je
werk zit.

| Limiet | Standaard | Gevolg |
|---|---|---|
| Dagverlies | 2% | **noodstop** |
| Equity-ondergrens | 80% | **noodstop** |
| Trades per dag | 100 | **noodstop** |
| Geen tickdata | 30 s | **noodstop** |
| Verliezers op rij | 5 | pauze 60 min |
| Spread | boven limiet | trade geweigerd |
| Positieduur | 900 s | geforceerd gesloten |

Een noodstop hervat niet vanzelf, ook niet bij de dagwissel — automatisch
hervatten betekent dat dezelfde storing zich in een lus kan herhalen. Gebruik
`gold_scalper.resume` nadat je de oorzaak hebt vastgesteld.

Deze limieten beschermen tegen weglopend gedrag, niet tegen een verliesgevende
strategie. Daar is de bewijsfase voor.

---

## Herstarten van Home Assistant

**Laag 1 — server-side stops.** De stop-loss gaat mee met de order zelf
(`stopLossOnFill`) en staat op OANDA's server. Hij overleeft een crash van HA,
een netwerkstoring én het uitvallen van je hele machine. Dit is de
belangrijkste bescherming.

Wat de broker níet doet is trailing en break-even bijwerken; dat vereist een
draaiende bot. Valt HA uit terwijl een positie in de winst staat, dan blijft de
laatst geplaatste stop staan.

**Laag 2 — afwikkelen vóór een geplande herstart.**

```yaml
automation:
  - alias: "Afwikkelen voor HA-update"
    trigger:
      - platform: state
        entity_id: update.home_assistant_core_update
        attribute: in_progress
        to: true
    action:
      - service: gold_scalper.prepare_shutdown
      - wait_template: >
          {{ is_state('binary_sensor.gold_scalper_veilig_herstarten', 'on') }}
        timeout: "00:05:00"
```

De HA-shutdown-hook is hier ongeschikt voor: die krijgt beperkt tijd en een
positie afwikkelen kan minuten duren. Bij het stop-event wordt alleen de
administratie weggeschreven.

**Laag 3 — afstemmen bij het opstarten.**

| Situatie | Gevolg |
|---|---|
| Database en broker komen overeen | handel hervat |
| Trade in database, gesloten bij broker | administratie bijgewerkt |
| **Positie bij broker, onbekend in database** | **handel geblokkeerd** |

Dat laatste geval is de gevaarlijke: een positie die niemand bewaakt. Een bot
die niet weet welke posities hij heeft kan dubbel openen of een stop plaatsen
op iets dat niet bestaat.

---

## Uitstappen met winst

| Mechanisme | Trigger | Effect |
|---|---|---|
| Break-even | ≥ 0,8×ATR | stop naar instap + 1,2× kosten |
| Gedeeltelijk sluiten | ≥ 1,0×ATR | 50% dicht, rest loopt door |
| Trailing | ≥ 1,5×ATR | volgt op 1,2×ATR, nooit terug |
| Tijdstop | 240 s binnen ±0,3×ATR | sluiten |
| Harde limiet | 900 s | sluiten |

Voorbeeld, long op 3300,00 met ATR 0,40:

| sec | bid | winst | actie |
|---|---|---|---|
| 30 | 3300,35 | 0,87×ATR | stop naar 3300,47 — **kan niet meer verliezen** |
| 60 | 3300,45 | 1,12×ATR | 50% dicht, +2,25 USD |
| 90 | 3300,70 | 1,75×ATR | hold (trailing zou lager liggen) |
| 150 | 3301,40 | 3,50×ATR | stop naar 3300,92 |

Twee details die vaak fout gaan: afstanden worden getoetst tegen de prijs waar
je écht uitstapt (bid voor een long), niet de mid — anders schuift break-even
een halve spread te vroeg. En de trailing stop beweegt nooit terug; zie de rij
op 90 seconden.

---

## Dashboard

**Keuringsrapport** — zelfstandig HTML, geen CDN, geen scripts. Openen, mailen,
archiveren; werkt over twee jaar nog.

De vormgeving volgt het keuringsrapport van een goudsmid. Goud keuren is
vaststellen of het echt is, en fijnheid wordt uitgedrukt in duizendsten.

**Rendementsfijnheid** = duizendsten van de gevangen marktbeweging die de
kosten overleven. 64% kostenverlies is 360 fijn. Netto negatief is 0 — onder
nul bestaat geen fijnheid, net als bij metaal.

| Paneel | Waarom |
|---|---|
| Equitycurve **met kostenlijn** | ligt de kostenlijn erboven, dan verdient de broker en jij niet |
| Resultaat per dag | draagt één dag alles, dan is er geen strategie |
| Signaaltrechter | waarom er *niet* gehandeld werd, per reden |
| MAE/MFE | winnaars dicht bij de stop = te krap; verliezers met veel MFE = te laat |

**Lovelace** — `dashboard/lovelace.yaml`, noodbediening bovenaan.

---

## Prestaties

| | Voor | Na |
|---|---|---|
| Indicatoren per tick | 1.007 µs | 5,3 µs (188×) |
| Databaseschrijven per rij | 135 µs | 8,4 µs (16×) |

De incrementele indicatoren zijn numeriek identiek aan de batchversie; grootste
afwijking 4,7e-9. `tests/test_streaming.py` dwingt dat af — snelheid die de
cijfers verandert is geen optimalisatie maar een bug.

Trades gaan bewust níet door de schrijfbuffer: dat is je bewijsmateriaal.

De resterende latency zit in de HTTP-hop naar OANDA en de HA event loop, niet
in deze code. Snelheid verandert je kostprijs per ounce trouwens nauwelijks;
ze koopt hooguit iets minder slippage.

---

## Entiteiten

| Entiteit | Waarvoor |
|---|---|
| `sensor.*_koers` / `_spread` / `_atr` | markttoestand |
| `sensor.*_signaal` | score, componenten, afwijsreden |
| `sensor.*_nettoresultaat` / `_kosten` | bruto en netto gescheiden |
| `sensor.*_oordeel` | uitkomst bewijsfase |
| `sensor.*_evaluaties` | signaaltrechter |
| `sensor.*_risicobewaking` | noodremtoestand |
| `binary_sensor.*_veilig_herstarten` | mag HA nu herstart worden |
| `binary_sensor.*_live_vrijgegeven` | is de poort open |
| `binary_sensor.*_noodstop` | noodstop actief |
| `switch.*_handel_actief` | hoofdschakelaar |

Services: `prepare_shutdown`, `close_all`, `resume`, `generate_report`.

---

## Database

`config/gold_scalper.db`, vier tabellen: `runs`, `trades`, `signals`, `equity`.

`gross_pnl` en `net_pnl` staan in aparte kolommen en `total_cost` is per
definitie het verschil, zodat de kostenpost niet in een samenvattend getal kan
verdwijnen. De `signals`-tabel legt élke evaluatie vast, ook de afgewezen —
zonder die kun je achteraf niet zien of je filters te streng stonden.

```sql
SELECT COUNT(*) trades,
       ROUND(SUM(gross_pnl),2) bruto,
       ROUND(SUM(total_cost),2) kosten,
       ROUND(SUM(net_pnl),2)   netto
FROM trades WHERE close_time IS NOT NULL;
```

---

## Wat dit niet is

Er kijkt niemand mee. Wat draait is een Python-proces op jouw machine. Loopt
het vast met een open positie terwijl jij er niet bent, dan merkt niemand dat
op behalve de server-side stop en de noodremmen.

Papermodus simuleert geen requotes, geen spreadverbreding rond nieuws en geen
storing in je eigen keten. Live uitvoering valt daardoor structureel slechter
uit dan het rapport laat zien.

Technische indicatoranalyse is geen financieel advies en voorspelt koersen niet
betrouwbaar.

---

## Tests

```bash
python -m pytest tests/ -q     # 97 tests
```

Draait zonder Home Assistant geïnstalleerd: `tests/conftest.py` stubt de
HA-imports, zodat de rekenkern overal verifieerbaar is.
