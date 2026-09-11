# Van 4.17.1 naar 4.22.2

Geverifieerd op een verse kloon van je GitHub: **835 tests groen**.

## Wat 4.22.2 repareert

**De correctie wachtte te lang.** Hij ging pas na dertig cycli, oftewel tien
minuten. Dat is te lang om twee redenen: bij een herstart staan er vaak al
schattingen uit de vorige sessie, en zolang de correctie niet heeft gedraaid
blijft ook de **wisselkoers** onbekend — die komt uit dezelfde lus.

Nu draait hij meteen bij de eerste cyclus en daarna elke tien.

**De teller stond op nul.** `estimated_settlements` werd alleen bijgewerkt
binnen de correctielus, dus hij meldde nul zolang die nog niet had gedraaid —
terwijl er twee trades op een schatting stonden.

Het getal wordt nu ook bij het leren bijgewerkt, zodat het klopt vóór de eerste
correctie.

## Wat er al werkte

De twee gecorrigeerde trades uit je rapport kloppen tot op de cent met het
overzicht van de broker:

| instap | mijn netto | omgerekend | broker |
|---|---|---|---|
| 4346,61 | US$ -3,54 | EUR -3,08 | **EUR -3,08** |
| 4351,43 | US$ -6,27 | EUR -5,45 | **EUR -5,45** |

Nul verschil. Mijn cijfers staan in dollars, die van de broker in euro's;
vermenigvuldig met 0,869 en ze zijn identiek.

## Wat je hierna ziet

Binnen een minuut na de herstart:

* `estimated_settlements` met het werkelijke aantal
* `conversion.rate` rond 0,868
* de geschatte trades die omslaan naar `gecorrigeerd`

Zodra de koers bekend is verandert je positiegrootte met ongeveer acht procent,
en dat begint automatisch een nieuwe run. Terecht: trades van voor en na die
omschakeling zijn niet vergelijkbaar.
