# Entiteiten en bediening

## Belangrijk: entiteit-ID's bevatten het symbool

Home Assistant zet de apparaatnaam vóór elke entiteitnaam. Het apparaat heet
`Gold Scalper XAU_USD`, dus de ID's zien er zo uit:

```
switch.gold_scalper_xau_usd_handel_actief
sensor.gold_scalper_xau_usd_status
sensor.gold_scalper_xau_usd_signaal
```

**Niet** `switch.gold_scalper_handel_actief` — die bestaat niet. Verander je het
symbool of de databron, dan veranderen de ID's mee.

Betrouwbaarste manier om ze te vinden: Instellingen → Apparaten en diensten
→ Gold Scalper → het apparaat aanklikken.

## Beginnen

Één schakelaar, en niets gebeurt zonder.

```yaml
service: switch.turn_on
target:
  entity_id: switch.gold_scalper_xau_usd_handel_actief
```

De stand blijft bewaard tussen herstarts, dus dit hoef je maar één keer te doen.

## Status: waarom gebeurt er niets

`sensor.<...>_status` zegt het in één woord, met een toelichting in het
attribuut `toelichting`.

| Status | Betekenis |
|---|---|
| `uitgeschakeld` | de hoofdschakelaar staat uit |
| `markt_gesloten` | goudmarkt dicht; hervat vanzelf, geen storing |
| `wachtend` | actief, maar nog geen geschikt signaal (reden staat erbij) |
| `positie_open` | er loopt een positie; exits worden bewaakt |
| `gepauzeerd` | tijdelijke pauze na een reeks verliezers |
| `noodstop` | limiet geraakt; vereist `gold_scalper.resume` |
| `afgestemd_probleem` | database en broker oneens over posities |
| `afwikkelen` | bezig met afwikkelen voor een herstart |

## Alle entiteiten

Vervang `<...>` door `gold_scalper_xau_usd` of wat er bij jouw symbool staat.

### Bediening
| Entiteit | Doel |
|---|---|
| `switch.<...>_handel_actief` | hoofdschakelaar, blijft bewaard |
| `button.<...>_alles_sluiten` | noodknop |
| `button.<...>_afwikkelen_voor_herstart` | afwikkelen voor een update |
| `button.<...>_hervatten_na_noodstop` | noodstop opheffen |
| `button.<...>_keuringsrapport_maken` | rapport naar bestand schrijven |

### Markt
| Entiteit | Doel |
|---|---|
| `sensor.<...>_koers` | midprijs |
| `sensor.<...>_spread` | bij marktdata: je *aanname*, geen meting |
| `sensor.<...>_atr` | gemiddelde beweging per candle |
| `sensor.<...>_signaal` | richting, score, componenten, afwijsreden |

### Resultaat
| Entiteit | Doel |
|---|---|
| `sensor.<...>_nettoresultaat` | netto, met bruto en kosten als attribuut |
| `sensor.<...>_kosten` | totale transactiekosten |
| `sensor.<...>_trades` | aantal gesloten trades |
| `sensor.<...>_evaluaties` | signaaltrechter met afwijsredenen |
| `sensor.<...>_oordeel` | uitkomst bewijsfase |

### Bewaking
| Entiteit | Doel |
|---|---|
| `binary_sensor.<...>_noodstop` | limiet geraakt |
| `binary_sensor.<...>_dataprobleem` | OHLCV-kolommen uit de pas |
| `binary_sensor.<...>_modus_genegeerd` | gekozen modus wordt overruled |
| `binary_sensor.<...>_veilig_herstarten` | mag HA nu herstart worden |
| `binary_sensor.<...>_live_vrijgegeven` | staat de poort open |

## Dashboard

Na installatie staat **Gold Scalper** in de zijbalk. Zie je het niet: hard
verversen met Ctrl+Shift+R, of open rechtstreeks
`http://homeassistant.local:8123/api/gold_scalper/report`.

### Hoe vaak wordt het bijgewerkt

Het rapport wordt bij elke aanvraag opnieuw uit de database opgebouwd, en
ververst zichzelf **elke 60 seconden**. Rechtsboven staat wanneer het is
opgemaakt, in jouw lokale tijd.

Zestig seconden is een compromis. De onderliggende data ververst elke twintig
seconden, maar het rapport opbouwen kost bij duizenden trades honderden
milliseconden; drie keer per minuut zou dat verdrievoudigen zonder dat je meer
te zien krijgt.

Aanpassen per aanvraag:

```
/api/gold_scalper/report?refresh=30     sneller (minimaal 15)
/api/gold_scalper/report?refresh=0      uit; alleen handmatig verversen
```

De verversing gebruikt een `meta http-equiv`-tag, geen JavaScript. Bewuste
keuze: het paneel is zonder authenticatie bereikbaar, en dan houd je het
scriptvrij.

### Tijden

De database bewaart alles in UTC — de enige zinnige keuze voor een reeks die
over de zomertijdwissel heen loopt. Het rapport toont de tijdzone die in Home
Assistant is ingesteld: voor Nederland UTC+2 in de zomer, UTC+1 in de winter.

## Lovelace

`dashboard/lovelace.yaml` via Instellingen → Dashboards → nieuw dashboard →
potlood → driepuntsmenu → Ruwe configuratie-editor. **Pas de entiteit-ID's aan**
naar jouw symbool.

## Van databron wisselen

Integratie → driepuntsmenu → **Herconfigureren**. Je tradedatabase blijft
behouden.
