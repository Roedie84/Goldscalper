# Dashboard zichtbaar maken

Twee dashboards, twee routes. De eerste werkt zonder dat je iets configureert.

---

## 1. Het keuringsrapport

Een zelfstandig HTML-bestand met equitycurve, kostenlijn, signaaltrechter,
MAE/MFE-spreiding en de tradelijst.

### Genereren

Druk op **Keuringsrapport maken** (de knop-entiteit), of:

```yaml
service: gold_scalper.generate_report
```

Het bestand komt in `config/www/gold_scalper_rapport.html`.

Dat pad is niet willekeurig. Home Assistant serveert alles in de `www`-map op
`/local/`. Dat is de enige manier om een eigen HTML-bestand in de UI te tonen
zonder een extra add-on of losse webserver.

Bestaat `config/www/` nog niet, dan maakt de integratie hem aan. Na het
aanmaken van die map is **één keer een HA-herstart** nodig voordat `/local/`
werkt — daarna niet meer.

### Direct openen

```
http://homeassistant.local:8123/local/gold_scalper_rapport.html
```

Ververst de browser hem niet na een nieuw rapport, zet er dan een cache-buster
achter: `?v=2`.

### Als kaart op je dashboard

Bewerk je dashboard, voeg een kaart toe, kies **Webpage**:

```yaml
type: iframe
url: /local/gold_scalper_rapport.html
aspect_ratio: 150%
```

### Als eigen menu-item in de zijbalk

In `configuration.yaml`:

```yaml
panel_iframe:
  gold_scalper_rapport:
    title: "Keuringsrapport"
    icon: mdi:gold
    url: "/local/gold_scalper_rapport.html"
    require_admin: true
```

Herstart HA. Het rapport staat nu als eigen item in je zijbalk.

### Automatisch elke ochtend verversen

```yaml
automation:
  - alias: "Keuringsrapport bijwerken"
    trigger:
      - platform: time
        at: "07:00:00"
    action:
      - service: gold_scalper.generate_report
```

---

## 2. Het Lovelace-dashboard

Live entiteiten in plaats van een momentopname.

### Plaatsen

Instellingen → Dashboards → **Nieuw dashboard toevoegen** → geef het een naam →
open het → rechtsboven het potlood → driepuntsmenu → **Ruwe configuratie-editor**.

Plak de inhoud van `dashboard/lovelace.yaml`. Opslaan.

### Wat erop staat

De noodbediening staat bovenaan. Als je die nodig hebt, wil je niet scrollen.

| Blok | Inhoud |
|---|---|
| Toestand | modus, levenscyclus, veilig herstarten |
| Bediening | hoofdschakelaar, afwikkelen, alles sluiten |
| Resultaat | netto, kosten, winstpercentage, profit factor, t-statistiek |
| Equity | historiegrafiek met cumulatieve kosten ernaast |
| Markt | koers, spread, ATR, signaal |
| Signaaltrechter | waarom er niet gehandeld werd, per reden |
| Bewijsfase | welke poortcriteria wel en niet gehaald zijn |
| Risico | noodstoptoestand, verliesreeks, dagverlies |

### Beide combineren

Voeg de iframe-kaart onderaan het Lovelace-dashboard toe:

```yaml
      - type: iframe
        url: /local/gold_scalper_rapport.html
        aspect_ratio: 180%
```

Live cijfers boven, het volledige keuringsrapport eronder.

---

## Waar te beginnen

De eerste dagen zijn twee entiteiten het interessantst.

`sensor.gold_scalper_spread` vertelt wat je broker werkelijk rekent voor goud.
Kijk naar de spreiding over de dag, niet naar het gemiddelde: de spread rond
nieuwsmomenten bepaalt of scalpen kan.

`sensor.gold_scalper_evaluaties` toont de trechter. Blijft `acted` op nul staan
met `edge_below_cost` als voornaamste reden, dan is je tijdsframe te laag voor
de spread van je broker en is M15 de volgende stap.

Het keuringsrapport wordt pas zinvol na een paar honderd trades. Daarvóór staat
er "onvoldoende data", en dat is de juiste uitkomst.

---

## Eerst proberen zonder account

Kies bij het toevoegen van de integratie **Simulator**. Geen token, geen
account, geen netwerk: de koersen worden lokaal gegenereerd.

Wat je daarmee kunt controleren:

- laden de entiteiten en vullen ze zich
- werkt de hoofdschakelaar
- verschijnen er papertrades in de database
- schuiven de stops op bij winst (break-even, trailing)
- vult het keuringsrapport zich
- werkt `prepare_shutdown` en gaat `veilig_herstarten` daarna aan

De spread is instelbaar. Zet hem eens op 0,12 en daarna op 0,35, en kijk wat
er met `sensor.gold_scalper_evaluaties` gebeurt. In een testdag van 20 uur gaf
dat dit:

| Spread | Trades | Bruto | Kosten | Netto |
|---|---|---|---|---|
| 0,12 | 12 | +5,50 | 2,44 | **+3,06** |
| 0,35 | 8 | +2,44 | 3,52 | **−1,09** |

Dezelfde markt, dezelfde strategie. De spread alleen draait het teken om.

**Wat je hiermee niet kunt vaststellen: of de strategie werkt.** Synthetische
data heeft geen marktstructuur. Winst hier is een eigenschap van de
ruisgenerator, niet van goud. `LiveGate` weigert een simulatorrun daarom
categorisch vrij te geven — ook bij duizenden winstgevende trades. Zie
`tests/test_simulator.py::test_gate_blocks_simulated_run_even_when_profitable`.
