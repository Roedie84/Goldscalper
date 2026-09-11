# Van 4.17.1 naar 4.21.4

Geverifieerd op een verse kloon van je GitHub: **826 tests groen**.

## De oorzaak, eindelijk gevonden

De logregel uit 4.21.3 gaf het antwoord:

    Transactieoverzicht van de broker: 1 transacties.
    Velden: [..., 'closeLevel', ..., 'openLevel', 'profitAndLoss', ...]

**De veldnamen klopten al.** `openLevel`, `closeLevel` en `profitAndLoss` zijn
precies wat de code leest.

Het probleem was **één transactie**. Zonder `from` en `to` levert dit endpoint
een heel smal venster: in de praktijk alleen de meest recente transactie. Alle
andere posities vonden dus nooit een match — en de veldnamen, die gewoon goed
waren, kregen twee ronden lang de schuld.

Nu wordt er vierentwintig uur teruggevraagd met `pageSize: 200`. Dat kost niets
tegen het datapuntenquotum, want dit endpoint telt daar niet tegen.

## En de tolerantie terug naar strak

In 4.21.3 had ik de prijsmarge naar 0,60 verruimd, voor het geval de broker
zou afronden. Uit de werkelijke gegevens blijkt dat `openLevel` **exact**
overeenkomt met de eigen instapprijs, tot op de cent.

Een ruime marge zou hier juist schaden: bij goud liggen opeenvolgende instappen
vaak binnen een dollar van elkaar, en dan koppel je de verkeerde trade. Terug
naar 0,05.

## Nieuw: melding bij een smal venster

Komen er minder dan drie transacties over een etmaal terug, dan verschijnt er
een waarschuwing. Dat is minder dan er trades zijn geweest, en precies het
signaal dat het datumbereik niet aankomt.

## Wat je moet controleren

Bij de eerste afgewikkelde trade na installatie:

* `broker_gesloten_gemeten` in de sluitreden — het werkt
* `conversion` in de diagnostiek krijgt een koers rond 0,868
* `estimated_settlements` blijft op nul staan

Gaat het mis, dan staat er weer een logregel met het aantal transacties.

## Over run 94

Onbruikbaar. Alle 21 trades zijn op een geschatte uitstapprijs afgerekend, en
uit de vergelijking met het overzicht van de broker blijkt dat die gemiddeld
**9,31 dollar** mis waren — bij twee trades zelfs met het verkeerde teken: een
verlies geboekt als winst, en een winst van bijna dertien dollar als verlies.

Begin opnieuw met `gold_scalper.new_run` zodra dit draait en de sluitreden
`gemeten` is.
