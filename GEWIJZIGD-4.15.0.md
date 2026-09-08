# Gewijzigd in 4.15.0

Bevat 4.14.1 tot en met 4.14.3.

| Bestand | Wat er veranderde |
|---|---|
| `broker/schedule.py` | waarneming van werkelijke sluitingen per uur |
| `coordinator.py` | ontbrekende methode; roosterwaarschuwing niet per cyclus; nulposities gelden als gesloten; sluitingen worden vastgelegd |
| `broker/reconcile_audit.py` | nulpositie is gesloten, geen verschil |
| `diagnostics.py` | sluitingswaarneming toegevoegd |

## Het roosterprobleem

Twee bronnen geven verschillende tijden voor spot goud bij IG. De Nederlandse
publicatie noemt een pauze van 23:00 tot 24:00 lokale tijd; een andere bron
noemt 22:00 tot 23:00 UTC — dat scheelt een uur in zomertijd.

Welke klopt valt van buitenaf niet vast te stellen. Kiezen zou gokken zijn.

Daarom wordt nu **gemeten** wanneer de broker werkelijk sluit, per uur van de
dag. Na een week staat er in de diagnostiek onder `closures` per uur hoe vaak
de markt dicht was, en onder `closure_hint` een uitspraak zodra er een patroon
is.

Bewust alleen waarnemen, niet automatisch bijstellen: een rooster dat zichzelf
aanpast op grond van een storing bij de broker, sluit je uit van een markt die
gewoon open is.
