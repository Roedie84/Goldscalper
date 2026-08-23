# Brokerkeuze

Eisen: EU-toezicht, beschikbaar in Nederland, en een REST-API die vanuit Home
Assistant op Linux werkt zonder tweede proces.

| Broker | Toezicht | API | Demo | Adapter |
|---|---|---|---|---|
| **Capital.com** | CySEC, EU-paspoort (AFM/DNB toezicht) | REST + WebSocket, gratis | ja | te bouwen |
| **OANDA TMS** | KNF (Polen) | v20 REST — **onzeker voor EU-accounts** | ja | `broker/oanda.py` |
| **IG** | BaFin (IG Europe GmbH) | REST + Lightstreamer | ja | te bouwen |
| **Saxo Bank** | Deense FSA, NL-vestiging | OpenAPI (OAuth) | ja | te bouwen |
| **XTB** | KNF | WebSocket/JSON, geen REST | ja | te bouwen |
| Interactive Brokers | Central Bank of Ireland | vereist IB Gateway | ja | past niet |
| AvaTrade | CBI (Ierland) | alleen MetaTrader | ja | past niet |
| Plus500, DEGIRO, eToro | divers | geen publieke API | — | past niet |

## Het probleem met OANDA in de EU

De v20 REST-adapter is gebouwd op OANDA's gepubliceerde API-documentatie. Maar
OANDA verplaatste in 2023 hun EU-operatie naar Warschau onder de Poolse KNF, en
die entiteit (OANDA TMS Brokers S.A.) biedt handel via MetaTrader 5 en hun eigen
platform. Of TMS-accounts toegang krijgen tot v20 is niet uit de documentatie op
te maken.

**Controleer dit vóór je verder bouwt.** Open een practice-account en kijk of je
onder My Services een "Manage API Access" ziet met een tokenoptie. Zo niet, dan
werkt `broker/oanda.py` niet met jouw account.

## Waarom Capital.com de veiligste keuze is

- Gedocumenteerde publieke REST-API met eigen demo-omgeving
- CySEC-vergunning met EU-paspoort; AFM en DNB houden lokaal toezicht
- Beleggerscompensatie tot 20.000 euro
- Geen commissie; kosten zitten in de spread
- Authenticatie via API-sleutel plus sessie (2FA moet aan staan vóór je een
  sleutel kunt genereren)

Aandachtspunt: de sessie verloopt na 10 minuten inactiviteit. De adapter moet
dus zelf hernieuwen. Dat is een klein stukje extra logica dat OANDA niet nodig
heeft, maar het is goed te doen.

## ESMA-hefboom

Als particuliere klant in de EU krijg je verplicht lagere hefbomen dan brokers
adverteren:

| Instrument | Maximum |
|---|---|
| Majors (EUR/USD) | 30:1 |
| **Goud** | **20:1** |
| Overige grondstoffen | 10:1 |
| Aandelen | 5:1 |
| Crypto | 2:1 |

De code rekent nu met 20:1. Ziet een broker 200:1 adverteren, dan geldt dat voor
professionele klanten of niet-EU-entiteiten. Reken met wat je werkelijk krijgt,
anders denkt de margeberekening dat er ruimte is die er niet is.

Verder gelden in de EU verplichte negatieve-saldobescherming en
margin-close-out op 50%.

## Wat een andere broker kost aan werk

`broker/adapter.py` definieert de interface: koersen, candles, account,
posities, order plaatsen, sluiten, stop verplaatsen. Een nieuwe broker is één
bestand van ongeveer 250 regels plus tests. Alles daarboven — analyse,
strategie, papersimulatie, database, poort, risicobewaking, exits, rapportage —
verandert niet mee.
