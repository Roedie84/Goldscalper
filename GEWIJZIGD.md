# Versie 5.3.2

Ten opzichte van 5.3.0; bevat ook 5.3.1. Geverifieerd: **912 tests groen**.

## Open posities werden als gesloten gezien

Je trade met instap 4319,64:

| | |
|---|---|
| geopend | 10:30 |
| administratie zoekt al naar de uitstapprijs | 11:27 |
| IG sluit hem werkelijk | 11:48 |

De positieopvraging gebruikte API-versie 1, waar omvang en instapprijs andere
namen hebben. Het veld `size` ontbrak, werd als **nul** gelezen, en nul
betekent "gesloten". Elke open positie leek daardoor gesloten zodra hij
geopend was.

### Wat dat veroorzaakte

* **Trades werden vlak na het openen afgerekend**, op de koers van dat moment.
  Vandaar uitstapprijzen die steeds één spread van de instap lagen. De
  correctie herstelde later het bedrag, maar niet het gedrag.
* **De limiet van één positie hield niet.** Met een administratie zonder open
  posities opende de bot de volgende — er stonden meerdere tegelijk open.
* **Een eerder alarm werd verkeerd uitgelegd.** De melding "broker meldt 0.0,
  database 1.28" betekende niet dat IG een gesloten positie op nul liet staan,
  maar dat het veld niet werd gelezen. De filters die daarop volgden, maakten
  van het alarm een stilte.

### De reparatie

* Posities worden opgevraagd als **versie 2**.
* **Beide veldnamen** worden gelezen: `size`/`level` en `dealSize`/`openLevel`.
* Een ontbrekend veld is **onbekend, niet nul**, en onbekend is open.
  Het wordt één keer gemeld met de velden die wél binnenkwamen.
* Of een positie gesloten is, beslist nu **één functie** in plaats van drie
  losse drempels.

## Minder ruis (uit 5.3.1)

"Geen transactie gevonden" verschijnt nog maar één keer per trade.

## Controleer na installatie

Kijk bij IG of er **meer dan één positie** openstaat. Zo ja, sluit de extra's
handmatig: die zijn door deze fout ontstaan.
