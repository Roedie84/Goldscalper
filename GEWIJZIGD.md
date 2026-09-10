# Van 4.17.1 naar 4.20.1

Dertien bestanden. Bepaald door je huidige GitHub-versie (4.17.1) te
vergelijken met de werkkopie, en geverifieerd door de volledige suite te
draaien op de samengevoegde boom: **797 tests groen**.

## Nieuw

| Bestand | Wat het doet |
|---|---|
| `storage/bar_archive.py` | bars bewaren over herstarts heen |
| `analysis/validation.py` | backtest vergelijken met het live-resultaat |
| `learning/sessions.py` | resultaat per handelssessie en rond publicatietijden |

## Gewijzigd

| Bestand | Wijziging |
|---|---|
| `coordinator.py` | archief openen en vullen; sessies en nieuwsvensters; valuta vóór de vingerafdruk; uitkomst van de validatie |
| `__init__.py` | diensten `import_history` en `validate_backtest` |
| `const.py`, `services.yaml` | de twee nieuwe diensten |
| `diagnostics.py` | sessies, nieuwsimpact, archief en validatie in de export |
| `tests/` | vier bestanden; van 746 naar 797 tests |

## Waarom dit

De beperking van dit project is niet het aantal ideeën maar het aantal
metingen. Bij vijfentwintig trades per dag kost één hypothese drie weken; tien
hypotheses kosten twee jaar.

Het **barsarchief** bewaart wat er toch al binnenkomt, zodat een hypothese op
termijn in een minuut te toetsen is in plaats van in weken. Er gaat geen enkel
extra verzoek naar de broker.

De **validatie** stelt de vraag die daarvóór komt: klopt de backtest wel met
wat er live gebeurde? Alle hypothesen die je op historische data toetst rusten
op die aanname, en als hij niet klopt is elke toets erop waardeloos.

Het oordeel benoemt ook de **richting** van een afwijking. Een backtest die
gunstiger uitvalt dan live is gevaarlijk — je keurt hypothesen goed die het
live niet halen. Een die ongunstiger uitvalt kost je hooguit een gemiste kans.
Voor kosten is het omgekeerd: te lage kosten in de backtest zijn de
optimistische kant.

## Na installatie

Herstart Home Assistant. Diensten worden alleen bij het opstarten
geregistreerd.

Er begint een **nieuwe run**: de accountvaluta bereikt nu voor het eerst de
vingerafdruk, waardoor je positiegrootte met ongeveer acht procent verandert.
Je bestaande trades blijven in het rapport staan.

## Volgorde van gebruik

1. `gold_scalper.import_history` — het archief vullen
2. de bewijsfase laten lopen
3. `gold_scalper.validate_backtest` — controleren of het meetinstrument deugt
4. pas daarna hypothesen toetsen op historische data

Stap 3 vóór stap 4 is het hele punt.
