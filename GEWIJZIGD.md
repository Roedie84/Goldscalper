# Van 4.17.1 naar 4.23.2

Geverifieerd op een verse kloon van je GitHub: **845 tests groen**.

## De fout in 4.23.1

`onvindbaar` sprong van 3 naar 26 — vrijwel alle nieuwe trades.

Mijn regel gaf op na drie pogingen, oftewel ruim tien minuten. Maar het
transactieoverzicht van de broker loopt uren achter; dat heb ik zelf gemeten:
nieuwste transactie 10:51 bij een opvraging om 14:22.

Elke nieuwe trade werd dus drie keer tevergeefs gezocht en daarna definitief
opgegeven — uren voordat de prijs beschikbaar kwam.

**Gevolg voor je cijfers.** Die 26 hielden hun geschatte prijs, en die
comprimeert naar nul:

| | bij 37 trades | bij 60 trades |
|---|---|---|
| gemiddelde winst | 14,51 | 10,61 |
| gemiddeld verlies | -10,06 | -6,54 |

Dat zag eruit als een verslechterende strategie en was een meetfout.

## De oplossing

Opgeven op **leeftijd** in plaats van op aantal pogingen: pas na twee dagen.
Dat is langer dan de vertraging die ooit is gemeten, en kort genoeg om een
trade niet eeuwig op te zoeken.

## Na installatie

    action: gold_scalper.recheck_exits

Draai die nog één keer. De 26 die te vroeg zijn opgegeven worden dan opnieuw
opgezocht, en hun prijs staat nu wel bij de broker.

## Niet meegeleverd

`dashboard_template.yaml` en `bestandscontrole.json` bestaan niet in deze repo —
Gold Scalper heeft alleen een `dashboard/`-map binnen de integratie zelf. Die
twee horen bij je EMS-project.
