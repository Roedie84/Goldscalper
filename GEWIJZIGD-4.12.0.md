# Gewijzigd in 4.12.0

Alleen deze vier bestanden zijn veranderd ten opzichte van 4.11.1.
Kopieer ze over de bestaande, met behoud van de mappenstructuur.

| Bestand | Wat er veranderde |
|---|---|
| `custom_components/gold_scalper/coordinator.py` | de run wordt niet meer afgesloten bij een herstart; de vorige run wordt alleen nog beëindigd als de opzet werkelijk wijzigt |
| `custom_components/gold_scalper/storage/database.py` | nieuwe methode `latest_open_run()` |
| `custom_components/gold_scalper/manifest.json` | versienummer |
| `tests/test_run_continuity.py` | vier tests die een herstart nabootsen |

## De fout

`end_run` zet `ended_at`, en `find_matching_run` zoekt alleen naar runs
*zonder* `ended_at`. Bij het afsluiten werd de run dichtgezet, waarna de
volgende start hem niet meer herkende — precies wat de adoptiefunctie moest
voorkomen.

Vijf herstarts leverden vijf runs op, elk met een handvol trades. Een
bewijsfase van dertig dagen wordt zo nooit gehaald.

Een run eindigt nu alleen nog bij een gewijzigde opzet, of als je zelf een
nieuwe begint.
