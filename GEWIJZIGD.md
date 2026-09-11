# Van 4.17.1 naar 4.22.1

Geverifieerd op een verse kloon van je GitHub: **833 tests groen**.

## De correctie werkt

Uit je rapport:

| gesloten | in | uit | netto | reden |
|---|---|---|---|---|
| 10:30 | 4351,43 | 4346,07 | -6,27 | **gecorrigeerd** |
| 10:57 | 4346,61 | 4343,66 | -3,54 | **gecorrigeerd** |
| 11:10 | 4344,01 | 4344,02 | +0,02 | geschat |

De twee gecorrigeerde hebben uitstapprijzen die vijf en drie dollar van de
instap liggen. De derde staat op één cent verschil — precies het patroon van
een schatting, die alles naar nul comprimeert.

## Twee dingen die 4.22.1 repareert

**De teller stond verkeerd.** `estimated_settlements` meldde nul terwijl er nog
één trade te corrigeren was. Die teller begon bij elke herstart opnieuw en werd
alleen verhoogd bij nieuwe schattingen; nu wordt de stand uit de database
gelezen.

Een getal dat verkeerd kan staan is erger dan geen getal, want je vertrouwt
erop.

**De wisselkoers bleef leeg.** De afleiding uit een open positie lukte nooit:
posities sluiten te snel om genoeg beweging te tonen.

Bij een correctie is het bedrag waarmee de broker werkelijk heeft afgerekend
wél bekend. Dat geeft de koers rechtstreeks, en preciezer — uit jouw scherm
blijkt die rond **0,868** te liggen.

Zodra hij bekend is wordt de positiegrootte omgerekend en verdwijnt de
waarschuwing over twee eenheden.

## Wat je hierna ziet

* `broker_gesloten_gecorrigeerd` bij vrijwel elke trade
* `estimated_settlements` dat oploopt en weer terugvalt naar nul
* `conversion.rate` met een waarde rond 0,868
