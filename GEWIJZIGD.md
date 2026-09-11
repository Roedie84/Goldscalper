# Van 4.17.1 naar 4.21.3

Geverifieerd op een verse kloon van je GitHub: **823 tests groen**.

## De stand van zaken

Alle 21 trades in run 94 staan op `broker_gesloten_geschat`. De werkelijke
uitstapprijs wordt nog steeds niet gevonden.

Twee pogingen zijn mislukt:

1. Zoeken op het **dealId** tegen het veld `reference` — twee verschillende
   identificaties, die matchen nooit.
2. Zoeken op de **instapprijs** tegen `openLevel` — werkt evenmin.

Beide keren was de oorzaak een aanname over veldnamen die ik niet kon
controleren, want ik heb geen toegang tot je account.

## Daarom eerst kijken, dan repareren

Deze versie logt bij de eerste poging **precies wat de broker teruggeeft**:

    Transactieoverzicht van de broker: 34 transacties. Velden van de eerste:
    ['cashTransaction', 'closeLevel', 'currency', 'date', ...]. Eerste
    transactie: {...}

Die regel staat één keer in het logboek, bij `custom_components.gold_scalper
.broker.ig_capital`. **Stuur hem op** — dan weet ik welk veld ik moet lezen in
plaats van te blijven gokken.

Is het overzicht leeg, dan staat er:

    Het transactieoverzicht van de broker is leeg.

Dat zou betekenen dat het endpoint of de rechten niet deugen, en dan is het een
ander probleem.

## Wat er ondertussen verruimd is

* Vier veldnamen geprobeerd voor de instapprijs, vier voor de uitstapprijs,
  vier voor de winst.
* De prijstolerantie van 0,05 naar 0,6, want de broker kan afronden en een te
  strenge vergelijking laat de match precies mislukken waar hij nodig is.

Misschien werkt het daarmee al. De logregel vertelt het.

## Over je cijfers

Run 94: 21 trades, netto -13,21, t = -2,51.

**Negeer die t-waarde.** Bij 21 trades zegt hij niets, en bovendien zijn alle
21 op een geschatte uitstapprijs afgerekend. Uit de vergelijking met jouw
IG-scherm bleek dat die schattingen tien dollar per trade kunnen schelen — in
beide richtingen.

Deze run is pas bruikbaar als de sluitreden `broker_gesloten_gemeten` wordt.
