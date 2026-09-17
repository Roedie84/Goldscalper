# Versie 5.1.0

Geverifieerd op een verse kloon van je GitHub: **860 tests groen**.

## Wat dit toevoegt

**Zes indicatorwaarden per trade.** ATR, ADX, RSI, EMA-afstand, trendsterkte en
momentum op het instapmoment.

Die werden berekend, gebruikt voor de beslissing en weggegooid. Zonder ze is
geen enkele correlatieanalyse mogelijk: er is niets om de uitkomst tegen af te
zetten.

Van de zestien kenmerken die een edge-analyse vraagt, werden er zes niet
bewaard — en dat waren precies deze. De vraag "welke marktomstandigheden zijn
winstgevend" was daardoor onbeantwoordbaar, niet vanwege te weinig trades maar
omdat de gegevens ontbraken.

Bestaande databases krijgen de kolommen er automatisch bij; er gaat niets
verloren.

**Puur observatie.** Er verandert geen enkele beslissing, dus je bewijsfase
loopt door. Er staat een test op die een toekomstige poging om hier een filter
van te maken tegenhoudt.

## Ook hierin

`signal_confidence` werd nergens vastgelegd terwijl het veld bestond. Nu wel.

## Wat je hierna kunt

Over een paar maanden is de edge-analyse uitvoerbaar. Nu nog niet: elf groepen
uit 105 trades is tien trades per groep, en dat is ruis.

## Niet meegeleverd

`dashboard_template.yaml` en `bestandscontrole.json` bestaan niet in deze repo;
Gold Scalper heeft alleen een `dashboard/`-map binnen de integratie zelf.
