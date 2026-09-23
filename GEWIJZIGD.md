# Versie 5.3.1

Ten opzichte van 5.3.0. Drie bestanden, **902 tests groen**.

## Minder ruis bij een ontbrekende transactie

    Geen transactie gevonden voor instapprijs 4319.64 ...
    nieuwste 2026-09-23T07:28:09

Het overzicht van de broker loopt uren achter: je trade sloot rond 09:27 UTC,
de nieuwste transactie daarin is van 07:28. De correctie probeert het elke paar
minuten opnieuw en vindt hem later vanzelf.

Elke mislukte poging als waarschuwing melden gaf zo'n veertig identieke regels
per trade. Nu één keer per ticket; daarna alleen op debugniveau.
