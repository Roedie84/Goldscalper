# Entiteiten en bediening

## Belangrijk: entiteit-ID's bevatten het symbool

Home Assistant zet de apparaatnaam vóór elke entiteitnaam. Het apparaat heet
`Gold Scalper XAU_USD`, dus de ID's zien er zo uit:

```
switch.gold_scalper_xau_usd_handel_actief
sensor.gold_scalper_xau_usd_status
sensor.gold_scalper_xau_usd_signaal
```

**Niet** `switch.gold_scalper_handel_actief` — die bestaat niet. Verander je het
symbool of de databron, dan veranderen de ID's mee.

Betrouwbaarste manier om ze te vinden: Instellingen → Apparaten en diensten
→ Gold Scalper → het apparaat aanklikken.

## Beginnen

Één schakelaar, en niets gebeurt zonder.

```yaml
service: switch.turn_on
target:
  entity_id: switch.gold_scalper_xau_usd_handel_actief
```

De stand blijft bewaard tussen herstarts, dus dit hoef je maar één keer te doen.

## Status: waarom gebeurt er niets

`sensor.<...>_status` zegt het in één woord, met een toelichting in het
attribuut `toelichting`.

| Status | Betekenis |
|---|---|
| `uitgeschakeld` | de hoofdschakelaar staat uit |
| `markt_gesloten` | goudmarkt dicht; hervat vanzelf, geen storing |
| `wachtend` | actief, maar nog geen geschikt signaal (reden staat erbij) |
| `positie_open` | er loopt een positie; exits worden bewaakt |
| `gepauzeerd` | tijdelijke pauze na een reeks verliezers |
| `noodstop` | limiet geraakt; vereist `gold_scalper.resume` |
| `afgestemd_probleem` | database en broker oneens over posities |
| `afwikkelen` | bezig met afwikkelen voor een herstart |

## Alle entiteiten

Vervang `<...>` door `gold_scalper_xau_usd` of wat er bij jouw symbool staat.

### Bediening
| Entiteit | Doel |
|---|---|
| `switch.<...>_handel_actief` | hoofdschakelaar, blijft bewaard |
| `button.<...>_alles_sluiten` | noodknop |
| `button.<...>_afwikkelen_voor_herstart` | afwikkelen voor een update |
| `button.<...>_hervatten_na_noodstop` | noodstop opheffen |
| `button.<...>_keuringsrapport_maken` | rapport naar bestand schrijven |

### Markt
| Entiteit | Doel |
|---|---|
| `sensor.<...>_koers` | midprijs |
| `sensor.<...>_spread` | bij marktdata: je *aanname*, geen meting |
| `sensor.<...>_atr` | gemiddelde beweging per candle |
| `sensor.<...>_signaal` | richting, score, componenten, afwijsreden |

### Resultaat
| Entiteit | Doel |
|---|---|
| `sensor.<...>_nettoresultaat` | netto, met bruto en kosten als attribuut |
| `sensor.<...>_kosten` | totale transactiekosten |
| `sensor.<...>_trades` | aantal gesloten trades |
| `sensor.<...>_evaluaties` | signaaltrechter met afwijsredenen |
| `sensor.<...>_oordeel` | uitkomst bewijsfase |

### Bewaking
| Entiteit | Doel |
|---|---|
| `binary_sensor.<...>_noodstop` | limiet geraakt |
| `binary_sensor.<...>_dataprobleem` | OHLCV-kolommen uit de pas |
| `binary_sensor.<...>_modus_genegeerd` | gekozen modus wordt overruled |
| `binary_sensor.<...>_veilig_herstarten` | mag HA nu herstart worden |
| `binary_sensor.<...>_live_vrijgegeven` | staat de poort open |

## Dashboard

Na installatie staat **Gold Scalper** in de zijbalk. Zie je het niet: hard
verversen met Ctrl+Shift+R, of open rechtstreeks
`http://homeassistant.local:8123/api/gold_scalper/report`.

### Hoe vaak wordt het bijgewerkt

Het rapport wordt bij elke aanvraag opnieuw uit de database opgebouwd, en
ververst zichzelf **elke 60 seconden**. Rechtsboven staat wanneer het is
opgemaakt, in jouw lokale tijd.

Zestig seconden is een compromis. De onderliggende data ververst elke twintig
seconden, maar het rapport opbouwen kost bij duizenden trades honderden
milliseconden; drie keer per minuut zou dat verdrievoudigen zonder dat je meer
te zien krijgt.

Aanpassen per aanvraag:

```
/api/gold_scalper/report?refresh=30     sneller (minimaal 15)
/api/gold_scalper/report?refresh=0      uit; alleen handmatig verversen
```

De verversing gebruikt een `meta http-equiv`-tag, geen JavaScript. Bewuste
keuze: het paneel is zonder authenticatie bereikbaar, en dan houd je het
scriptvrij.

### Tijden

De database bewaart alles in UTC — de enige zinnige keuze voor een reeks die
over de zomertijdwissel heen loopt. Het rapport toont de tijdzone die in Home
Assistant is ingesteld: voor Nederland UTC+2 in de zomer, UTC+1 in de winter.

## Lovelace

`dashboard/lovelace.yaml` via Instellingen → Dashboards → nieuw dashboard →
potlood → driepuntsmenu → Ruwe configuratie-editor. **Pas de entiteit-ID's aan**
naar jouw symbool.

## Van databron wisselen

Integratie → driepuntsmenu → **Herconfigureren**. Je tradedatabase blijft
behouden.

## Runs en de bewijsfase

De bewijsfase telt per **run**. Een run loopt door zolang de opzet niet
verandert.

| Actie | Gevolg |
|---|---|
| Home Assistant herstarten | run loopt door |
| Integratie herladen | run loopt door |
| Risicolimiet aanpassen | run loopt door |
| Startbalans aanpassen | run loopt door |
| **Databron wisselen** | nieuwe run |
| **Instapdrempel wijzigen** | nieuwe run |
| **Aangenomen spread wijzigen** | nieuwe run |
| **Handelsvenster wijzigen** | nieuwe run |
| **Strategieversie bijgewerkt** | nieuwe run |

De scheidslijn: alles wat de *signalen* verandert begint een nieuwe run, want
resultaten van voor en na zijn dan niet vergelijkbaar. Je kunt een strategie
niet bewijzen terwijl je hem verandert.

Risicolimieten zitten er bewust niet in. Die begrenzen de schade maar
veranderen niet welke trades er ontstaan.

Eerdere runs blijven in de database en staan onderaan het keuringsrapport, met
aantal trades, kosten en nettoresultaat per run. Er gaat niets verloren; de
teller begint alleen opnieuw waar dat inhoudelijk moet.

`sensor.<...>_status` toont `run_id` en `run_started` als attribuut.

## Wanneer wordt er gehandeld

Standaard: **zodra de markt open is.** Er is geen vast tijdvenster.

Goud handelt bijna 24 uur per dag op werkdagen, met een korte dagelijkse
onderbreking en het hele weekend dicht. Buiten die uren staat de status op
`markt_gesloten` en gebeurt er niets; dat is geen storing en hervat vanzelf.

### Waarom het vaste venster eruit is

Het venster van 7:00-20:00 UTC was een *proxy*. De redenering erachter: buiten
de Londen/New York-overlap is de spread breder en de beweging kleiner. Maar
allebei die dingen worden elders directer gemeten:

- `max_spread` weigert bij een te brede spread
- de volatiliteitscontrole weigert als de markt te stil is om de kosten terug
  te verdienen

Die kijken naar wat er werkelijk gebeurt in plaats van naar de klok. Gemeten
over een etmaal simulatiedata: 63 kandidaten zonder venster tegen 58 met - maar
de bot vond ook kansen in uur 5 en 6, vóór de Londense opening, die het vaste
venster had weggegooid terwijl er beweging was.

### Eén uitzondering waar je op moet letten

Bij een databron zonder echte bied- en laatprijs - simulator, Yahoo, Stooq - is
de spread een **aanname**, en dan is `max_spread` een vergelijking met een
constante die nooit afgaat. Hij filtert dus niets.

In dat geval wordt de volatiliteitsdrempel automatisch strenger gezet
(0,85x mediaan in plaats van 0,6x), want anders is er zonder tijdvenster geen
enkele rem op handelen in dunne uren - precies waar spreads in werkelijkheid
uitlopen. In de testrun werden daardoor 112 evaluaties geweigerd op
`volatility_regime`.

Wil je het venster toch terug, dan staat de schakelaar **Beperk tot een vast
tijdvenster** bij de opties. Let op: dat verandert de vingerafdruk en begint dus
een nieuwe run.

## Meldingen op je telefoon

Bij de opties staat **Meldingen sturen naar**. De keuzelijst wordt gevuld met
de notify-diensten die Home Assistant werkelijk kent, dus je hoeft niets over
te typen. Je telefoon staat er als `mobile_app_<naam van je toestel>`.

Zie je hem niet, dan is de Home Assistant-app op dat toestel nog niet
gekoppeld: open de app, log in, en de dienst verschijnt na een herstart van
Home Assistant.

### Wat je krijgt

**Elk uur een samenvatting** met wat er dat uur gebeurde: aantal trades, het
resultaat over dat uur én het totaal, kosten, trefkans en de huidige status.

Standaard wordt een uur overgeslagen waarin er niets gebeurde. Een bericht dat
elk uur "nul trades" zegt leer je binnen een dag negeren, en dan mis je ook de
berichten die er wél toe doen. Een uur waarin de bot stillag door een noodstop
telt niet als 'niets gebeurd'.

**Directe waarschuwingen** bij:

| Gebeurtenis | Waarom |
|---|---|
| Noodstop | handel ligt stil tot je hervat |
| Posities kloppen niet | database en broker oneens; handel geblokkeerd |
| Positie zonder stop | het enige scenario met onbegrensd verlies |
| Hervat | de noodstop is opgeheven |

Die gaan op de *overgang*, niet op de toestand: een noodstop duurt tot je hem
opheft, en zonder dat onderscheid zou je elke tien seconden hetzelfde bericht
krijgen. Dezelfde waarschuwing wordt hooguit eens per vier uur herhaald.

### Kritieke meldingen

Staat **Noodstop als kritieke melding** aan, dan komt die waarschuwing op iOS
door een stille stand en door Niet storen heen. Bij een handelsbot die zichzelf
heeft stilgelegd is dat gepast; het uurbericht gaat altijd als gewone melding.

## Hervatten na een noodstop

Bij de opties staat **Hervattingen per dag na een noodstop**, standaard twee.

Hervatten verzet het dagijkpunt: het dagverlies telt vanaf dat moment opnieuw.
Zonder dat zou hervatten zinloos zijn - de volgende cyclus ziet dezelfde
overschrijding en stopt meteen weer.

Juist daarom zit er een grens op. Onbeperkt hervatten maakt van de daglimiet
een suggestie: je kunt dan telkens opnieuw hetzelfde percentage verliezen.

| Waarde | Betekenis |
|---|---|
| 0 | een noodstop duurt tot morgen |
| 2 | standaard: ruimte om een storing te herstellen |
| hoger | meer speling, maar de daglimiet verwatert evenredig |

Nul is een verdedigbare keuze voor wie zichzelf niet wil kunnen overrulen. Het
verlies dat al geleden is blijft hoe dan ook in de database en telt mee in je
resultaten; alleen de noodrem begint opnieuw.

De statussensor toont hoeveel hervattingen je vandaag nog hebt.

## Doel en stop instellen

Standaard schalen doel en stop mee met de ATR: **1,5x** voor het doel, **1,0x**
voor de stop. Bij een ATR van 6 is dat een doel van 9 en een stop van 6 USD per
ounce.

Dat meeschalen is bewust. Hetzelfde percentage van de beweging, of goud nu 2 of
12 dollar per bar aflegt.

### Vast bedrag in USD

Bij de opties staan **Doel als vast bedrag** en **Stop als vast bedrag**. Nul
betekent: de ATR gebruiken. Vul je er iets in, dan geldt dat bedrag ongeacht de
volatiliteit.

Dat snijdt twee kanten op:

| Markt | ATR | Vast doel van 10 |
|---|---|---|
| rustig | 2 | onbereikbaar; je doet vrijwel niets |
| normaal | 6 | vergelijkbaar met 1,7x ATR |
| onrustig | 12 | binnen een halve bar geraakt |

En bij een vaste **stop** is het omgekeerde het risico: in een onrustige markt
word je er stelselmatig uitgeschud voordat de beweging op gang komt.

Bij het openen van het optiescherm staat de huidige ATR erbij, met wat de
multipliers daarmee opleveren. Dan weet je waar je een vast bedrag tegen
afzet.

De twee zijn onafhankelijk: je kunt het doel vastzetten en de stop laten
meeschalen.

**Let op:** doel en stop zitten in de vingerafdruk van de run. Ze aanpassen
begint een nieuwe bewijsfase, want trades met andere doelen zijn niet
vergelijkbaar.
