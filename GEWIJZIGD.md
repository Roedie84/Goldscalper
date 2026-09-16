# Van 4.17.1 naar 4.24.2

Geverifieerd op een verse kloon van je GitHub: **855 tests groen**.

## Drie reparaties aan de koppeling met de broker

### 1. Het sluitmoment komt van de broker

Het eigen tijdstempel was het moment waarop de beheerlus de positie
afwikkelde, en dat liep tot negentig minuten uit de pas:

| mijn rij | instap | bij de broker |
|---|---|---|
| 10:31 | 4329,97 | **12:01** |
| 12:30 | 4348,43 | **13:55** |

Naast het overzicht van de broker was het rapport daardoor niet te lezen: je
vergelijkt rijen op tijdstip en koppelt dan de verkeerde trades aan elkaar.

De broker bepaalt wanneer een positie sloot, dus zijn tijdstip is het juiste.
De looptijd schuift mee.

### 2. Omvang scheidt trades die op één cent liggen

Twee longs, instap 4336,13 en 4336,14 — één cent verschil, dus met prijs en
richting niet te onderscheiden. Beide kregen dezelfde uitstapprijs, waarvan er
één verkeerd was.

Hun omvang was 1,69 en 1,75 ounce. De koppeling weegt nu prijs én omvang.

### 3. Het bedrag komt van de broker

Zelf narekenen uit prijzen leverde steeds afwijkingen op door details die niet
te controleren vielen. Het bedrag dat de broker meldt is per definitie juist —
dat is wat er op de rekening gebeurde.

Rijmt het niet met de prijsbeweging, dan verschijnt er een waarschuwing in
plaats van dat het wordt weggerekend.

## Na installatie

    action: gold_scalper.recheck_exits

De 26 onvindbare trades staan allemaal in het overzicht van de broker. Na de
herzoekopdracht krijgen ze hun juiste prijs, bedrag en sluittijd.

Je netto gaat er slechter uitzien: van EUR -22,14 naar ongeveer -78,31 over die
trades. Dat is het wegvallen van een meetfout, geen verslechtering.

## Niet meegeleverd

`dashboard_template.yaml` en `bestandscontrole.json` bestaan niet in deze repo;
Gold Scalper heeft alleen een `dashboard/`-map binnen de integratie zelf.
