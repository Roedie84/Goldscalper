# Van 4.17.1 naar 4.21.2

Geverifieerd op een verse kloon van je GitHub: **819 tests groen**.

## Waarom 4.21.0 niet werkte

De sluitreden stond op `broker_gesloten_geschat`: het ophalen van de werkelijke
uitstapprijs mislukte elke keer.

Oorzaak: ik vergeleek het **dealId** (`DIAAAAYFPY669AZ`) met het veld
`reference` uit het transactieoverzicht van de broker. Dat zijn twee
verschillende identificaties — ze matchen nooit.

Gevolg: de fix die de fout van 28 euro per middag moest oplossen, viel elke
keer stil terug op precies die fout.

## Nu wordt op de instapprijs gezocht

Die staat in de eigen administratie én in het overzicht van de broker, met vier
decimalen. Twee trades met exact dezelfde instapprijs binnen vijftig
transacties is onwaarschijnlijk genoeg.

Uit jouw scherm:

| | in | uit volgens IG | uit volgens mij |
|---|---|---|---|
| 15:38 | 4360,06 | **4371,57** | 4361,28 |
| 15:32 | 4350,12 | **4360,85** | — |

IG boekte -8,69 en -8,95 euro; mijn administratie -0,10 en -1,06. Ook hier
zaten mijn cijfers dicht bij nul waar de werkelijkheid tien dollar bewoog.

## En een schatting is nu luidruchtig

Mislukt het ophalen alsnog, dan verschijnt bij de eerste, vijfde, twintigste en
vijftigste keer een waarschuwing in het logboek, en staat het aantal in de
diagnostiek onder `estimated_settlements`.

Een schatting die stil doorgaat produceert cijfers die eruitzien als metingen.
Dat is precies hoe deze fout twee keer onopgemerkt bleef: eerst omdat er geen
onderscheid was, daarna omdat de terugval niets zei.

## Na installatie

Geen nieuwe run nodig — die heb je net begonnen. Kijk bij de eerste trades naar
de sluitreden:

* `broker_gesloten_gemeten` — de fix werkt
* `broker_gesloten_geschat` — hij werkt nog steeds niet, laat het weten
