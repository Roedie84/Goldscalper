# Van 4.17.1 naar 4.20.2

Dertien bestanden. Geverifieerd op een verse kloon van je GitHub: **800 tests
groen**.

## De fout in 4.20.1

Het archief werd nooit geopend. `self.archive` stond op None en werd nergens
gevuld — de regel die het bestand aanmaakt ontbrak, terwijl het veld en alle
gebruik ervan er wel stonden.

Gevolg: elke bar werd stil overgeslagen en `import_history` meldde *"Het
archief is niet geopend"*.

En het veld stond twee keer in de constructor; die invoeging was gedupliceerd.

## Waarom 800 tests dit niet vingen

De tests op het archief maken hun eigen exemplaar en raken de coordinator niet
aan. Ze toetsten of een archief werkt, niet of het wordt geopend.

Er staan nu drie controles op:

* `test_the_archive_is_actually_opened` — de coordinator maakt en opent het
* `test_the_archive_has_its_own_file` — apart bestand naast de trades
* `test_optional_components_are_initialised` — statisch: een veld dat op None
  begint en nergens gevuld wordt

Die laatste vangt de volgende variant van deze fout, ongeacht welk onderdeel
het betreft.

## Wat er verder in zit

| Nieuw | |
|---|---|
| `storage/bar_archive.py` | bars bewaren over herstarts heen |
| `analysis/validation.py` | backtest vergelijken met het live-resultaat |
| `learning/sessions.py` | resultaat per handelssessie en rond publicatietijden |

## Na installatie

Herstart Home Assistant, en dan:

    action: gold_scalper.import_history
    data:
      bars: 1000

In het logboek verschijnt hoeveel bars zijn opgehaald en wat de dekking is. Bij
goud hoort die rond de 75% te liggen — weekenden en de dagelijkse onderbreking
zitten in het gat.
