# Van 4.17.1 naar 4.25.0

Geverifieerd op een verse kloon van je GitHub: **855 tests groen**.

## De waarden klopten al

Negen van je elf zichtbare trades kloppen tot op de cent met het overzicht van
de broker. Het probleem was de **eenheid**: mijn tabel stond in de valuta van
het instrument, de broker rekent in accountvaluta.

    -9.49 x 0.874 = -8.29   tegenover de -8.29 van de broker

Bij een koers rond 0,87 scheelt dat dertien procent, en dan lijkt een
kloppende administratie fout.

## Nieuw: een kolom in accountvaluta

De tradetabel heeft er een kolom bij met het bedrag in de valuta van je
rekening, naast het bedrag in instrumentvaluta. Nu is elke regel rechtstreeks
te vergelijken.

Zonder bekende koers blijft die kolom leeg in plaats van een geschat bedrag te
tonen: een bedrag dat eruitziet als een meting maar er geen is, is erger dan
een leeg veld.

## Ook in deze versie

**Het sluitmoment komt van de broker.** Het eigen tijdstempel liep tot
negentig minuten uit de pas, waardoor je rijen op tijdstip vergeleek en bij de
verkeerde trade uitkwam.

**Omvang scheidt trades die op één cent liggen.** Twee longs op 4336,13 en
4336,14 kregen dezelfde uitstapprijs; hun omvang van 1,69 tegen 1,75 ounce
scheidt ze wel.

**Het bedrag komt van de broker.** Zelf narekenen uit prijzen leverde steeds
afwijkingen op door details die niet te controleren vielen.

**Een wisselvallige test hersteld.** `test_history_is_reproducible` faalde
ongeveer één op de honderd keer omdat de gesimuleerde reeks aan het huidige
moment is geankerd en er een minuutgrens tussen twee aanroepen kon vallen. Een
test die soms faalt leer je negeren.

## Na installatie

    action: gold_scalper.recheck_exits

De trades krijgen dan hun juiste prijs, bedrag en sluittijd, en de nieuwe kolom
laat ze naast het overzicht van de broker zien.

## Niet meegeleverd

`dashboard_template.yaml` en `bestandscontrole.json` bestaan niet in deze repo;
Gold Scalper heeft alleen een `dashboard/`-map binnen de integratie zelf.
