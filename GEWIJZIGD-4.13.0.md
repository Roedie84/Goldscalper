# Gewijzigd in 4.13.0

| Bestand | Wat er veranderde |
|---|---|
| `custom_components/gold_scalper/diagnostics.py` | leerlaag, periodes, sizing, audit en backtest toegevoegd aan de export |
| `custom_components/gold_scalper/manifest.json` | versienummer |
| `tests/test_static_analysis.py` | twee tests die dit soort ontbrekende velden vangen |

## De fout

De diagnostiek bouwt zijn antwoord met de hand op. Alles wat sinds versie 4.0
aan de coordinator is toegevoegd — de consistentietoets, het periodeoverzicht,
de verliesontleding, de positiegrootte, de brokervergelijking — kwam wel in de
sensorgegevens maar niet in deze export.

Gevolg: elke diagnostiek meldde `robustness: None` en `periods: 0 dagen`, ook
bij ruim honderd trades. De berekening klopte; hij was alleen onzichtbaar.

Dat is vervelender dan het klinkt, want die export is waarop de beoordeling
rust.
