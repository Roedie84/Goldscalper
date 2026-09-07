# Gewijzigd in 4.14.0

Bevat ook de diagnostiekfix uit 4.13.0.

| Bestand | Wat er veranderde |
|---|---|
| `learning/postmortem.py` | nieuwe categorie `niet_gemeten`; ongemeten trades tellen niet meer mee in de conclusie |
| `coordinator.py` | terugval op de uitkomst werkt nu ook als de meting op nul staat |
| `diagnostics.py` | leerlaag, periodes, sizing, audit en backtest toegevoegd aan de export |
| `manifest.json` | versienummer |
| `tests/` | zes tests erbij |

## De fout

Bij het openen van een positie werd `{"mfe": 0, "mae": 0}` gezet zodat de
sleutel bestond. De terugval bij het sluiten greep alleen in als die sleutel
*ontbrak* — maar hij was er altijd.

Sloot de broker de positie voordat de beheerlus hem zag, dan bleven beide nul.
De regel "nauwelijks beweging in beide richtingen" is dan per definitie waar,
en de trade werd als `geen_vervolg` gestempeld.

Bij 61% van de trades gebeurt dat. Vandaar 94% `geen_vervolg` — geen bevinding
maar een artefact van deze code.

Ongemeten trades heten nu `niet_gemeten` en blijven buiten de conclusie.
