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

De betrouwbaarste manier om ze te vinden: Instellingen → Apparaten en diensten
→ Gold Scalper → het apparaat aanklikken. Daar staan ze allemaal.

## Beginnen

E�n schakelaar, en niets gebeurt zonder.

```yaml
service: switch.turn_on
target:
  entity_id: switch.gold_scalper_xau_usd_handel_actief
```

Of via Ontwikkelhulpmiddelen → Acties → `switch.turn_on`.

De stand blijft bewaard tussen herstarts, dus dit hoef je maar één keer te doen.

## Status: waarom gebeurt er niets

`sensor.<...>_status` zegt het in één woord, met een toelichting in het
attribuut `toelichting`.

| Status | Betekenis |
|---|---|
| `uitgeschakeld` | de hoofdschakelaar staat uit |
| `wachtend` | actief, maar nog geen geschikt signaal (reden staat erbij) |
| `positie_open` | er loopt een positie; exits worden bewaakt |
| `gepauzeerd` | tijdelijke pauze na een reeks verliezers |
| `noodstop` | limiet geraakt; vereist `gold_scalper.resume` |
| `afgestemd_probleem` | database en broker oneens over posities |
| `afwikkelen` | bezig met afwikkelen voor een herstart |

Bij `wachtend` staat in de toelichting waaróm: buiten het handelsvenster,
spread te breed, signaal te zwak, verwachte beweging dekt de kosten niet.

## Alle entiteiten

Vervang `<...>` door `gold_scalper_xau_usd` of wat er bij jouw symbool staat.

### Bediening
| Entiteit | Doel |
|---|---|
| `switch.<...>_handel_actief` | hoofdschakelaar, blijft bewaard |
| `button.<...>_alles_sluiten` | noodknop |
| `button.<...>_afwikkelen_voor_herstart` | afwikkelen vóór een update |
| `button.<...>_hervatten_na_noodstop` | noodstop opheffen |
| `button.<...>_keuringsrapport_maken` | rapport schrijven |

### Markt
| Entiteit | Doel |
|---|---|
| `sensor.<...>_koers` | midprijs |
| `sensor.<...>_spread` | bij marktdata: je *aanname*, geen meting |
| `sensor.<...>_atr` | gemiddelde beweging per candle |
| `sensor.<...>_signaal` | richting, score, afwijsreden |

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

Na installatie staat **Gold Scalper** in de zijbalk met het keuringsrapport.
Zie je het niet: hard verversen met Ctrl+Shift+R, of open rechtstreeks
`http://homeassistant.local:8123/api/gold_scalper/report`.

Voor live entiteiten: `dashboard/lovelace.yaml` via Instellingen → Dashboards →
nieuw dashboard → potlood → driepuntsmenu → Ruwe configuratie-editor. **Pas de
entiteit-ID's aan** naar jouw symbool.

## Van databron wisselen

Integratie → driepuntsmenu → **Herconfigureren**. Je tradedatabase blijft
behouden.
