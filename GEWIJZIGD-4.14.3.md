# Gewijzigd in 4.14.3

Bevat ook 4.14.1 en 4.14.2.

| Bestand | Wat er veranderde |
|---|---|
| `broker/reconcile_audit.py` | een positie van nul ounce geldt als gesloten, niet als verschil |
| `coordinator.py` | ontbrekende methode `_audit_against_broker`; roosterwaarschuwing niet meer per cyclus; nulposities gelden als verdwenen |
| `broker/schedule.py` | duidelijker tekst bij een feestdagsluiting |
| `manifest.json` | versienummer |
| `tests/` | drie nieuwe controles |

## De fout

De eerste keer dat de brokervergelijking draaide, sloeg hij meteen af:

> Positie DIAAAAYEV8D22B2: broker meldt 0.0, database 1.28.

Een positie van nul ounce bij de broker is geen positie — het is een gesloten
positie die IG nog even in de lijst laat staan. Mijn code las dat als "de
omvang verschilt", merkte het aan als kritiek en legde de handel stil.

Nu geldt zo'n positie als gesloten en wordt de trade alsnog afgerekend. Een
echt verschil in omvang — twee getallen die allebei boven nul liggen — blijft
wel kritiek.
