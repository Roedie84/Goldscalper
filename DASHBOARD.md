# Dashboard

Sinds 1.4.0 verschijnt het dashboard vanzelf. Geen knop indrukken, geen YAML
plakken, geen `www/`-map, geen herstart.

## Het keuringsrapport

Na het toevoegen van de integratie staat **Gold Scalper** in je zijbalk, met een
goudkleurig icoon. Klik erop.

Het rapport wordt bij elke keer openen opnieuw gebouwd uit de database, dus wat
je ziet is altijd actueel. Ververs de pagina om bij te werken.

Direct adres, als je het buiten de zijbalk wilt openen:

```
http://homeassistant.local:8123/api/gold_scalper/report
```

### Wat je ziet vóór de eerste trade

Een rapport met de stempel **IN KEURING** en "Nog geen gesloten trades". Dat is
de juiste uitkomst, geen fout. Zodra er posities gesloten worden vullen de
equitycurve, de dagstaven en de tradelijst zich.

De signaaltrechter vult zich wél meteen: die telt élke evaluatie, ook de
afgewezen. Blijft het aantal trades op nul terwijl de evaluaties oplopen, dan
staat daar waaróm.

### Beveiliging, eerlijk benoemd

Het paneel vraagt geen authenticatie. Dat moet: een iframe in de Home
Assistant-frontend stuurt geen bearer-token mee, dus met authenticatie aan zou
het paneel simpelweg leeg blijven.

Gevolg: iedereen die je Home Assistant kan bereiken, kan dit rapport lezen. Er
staan handelsresultaten, posities en statistieken in — **geen** API-tokens,
account-ID's of inloggegevens. Die komen in de rapportgenerator niet voor, en
`tests/test_http_panel.py::test_report_never_contains_credentials` bewaakt dat.

Wil je het paneel niet, zet dan **Toon 'Gold Scalper' in de zijbalk** uit bij
de opties. Het adres blijft dan wel werken.

## Het Lovelace-dashboard

Voor live entiteiten in plaats van een momentopname.

Instellingen → Dashboards → **Nieuw dashboard toevoegen** → open het → potlood
rechtsboven → driepuntsmenu → **Ruwe configuratie-editor**. Plak de inhoud van
`dashboard/lovelace.yaml`.

Wil je het rapport eronder, voeg dan toe:

```yaml
      - type: iframe
        url: /api/gold_scalper/report
        aspect_ratio: 180%
```

## Als je niets ziet

Loop dit af, in deze volgorde:

**1. Laadt de integratie?** Instellingen → Apparaten en diensten → Gold
Scalper. Staat daar een foutmelding, kijk dan in Instellingen → Systeem →
Logboek.

**2. Zijn er entiteiten?** Klik door naar het apparaat. Je hoort er ruim twintig
te zien, waaronder `sensor.gold_scalper_koers`. Staat die op `onbekend`, dan
komt er geen data binnen.

**3. Staat het menu-item er?** Zo niet, ververs je browser hard (Ctrl+Shift+R).
Home Assistant cachet de zijbalk.

**4. Werkt het adres rechtstreeks?** Open
`http://homeassistant.local:8123/api/gold_scalper/report` in een tabblad. Krijg
je daar wel iets en in de zijbalk niet, dan is het een cacheprobleem.

## Waar te beginnen

De eerste dagen zijn twee entiteiten het interessantst.

`sensor.gold_scalper_spread` — bij publieke marktdata is dit je *aanname*, niet
een meting. Staat hij op 0, dan zijn de kosten uitgeschakeld en is elk
resultaat fictief.

`sensor.gold_scalper_evaluaties` — de trechter. Blijft `acted` op nul met
`edge_below_cost` als voornaamste reden, dan is je tijdsframe te laag voor de
spread en is M15 de volgende stap.
