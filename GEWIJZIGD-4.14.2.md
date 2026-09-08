# Gewijzigd in 4.14.2

Bevat ook de ontbrekende methode uit 4.14.1.

| Bestand | Wat er veranderde |
|---|---|
| `coordinator.py` | ontbrekende methode `_audit_against_broker`; roosterwaarschuwing nog maar één keer per afwijking |
| `broker/schedule.py` | duidelijker tekst bij een feestdagsluiting |
| `manifest.json` | versienummer |
| `tests/` | controle op niet-bestaande methoden, en op herhaalde waarschuwingen |

## De twee meldingen

**903 roosterwaarschuwingen.** Op 7 september 2026 was het Labor Day; de
Amerikaanse markten sloten vervroegd en IG volgde. Het rooster kent geen
feestdagen en meldde de afwijking terecht — maar elke twintig seconden
opnieuw.

Het gedrag was goed: er is niet gehandeld. Alleen de logging deugde niet.

**106 AttributeErrors.** De aanroep `self._audit_against_broker()` stond er
wel, de methode niet. Elke tiende cyclus viel de handelslus om.
