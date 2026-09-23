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

## Backtest

Ontwikkelhulpmiddelen → Acties → **Gold Scalper: Backtest draaien**.

Hij laat de strategie over de bars lopen die de integratie zelf heeft
verzameld, en vraagt dus geen datapunten op bij je broker. Je kunt spread,
slippage en ordergrootte meegeven om te zien hoe gevoelig het resultaat daarvoor
is.

Het resultaat komt in het logboek, in het attribuut `backtest` van
`sensor.<...>_resultaat_per_periode`, en als event `gold_scalper_backtest_done`
zodat je er een melding aan kunt hangen.

### Wat deze backtest wel en niet is

Hij roept **dezelfde code aan als de live handel** - dezelfde `evaluate()`,
dezelfde exitmanager. Dat is het belangrijkste kenmerk: een backtest die de
strategie nabouwt, toetst de nabouw.

Stops worden getoetst tegen de uitersten binnen de bar, niet tegen de
slotkoers. En zijn stop en doel binnen dezelfde bar allebei geraakt, dan wint
de stop - uit een candle valt niet af te leiden welke eerst kwam, en gokken op
de gunstige volgorde is precies hoe een backtest zichzelf rijk rekent.

Niet gemodelleerd: spread die verbreedt rond nieuws, requotes, partiële fills,
latency tussen signaal en fill. Reken op minder dan de backtest laat zien, niet
op meer.

### De valkuil die niet technisch is

Een backtest die je gebruikt om instellingen te kiezen, meet daarna niets meer:
je hebt de uitkomst in de keuze gestopt. Wie twintig varianten probeert en de
beste kiest, heeft de beste van twintig ruisuitkomsten gekozen.

Draai hem, noteer de uitkomst, en verander daarna niets op grond van wat je
zag. Wil je toch iets aanpassen, doe dat dan op grond van een reden die
losstaat van de uitkomst - en draai daarna een nieuwe bewijsfase.

## Resultaat per dag, week en maand

`sensor.<...>_resultaat_per_periode` toont het resultaat van vandaag. In de
attributen staan:

| Attribuut | Inhoud |
|---|---|
| `vandaag`, `deze_week`, `deze_maand` | trades, bruto, kosten, netto, trefkans |
| `dagen`, `weken`, `maanden` | de reeks, voor grafieken |
| `reeksen` | langste winst- en verliesreeks, aandeel winstdagen, mediane dag |

Bruto en kosten staan bewust apart. Een winstcijfer zonder de kostenkolom
ernaast is misleidend, en juist die verhouding is bij scalping het hele verhaal.

`reeksen` is de moeite waard om in de gaten te houden. Acht verliesdagen op rij
is iets heel anders dan acht verspreid over twee maanden, ook als het totaal
gelijk is - en het eerste is wat je in de praktijk moet kunnen volhouden.

Alles wordt gegroepeerd op **lokale** kalenderdagen. Een trade van half twee 's
nachts hoort bij die nacht zoals jij hem beleeft.

## Bijkopen: pyramiden, niet middelen

Bij de opties staat **Bijkopen bij bevestiging (pyramiden)**, standaard uit.

### Het verschil met middelen

Middelen is bijkopen als de koers tegen je in gaat, omdat het dan "goedkoper"
is. Dat klopt rekenkundig en het is de snelste manier om een rekening leeg te
maken:

| Stap | Koers | Positie | Verlies |
|---|---|---|---|
| 0 | 4665 | 10 oz | 0 |
| 1 | 4661 | 20 oz | -40 |
| 2 | 4657 | 30 oz | -120 |
| 3 | 4653 | 40 oz | -240 |
| 4 | 4649 | 50 oz | **-400** |

Je stop is geen enkele keer geraakt, en toch is je verlies vertienvoudigd. Het
perverse eraan: negen van de tien keer herstelt de koers en kom je er beter
uit. De tiende keer verlies je alles wat die negen opleverden.

Pyramiden is het spiegelbeeld: bijkopen als de markt je **gelijk geeft**.

### De regel die alles bijeenhoudt

Elke toevoeging gaat gepaard met het verplaatsen van de stop, zodat het totale
risico van de samengestelde positie niet groter wordt dan dat van de eerste.
Kan de stop niet ver genoeg mee, dan gaat de toevoeging **niet door**.

Zonder die koppeling is pyramiden gewoon een trager soort middelen.

Bij een instap op 4665,85 met 10 ounce en een stop op 4661,84:

| Koers | Actie | Positie | Stop | Risico |
|---|---|---|---|---|
| start | | 10 oz | 4661,84 | 40,10 |
| 4670 | +5 oz | 15 oz | 4667,33 | 40,09 |
| 4676 | +2,5 oz | 17,5 oz | 4675,16 | 14,77 |

Je positie groeit met 75%, je risico blijft gelijk of daalt.

### Verdere begrenzingen

Elke volgende toevoeging is de helft van de vorige. Een piramide die naar boven
breder wordt valt om: je grootste inzet zit dan op het hoogste punt, precies
waar een trend het vaakst eindigt.

En er zit minimaal 0,75 x ATR tussen twee toevoegingen. Zonder die afstand
stapelen ze zich op één prijsniveau, en dan heb je geen piramide maar een grote
positie met een dun excuus.

### Wanneer aanzetten

Niet nu. Pyramiden vergroot je inzet op de strategie, en dat is alleen zinvol
als je weet dat er een edge is. Zet het aan als de bewijsfase geslaagd is, niet
ervoor.

## Wat er gebeurt als de markt dicht is

Goud handelt bijna 24 uur per werkdag, met een korte dagelijkse onderbreking en
het hele weekend gesloten. Dan gebeurt er dit:

| Onderdeel | Gedrag |
|---|---|
| Status | `markt_gesloten` - geen storing, geen noodstop |
| Koers ophalen | gaat door; levert de laatst bekende koers |
| Orders plaatsen | geweigerd met reden "markt gesloten" |
| Posities beheren | overgeslagen |
| Bars opbouwen | **overgeslagen** |
| Controle tegen de broker | overgeslagen |
| Leren en rapporteren | gaat door; leest alleen de database |

Je krijgt dus geen foutmeldingen, en de dataverbinding wordt niet als dood
beschouwd ook al is de laatste koers uren oud.

### Twee bronnen voor de handelstijden

De integratie leunde eerst volledig op het veld `marketState` dat IG meestuurt.
Dat werkt, maar het is één bron: klopt dat veld niet, dan handelt de bot op
verouderde koersen zonder dat iets het merkt.

Er zit nu een rooster naast, met de gepubliceerde tijden voor spot goud in
Nederlandse tijd:

| | |
|---|---|
| Opent | maandag 00:00 |
| Sluit | vrijdag 23:00 |
| Dagelijkse onderbreking | 23:00 tot 24:00 |

**Bij onenigheid wint 'gesloten'.** Zegt de broker open en het rooster dicht,
of andersom, dan wordt er niet gehandeld. Dat is geen voorzichtigheid maar
rekenkunde: een gemiste kans kost je niets, handelen op een koers van uren
geleden kan je alles kosten.

Een afwijking wordt gemeld in de statussensor en het logboek, niet stil
gecorrigeerd. Het rooster is namelijk geen waarheid: feestdagen en vervroegde
sluitingen staan er niet in. Zie je die melding vaak op hetzelfde moment, dan
is het rooster verouderd en moet het bijgesteld.

Uitzetten kan met **Handelstijden controleren tegen een eigen rooster**.

### Geen nieuwe posities vlak voor sluiting

Standaard gaat er in de laatste **10 minuten** voor een sluiting geen nieuwe
positie meer open.

Een trade met een tijdslimiet van vijf minuten die om 22:58 opengaat, wordt
door de sluiting overvallen: je zit dan vast tot de volgende sessie, en die
opent met een gat waar geen stop tussen zit.

### Waarom bars overslaan zo belangrijk is

Zou de bot doorbouwen tijdens een gesloten markt, dan levert een weekend van
achtenveertig uur ongeveer vijfhonderd bars op met exact dezelfde prijs. Die
verdringen de werkelijke historie en de ATR zakt naar nul.

Maandagochtend blokkeert de kostenpoort dan élke trade - want een doel van nul
dekt nooit je kosten. Je zou dat pas merken als de bot een dag lang niets deed.

Een gat in de reeks is hier het juiste gedrag: de markt bewóóg niet, en doen
alsof er bars waren zou een verzinsel zijn.
