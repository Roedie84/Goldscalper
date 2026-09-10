# Van 4.17.1 naar 4.21.1

Geverifieerd op een verse kloon van je GitHub: **812 tests groen**.

## Nieuw in 4.21.1: bewust een nieuwe bewijsfase beginnen

    action: gold_scalper.new_run
    data:
      note: meting van uitstapprijzen was fout

Die dienst bestond niet. Een run begon alleen als de vingerafdruk wijzigde —
dus als je een instelling aanpaste.

Maar er is een geval waarin je opnieuw wilt beginnen zonder iets aan de
strategie te veranderen: wanneer blijkt dat de **meting** fout was. Zonder deze
dienst zou je een instelling moeten verzinnen om aan te passen, en dan meet je
twee dingen tegelijk.

De reden wordt bij de run bewaard, zodat je later weet waarom hij begon.

## De reden dat dit nu nodig is (4.21.0)

Vijf trades op één middag: de broker boekte **+28,58 euro**, de eigen
administratie **-4,83**.

| | in | uit volgens IG | uit volgens mij |
|---|---|---|---|
| 14:34 | 4355,17 | **4344,50** | 4354,95 |
| 14:31 | 4369,25 | **4357,83** | 4370,88 |
| 14:26 | 4374,46 | **4363,40** | 4362,84 |

Er werd afgerekend op de koers van het moment waarop de lus *ontdekt* dat een
positie weg is, niet op de prijs waarop de broker sloot. Bij shorts die op hun
doel sloten viel dat precies verkeerd uit: de koers keert na een doeltreffer
vaak terug, en dan lijkt de trade nauwelijks bewogen te hebben.

De werkelijke uitstapprijs komt nu uit het activiteitenoverzicht van de broker.
Lukt dat niet, dan heet de sluitreden `broker_gesloten_geschat` in plaats van
`broker_gesloten_gemeten`, zodat je weet welke cijfers hard zijn.

**Bijkomend:** de winst die de broker in accountvaluta meldt geeft de
wisselkoers rechtstreeks — preciezer dan de afleiding uit een open positie.

## Volgorde na installatie

1. Herstart Home Assistant.
2. `gold_scalper.new_run` met een reden.
3. Laten lopen.

Je oude runs blijven onderaan het keuringsrapport staan.
