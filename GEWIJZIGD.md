# Versie 5.3.0

Ten opzichte van de volledige 5.2.2 die je net hebt geüpload. Geverifieerd:
**901 tests groen**.

## Indicatorlab

    action: gold_scalper.indicator_lab

Toetst 22 indicatoren op je barsarchief: wat had elk opgeleverd als je erop had
gehandeld? Resultaat in het logboek en in de diagnostiek onder
`indicator_lab`.

Indicatoren hoeven niet verzameld te worden — het zijn berekeningen op de prijs,
en die prijzen staan al in het archief. Het lab werkt dus direct op de bars die
er nu liggen, en wordt beter naarmate het archief groeit.

### De twee beveiligingen

Twintig indicatoren toetsen op dezelfde data vindt er altijd een paar die
"werken" — door toeval. Daarom, vast in de code:

1. **Ontdekken en bevestigen gescheiden.** De eerste 70% bepaalt drempels en
   richting, de laatste 30% toetst die vaste regel zonder aanpassing.
2. **De lat stijgt met het aantal toetsen.** Bij 22 indicatoren is t ≥ 3,06
   nodig in plaats van 2,0.

Op gesimuleerde data kozen zeven indicatoren in de ontdekking een richting die in
de bevestiging omsloeg, tot t = −3,8. Zonder scheiding had je die als winnaar
gezien. En op zuivere ruis slaagt er geen enkele — dat is getest.

### Wat wel en niet meedoet

Oscillatoren, trend- en volatiliteitsindicatoren, met standaardinstellingen uit
de literatuur. **Geen** volumeindicatoren: bij een CFD is het volume een
tikteller, en een indicator op een nepgetal geeft een nepresultaat.

Per trade: doel en stop zoals je strategie, maximaal 16 bars, kosten 0,75 per
ounce (uit je gemeten live-kosten). Raken stop en doel dezelfde bar, dan telt de
stop. Trades overlappen niet, anders telt één beweging als twintig bewijzen.

## Klantsentiment wordt verzameld

Bij elke afgesloten bar wordt vastgelegd welk deel van de IG-klanten long en
short staat. IG bewaart geen historie van dit getal, dus elke dag die niet
wordt vastgelegd is weg. Voortgang in de diagnostiek onder
`sentiment.verzameling`: de toets vraagt 200 extreme waarnemingen (≥ 75% aan
één kant).

## Archief groeide niet meer

Met historie van IG in plaats van zelfgebouwde bars werd er niets
gearchiveerd — de haak zat alleen in het zelfbouwpad. Je archief stond daardoor
stil (1817, en dagen later 1822). Nu verwerken beide paden hun bars op één plek.

## Per trade erbij

`entry_sentiment_long`, `entry_williams_r`, `entry_cci` — naast de zes
indicatorwaarden uit 5.1.0. Uitsluitend meting, geen invloed op de handel.
