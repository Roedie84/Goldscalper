# Gewijzigd in 4.17.1

| Bestand | Wat er veranderde |
|---|---|
| `coordinator.py` | accountvaluta wordt opgehaald vóór de vingerafdruk |
| `diagnostics.py` | `conversion` toegevoegd aan de export |
| `tests/test_broker_cycle.py` | twee tests op de valuta in de vingerafdruk |
| `tests/test_static_analysis.py` | controle kijkt nu ook naar objectvelden |

## Twee gaten

**De valuta bereikte de vingerafdruk niet.** Hij zit erin omdat de
positiegrootte ervan afhangt, maar hij werd pas bekend bij de eerste
accountopvraging in de handelslus — ruim ná het bepalen van de run. De
vingerafdruk las dus altijd de standaardwaarde en veranderde nooit, waardoor
een valutaomschakeling stilzwijgend in dezelfde bewijsfase belandde.

Nu wordt de valuta vóór de vingerafdruk opgehaald.

**`conversion` stond niet in de diagnostiek.** De controle uit 4.13.0 keek
alleen naar velden van het type dict; `conversion` is een object en ontsnapte
daardoor. De controle kijkt nu ook naar velden met een `as_dict`-methode.
