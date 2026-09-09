# Gewijzigd in 4.16.0

Bevat ook de levenscyclusfix uit 4.15.1.

| Bestand | Wat er veranderde |
|---|---|
| `broker/currency.py` | **nieuw** — omrekening tussen instrument- en accountvaluta |
| `strategy/sizing.py` | budget wordt omgerekend voordat het door de stopafstand wordt gedeeld |
| `coordinator.py` | koers wordt afgeleid uit de broker; valuta in de vingerafdruk |
| `lifecycle.py` | nulpositie geldt als gesloten, niet als verweesd |
| `manifest.json` | versienummer |
| `tests/` | elf tests op de valuta, drie op de levenscyclus |

## Het valutaprobleem

Goud noteert in dollars, jouw account staat in euro's. Dat botste op één plek
die ertoe doet:

    budget  = eigen vermogen (EUR) x risicopercentage
    ounces  = budget / stopafstand (USD)

Een euro-budget gedeeld door een dollarafstand. Bij een koers rond 1,08 leverde
dat een positie op die zo'n acht procent te klein was.

## De koers komt van de broker

Niet van een externe bron. IG meldt de onrealiseerde winst in accountvaluta
terwijl de prijsbeweging in instrumentvaluta staat; de verhouding tussen die
twee is de koers — en dat is degene waarmee hij ook afrekent.

Zolang die koers niet bekend is, wordt er **niet** omgerekend maar gemeld dat
het niet kan. Een geschatte koers maakt de fout onzichtbaar in plaats van
zichtbaar.

## Dit begint een nieuwe run

De accountvaluta zit nu in de vingerafdruk. Zodra er wordt omgerekend
verandert de positiegrootte bij hetzelfde risicopercentage, en trades van voor
en na die omschakeling zijn niet vergelijkbaar.

Je huidige run blijft in het rapport staan.
