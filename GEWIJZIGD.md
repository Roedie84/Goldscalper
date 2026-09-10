# Van 4.17.1 naar 4.20.3

Geverifieerd op een verse kloon van je GitHub: **802 tests groen**.

## Wat 4.20.3 toevoegt

**Herhaalde controlemeldingen onderdrukt.** De vergelijking met de broker
draait elke tiende cyclus, en dezelfde toestand duurt vaak veel langer. Tien
identieke regels in zeven minuten maakt het logboek onbruikbaar.

Dit was al opgelost bij de roosterwaarschuwing en vergeten bij de
controlebevindingen. De sleutel bevat het ticket, dus een nieuwe positie met
hetzelfde probleem meldt wel weer.

**Geslaagde handelingen niet meer als waarschuwing.** `import_history` meldde
zijn resultaat met `warning`, waardoor elke geslaagde import als rood item
verscheen. Het onderscheid tussen "er is iets mis" en "dit is gelukt" gaat
verloren als beide er hetzelfde uitzien.

## Over de valutamelding

    Orders worden in USD geplaatst terwijl het account in EUR staat.

Die blijft staan tot de wisselkoers is afgeleid, en dat gebeurt uit een open
positie met genoeg beweging - ruim een dollar winst of verlies. Bij kleine
posities die snel sluiten kan dat even duren.

Zodra het lukt verschijnt in het logboek:

    Wisselkoers USD/EUR afgeleid uit een open positie: 0.92xx

Vanaf dat moment wordt de positiegrootte omgerekend en verdwijnt de melding.
Zolang de koers onbekend is wordt er bewust **niet** omgerekend: een geschatte
koers maakt de fout onzichtbaar in plaats van zichtbaar.

## Wat er verder in zit

Barsarchief, backtestvalidatie, sessie- en nieuwsuitsplitsing, en de
valutaomrekening. Zie de eerdere beschrijving.
