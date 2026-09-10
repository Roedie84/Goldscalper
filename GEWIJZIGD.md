# Van 4.17.1 naar 4.21.0

Geverifieerd op een verse kloon van je GitHub: **809 tests groen**.

## De ernstigste fout tot nu toe

Vijf trades op één middag. De broker boekte **+28,58 euro**, de eigen
administratie **-4,83**.

| | in | uit volgens IG | uit volgens mij |
|---|---|---|---|
| 14:34 | 4355,17 | **4344,50** | 4354,95 |
| 14:31 | 4369,25 | **4357,83** | 4370,88 |
| 14:26 | 4374,46 | **4363,40** | 4362,84 |

De instapprijzen klopten. De uitstapprijzen lagen binnen twee dollar van de
instap terwijl er tien tot elf vanaf werd gesloten.

### Waarom

De beheerlus merkt pas na een cyclus dat een positie weg is, en in die tijd
loopt de koers verder. Er werd afgerekend op de koers van het *ontdekkings*-
moment, niet op de prijs waarop de broker werkelijk sloot.

Bij shorts die op hun doel sloten viel dat precies verkeerd uit: de koers keert
na een doeltreffer vaak terug, en dan lijkt de trade nauwelijks bewogen te
hebben.

Alle vijf stonden op `broker_gesloten`, dus zelfs de niveaudetectie greep niet.

### De oplossing

De werkelijke uitstapprijs wordt nu opgehaald uit het activiteitenoverzicht van
de broker. Dat is de enige betrouwbare bron; alles anders is een schatting die
er precies naast zit wanneer het het meest uitmaakt.

Lukt dat niet, dan blijft de schatting maar heet de sluitreden
`broker_gesloten_geschat` in plaats van `broker_gesloten_gemeten`. Zo weet je
later welke cijfers hard zijn.

### Bijkomend voordeel

De winst in accountvaluta die de broker meldt, geeft ook de wisselkoers — en
preciezer dan de afleiding uit een open positie, want dit is het bedrag waarmee
werkelijk is afgerekend. Uit jouw scherm blijkt die koers rond **0,855** te
liggen, niet de 0,926 die ik eerder als voorbeeld nam.

## Wat betekent dit voor je cijfers

Alle resultaten van posities die de broker zelf sloot, zijn onbetrouwbaar. Dat
zijn er veel: in de vorige diagnostiek 104 van 129 trades.

Je werkelijke resultaat is vermoedelijk **beter** dan gerapporteerd, want de
fout snijdt systematisch de doeltreffers af. Hoeveel beter valt niet achteraf
te bepalen.

Ik zou een nieuwe run beginnen zodra dit draait.

## Wat er verder in zit

Barsarchief, backtestvalidatie, sessie- en nieuwsuitsplitsing, valutaomrekening,
onderdrukking van herhaalde meldingen.
