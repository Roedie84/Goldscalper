# Gewijzigd in 4.16.1

| Bestand | Wat er veranderde |
|---|---|
| `coordinator.py` | `record_close()` krijgt nu het tijdstip mee, op beide plekken |
| `manifest.json` | versienummer |
| `tests/test_static_analysis.py` | controle op te weinig argumenten bij een aanroep |

## De fout

`record_close(net_pnl, now)` werd op twee plekken zonder `now` aangeroepen: de
twee die bij de brokerregistratie zijn toegevoegd. De paper-broker riep hem al
goed aan, dus het viel niet op — tot een positie door de broker werd gesloten
en de hele handelslus omviel.

De bestaande controle uit 4.14.1 kijkt of een methode *bestaat*, niet of de
argumenten kloppen. Pyflakes doet dat evenmin.

De nieuwe controle vergelijkt elke aanroep met het aantal verplichte
argumenten. Classmethods, staticmethods en namen die ook op ingebouwde types
voorkomen worden overgeslagen; anders staat de test vol vals alarm.
