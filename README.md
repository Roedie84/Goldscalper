# Gold Scalper

Een Home Assistant-integratie die een handelsstrategie op goud (XAU/USD) draait
en — belangrijker — **meet of die strategie werkelijk iets waard is**.

De nadruk ligt op het tweede. Een bot die handelt is eenvoudig; een bot die
eerlijk vaststelt dat hij geen edge heeft, is dat niet.

---

## Huidige stand

**Er is geen edge aangetoond. Het bewijs wijst de andere kant op.**

Na 184 gemeten trades op een demo-account:

| | |
|---|---|
| trefkans | 30,4% |
| nodig voor break-even | 39,3% |
| bruto per trade | **-1,10** |
| t-statistiek | **-2,51** |
| winstfactor | 0,68 |

De bruto edge is negatief: ook zonder kosten verliest de strategie. Alle vijf
gemeten periodes zijn negatief. Bij t = -2,51 is dat niet langer toeval.

Dat is een resultaat, geen mislukking. Het kostte drie weken meten in plaats
van maanden handelen.

---

## Wat deze integratie doet

### Handelen

* Strategie op M15 met trend-, momentum- en structuurcomponenten
* Positiegrootte uit een risicopercentage en de stopafstand
* Stop en doel staan **bij de broker**, dus ook actief als Home Assistant uitvalt
* Break-even, trailing stop en een tijdslimiet
* Circuit breakers: dagverlies, verliesreeks, vermogensvloer

### Meten

Dit is waar het werk in zit.

* **Kosten worden gemeten, niet gemodelleerd** — spread en slippage per trade
* **Uitstapprijzen komen van de broker**, inclusief het bedrag waarmee hij
  werkelijk afrekende. Zelf narekenen uit prijzen gaf steeds afwijkingen
* **Valutaomrekening** tussen instrument- en accountvaluta, met de koers die de
  broker zelf hanteert
* **Brokervergelijking** elke tiende cyclus: omvang, richting, stop, doel
* **Verliesanalyse** die onderscheidt tussen ontwerpfouten en marktgedrag
* **Consistentietoets** over meerdere periodes
* **Barsarchief** dat herstarts overleeft, zodat hypothesen op historie te
  toetsen zijn in plaats van over weken

### De poort naar echt geld

Handel met echt geld is vergrendeld tot:

* ≥ 500 trades, ≥ 30 kalenderdagen, ≥ 15 handelsdagen
* positief oordeel, beste dag ≤ 50% van de winst
* consistentietoets doorstaan
* echte marktdata, kosten meegerekend

Er is geen weg omheen.

---

## Installatie

Via HACS als aangepaste repository, of handmatig:

    custom_components/gold_scalper/  ->  config/custom_components/

Daarna Home Assistant herstarten en de integratie toevoegen via
**Instellingen → Apparaten en diensten**.

### Databronnen

| Bron | Koersen | Handel |
|---|---|---|
| IG / Capital.com | ja | ja |
| OANDA | ja | ja (ongetest tegen een echte verbinding) |
| Yahoo Finance | ja | nee |
| Stooq | dagelijks | nee |
| Simulator | synthetisch | gesimuleerd |

Zie `VENUES.md` en `BROKERS.md`.

---

## Diensten

| Dienst | Waarvoor |
|---|---|
| `backtest` | strategie over het barsarchief draaien; met `invert: true` het signaal omgedraaid |
| `validate_backtest` | toetst of de backtest klopt met wat er live gebeurde |
| `import_history` | archief vullen met bars van de broker |
| `recheck_exits` | uitstapprijzen opnieuw ophalen |
| `new_run` | bewust een nieuwe bewijsfase beginnen |
| `generate_report` | keuringsrapport schrijven |
| `close_all`, `resume`, `reset_day`, `prepare_shutdown` | bediening |

---

## Rapport en overzicht

    http://<home-assistant>/api/gold_scalper/overview
    http://<home-assistant>/api/gold_scalper/report

Het keuringsrapport toont de equitycurve met de kostenlijn eroverheen: loopt
die erboven, dan verdient de broker aan de strategie en jij niet.

---

## Ontwikkelen

    python -m pytest tests/ -q

872 tests. Een deel daarvan toetst geen gedrag maar code-eigenschappen: geen
aangeroepen methode die niet bestaat, geen aanroep met te weinig argumenten,
geen eenheidsconstante op twee plekken, elk gedocumenteerd dienstveld in het
schema.

Elk van die controles is toegevoegd nadat de bijbehorende fout zich had
voorgedaan.

---

## Waarschuwing

Handelen in CFD's brengt risico op verlies met zich mee. Deze integratie is
gebouwd om vast te stellen of een strategie werkt — en het antwoord is
voorlopig nee. Gebruik hem als meetinstrument, niet als belegging.
