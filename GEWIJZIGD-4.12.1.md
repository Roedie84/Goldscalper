# Gewijzigd in 4.12.1

Ten opzichte van 4.12.0 zijn alleen deze bestanden veranderd.

| Bestand | Wat er veranderde |
|---|---|
| `custom_components/gold_scalper/coordinator.py` | mfe en mae worden altijd vastgelegd |
| `custom_components/gold_scalper/manifest.json` | versienummer |
| `tests/test_broker_trade_recording.py` | drie tests erbij |

## De fout

De verliesanalyse filtert op `mfe is not None`. Die waarde werd alleen gezet
voor posities die de beheerlus had gezien. Een positie die tussen twee cycli
opent en sluit — of die de broker sluit voordat de lus hem ziet — kreeg nooit
een meting.

Gevolg: bij 33 trades met 18 verliezers meldde de analyse er **nul**.

Nu wordt bij het openen meteen een beginwaarde gezet, en valt hij bij het
sluiten terug op de uitkomst als er geen meting is. Minder nauwkeurig dan een
gemeten uiterste, maar veel beter dan de trade buiten de analyse laten.
