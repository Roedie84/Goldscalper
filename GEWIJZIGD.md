# Van 4.17.1 naar 4.22.0

Geverifieerd op een verse kloon van je GitHub: **830 tests groen**.

## Wat de logregel verder liet zien

Het datumbereik werkt: **33 transacties** in plaats van één.

Maar er staat nog iets in die regel:

    nieuwste transactie: 2026-09-11T06:07:47
    opgevraagd om:       10:30:15

**Het transactieoverzicht loopt ruim vier uur achter.** Op het moment dat de
lus een positie afwikkelt, staat de werkelijke uitstapprijs er nog niet in.
E�n poging is dus principieel niet genoeg, hoe goed de zoekopdracht ook is.

## Daarom: later corrigeren

Elke tien minuten worden trades die als schatting zijn geboekt opnieuw
opgezocht. Lukt het dan, dan wordt de trade bijgewerkt:

    Trade FZC2JGB2 gecorrigeerd: netto van -0.05 naar -8.69
    (uitstapprijs 4358.34 in plaats van een schatting).

De sluitreden wordt `broker_gesloten_gecorrigeerd`, zodat je in het rapport ziet
welke cijfers uit een correctie komen.

Hoogstens vijf per cyclus, anders loopt de handelslus vast op netwerkverzoeken.

## En bij een mislukte match: zeggen waarom

Wordt er niets gevonden, dan staat er nu in het logboek welke prijs werd
gezocht, hoeveel transacties er lagen, van wanneer de nieuwste en oudste waren,
en de drie dichtstbijzijnde instapprijzen met hun verschil.

Twee eerdere pogingen faalden op een aanname die ik niet kon controleren. Deze
regel maakt dat onmogelijk.

## Wat je moet controleren

Na installatie en een paar trades:

* `broker_gesloten_gecorrigeerd` in de sluitredenen — de correctie werkt
* `estimated_settlements` loopt terug naar nul
* `conversion.rate` krijgt een waarde rond 0,868

Blijft alles op `geschat` staan, dan staat er een logregel met de gezochte
prijs en de kandidaten. Stuur die op.

## Over de lopende run

Run 95 heeft één trade en die is geschat. Zodra de correctie werkt, wordt hij
bijgewerkt en kan de run gewoon doorlopen — een nieuwe run is niet nodig.
