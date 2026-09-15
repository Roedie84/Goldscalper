# Van 4.17.1 naar 4.23.1

Geverifieerd op een verse kloon van je GitHub: **844 tests groen**.

## Wat de diagnostiek blootlegde

    Geen transactie gevonden voor instapprijs 4279.75.
    23 transacties bekeken, nieuwste 15-09T10:51, oudste 14-09T12:35.

Twee dingen:

**De twee onvindbare trades sloten ná 10:51.** Die staan nog niet in het
overzicht van de broker — dat loopt uren achter. Ze worden later gecorrigeerd;
dat is goed gedrag.

**Maar ernstiger: mijn herzoekopdracht was schadelijk.** Hij zette
vierendertig trades terug op "geschat", terwijl het zoekvenster maar tot
14-09 12:35 reikte. Alles wat daarvoor sloot was daarmee nooit meer te vinden —
en die trades hádden een juiste uitstapprijs.

Correcte cijfers werden zo als schatting in het rapport gezet.

## De oplossing

**Het zoekvenster ligt nu rond de sluittijd van elke trade**, niet rond het
huidige moment. Zes uur ervoor tot twaalf uur erna. Daarmee blijft elke trade
vindbaar, hoe oud ook.

**En na drie mislukte pogingen wordt opgegeven**, met een eigen label
`broker_gesloten_onvindbaar`. Eeuwig blijven proberen kost elke ronde een
netwerkverzoek en houdt de trade als schatting in het rapport, ook wanneer de
prijs niet meer te achterhalen is. De geboekte prijs blijft staan.

## Na installatie

    action: gold_scalper.recheck_exits

Draai die opnieuw. Nu worden ook de oudere trades gevonden, en de
vierendertig die ik onbedoeld naar "geschat" degradeerde krijgen hun juiste
label terug.

Reken op een half uur: vijf per keer, elke paar minuten.

## Wat dit betekent

Dit is de vierde ronde aan deze reparatie. Elke ronde loste een echte fout op
en legde de volgende bloot — en elke keer was de diagnostiek die ik erbij
bouwde de reden dat we hem vonden.

Het patroon dat overblijft: ik kan niet bij jouw brokeraccount, dus elke
aanname over wat de broker teruggeeft moet blijken uit een logregel. Dat is
langzamer dan gokken, maar het is de enige weg die eindigt.
