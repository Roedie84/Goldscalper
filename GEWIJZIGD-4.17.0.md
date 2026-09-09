# Gewijzigd in 4.17.0

| Bestand | Wat er veranderde |
|---|---|
| `tests/test_broker_cycle.py` | **nieuw** — dertien tests, waarvan zes een volledige handelscyclus draaien |
| `tests/conftest.py` | werkende `DataUpdateCoordinator` en `Store` in de teststubs |
| `lifecycle.py` | nulpositiecontrole (was in 4.15.1 verloren gegaan bij het samenvoegen) |
| `diagnostics.py` | `execution_facts` toegevoegd aan de export |

## Het gat dat dit dicht

Vier fouten op rij zaten in hetzelfde pad: alles wat de brokermodus aanraakt.
De papermodus werd goed getest, en dat is een ander pad — de paper-broker houdt
zijn posities in het geheugen, een broker aan de andere kant van een
netwerkverbinding niet.

* `_audit_against_broker` werd aangeroepen maar bestond niet
* `record_close(net_pnl, now)` werd zonder `now` aangeroepen
* een positie van nul ounce gold als verweesd en legde de handel stil
* MFE en MAE bleven op nul bij een positie die de broker sloot

Elke fout kwam pas aan het licht nadat de bot een uur had gedraaid.

## Wat er nu draait

De testopzet kon de coordinator niet bouwen: de gestubde
`DataUpdateCoordinator` viel terug op `object`, en `Store` gaf iets terug dat
niet te awaiten was. Beide zijn nu echte, minimale implementaties.

Daardoor kan de handelslus als geheel draaien, met een gescripte broker die
posities zelf sluit, op nul zet of laat verdwijnen.

Tegenproef gedaan op alle drie de reproduceerbare fouten: elke keer faalt
precies de test die erover gaat.
