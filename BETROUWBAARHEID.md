# Betrouwbaarheid bij echt geld

Papermodus kan nooit volledig gelijk zijn aan live. Dit document beschrijft wat
er is gedaan om het verschil te verkleinen, en - belangrijker - welke verschillen
blijven bestaan.

## Wat er is gerepareerd

### Gemiste stops (12% van de gevallen)

Papermodus controleerde stops alleen op het pollmoment, elke twintig seconden.
Een broker toetst elke tick. Gemeten over 600 candles: van de 183 stops die een
broker zou hebben geraakt, zag papermodus er 22 niet - posities die live waren
uitgestopt, liepen op papier door en maakten soms alsnog winst.

Dat is een systematische vertekening **in je voordeel**, en juist die soort
fout zet je op een verkeerd besluit over echt geld.

Nu worden stops getoetst tegen de hoogste en laagste koers sinds de vorige
cyclus, en wordt er afgerekend op het stopniveau in plaats van op de prijs die
twintig seconden later toevallig geldt.

Zijn stop en take-profit binnen hetzelfde interval allebei geraakt, dan neemt
de simulatie de **stop** aan. Uit een candle valt niet af te leiden welke eerst
kwam, en gokken op de gunstige volgorde is precies hoe een backtest zichzelf
rijk rekent.

### Onbeschermde posities

Het enige scenario met in principe **onbegrensd verlies**: de order wordt
gevuld maar de stop komt er niet op, omdat de broker een minimale stopafstand
hanteert, het niveau inmiddels aan de verkeerde kant ligt, of de tweede aanroep
faalde.

`SafeExecutor.open_protected()` handelt dat af:

1. Een order zonder stop wordt categorisch geweigerd
2. Na de fill wordt geverifieerd dát de stop erop zit
3. Zo niet, wordt hij alsnog geplaatst en opnieuw geverifieerd
4. Lukt dat niet, gaat de positie direct dicht - een kleine zekere kost is
   beter dan een onbekende
5. Lukt sluiten óók niet, dan volgt een `CRITICAL`-melding met de opdracht
   handmatig in te grijpen

Daarnaast controleert de bot elke tiende cyclus alle open posities op een stop.
Ontbreekt er een, dan volgt een noodstop.

### Dubbele orders

Valt de verbinding weg ná verzending maar vóór het antwoord, dan weet je niet
of de order is uitgevoerd. Opnieuw sturen verdubbelt je positie; niet sturen
laat een onbewaakte positie achter.

Elke order krijgt daarom een uniek ordernummer mee. Na een fout worden de
posities bij de broker nagekeken op dat nummer. Wordt hij gevonden, dan is de
order dus wél uitgevoerd en volgt er geen tweede.

### Ongeldige orderparameters

Vooraf gecontroleerd in plaats van door de broker laten weigeren: volume
afgerond op de step en getoetst aan minimum en maximum, prijzen afgerond op de
tick, stop aan de juiste kant van de markt.

Ligt de stop dichter op de markt dan de broker toestaat, dan wordt de trade
**overgeslagen** en niet de stop verder weggezet. Dat laatste zou je risico
vergroten zonder dat je het vroeg.

## Wat er verschillend blijft

Deze punten kan de simulatie niet nabootsen. Reken erop dat live daardoor
slechter uitvalt dan papier, niet beter.

| Verschil | Gevolg |
|---|---|
| **Spread is een aanname** | zolang je geen brokerfeed hebt, is je grootste kostenpost geraden |
| **Slippage is gemodelleerd** | de werkelijke slippage wordt pas live gemeten |
| **Requotes** | een order kan geweigerd worden op de prijs die je vroeg |
| **Partiële fills** | je krijgt soms minder dan je vroeg |
| **Spreadverbreding rond nieuws** | kan binnen een seconde vervijfvoudigen |
| **Latency tussen signaal en fill** | 50-300 ms waarin de prijs beweegt |
| **Weekendgaps** | maandagopening kan voorbij je stop liggen |
| **Je broker ziet je orderflow** | market makers zijn je tegenpartij |

Dat eerste punt is het zwaarste. Zolang je op publieke data draait is de spread
geraden, en bij scalping is dat de dominante kostenpost. **De bewijsfase is pas
werkelijk bewijs als hij op een brokerfeed met echte bied- en laatprijzen
draait.**

## De volgorde die dit ondersteunt

1. **Publieke marktdata, aangenomen spread 0** - werkt de machinerie
2. **Publieke marktdata, spread 0,25** - blijft er iets over bij realistische kosten
3. **Broker demo-account** - echte quotes, gemeten spread, gemeten slippage
4. **Live** - alleen als de poort daadwerkelijk opengaat

Stap 3 is niet overslaan. Pas daar meet je de twee getallen waarop alles
steunt, en pas daar leer je hoe jouw broker zich gedraagt bij requotes en
partiële fills.

Vergelijk na stap 3 de gemeten slippage met de 0,02 die de simulatie aannam.
Valt hij structureel hoger uit, dan waren je papercijfers te optimistisch en
moet de bewijsfase opnieuw met de werkelijke waarde.
