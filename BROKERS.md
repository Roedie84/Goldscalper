# Brokerkeuze

## Wat er nu in zit

| Databron | API | Kan handelen | Toezicht | Door mij getest |
|---|---|---|---|---|
| **IG** | REST + streaming | ja | BaFin (IG Europe) | alleen nagebootst |
| **Capital.com** | REST + WebSocket | ja | CySEC | alleen nagebootst |
| OANDA | v20 REST | ja | KNF (Polen) | alleen nagebootst |
| Yahoo | publieke chart-API | nee | — | alleen nagebootst |
| Stooq | CSV | nee | — | alleen nagebootst |
| Simulator | lokaal | nee | — | volledig |

**Geen van de brokeradapters is tegen een echte verbinding getest.** Mijn
omgeving mag ze niet bereiken. De parsing is gebouwd op hun publieke
documentatie en getoetst tegen nagebootste antwoorden; dat vangt logicafouten,
maar niet of het endpoint doet wat de documentatie belooft.

Reken erop dat de eerste verbinding iets oplevert dat nog niet klopt. De
foutmeldingen zijn daarom zo specifiek mogelijk: status, foutcode van de
broker, en waar mogelijk wat je eraan kunt doen.

## IG

REST-API met aparte endpoints voor demo (`demo-api.ig.com`) en live
(`api.ig.com`), dus hetzelfde adapterbestand werkt voor beide.

**Aandachtspunt bij aanmelden:** om de API op demo te gebruiken moet je
hetzelfde e-mailadres gebruiken als je live account. In de praktijk: eerst een
live account registreren, dan van daaruit een demo openen. Registreren
verplicht je niet tot storten.

**Twee stappen bij een order.** IG geeft eerst een `dealReference` terug en pas
daarna, via `/confirms/{ref}`, het `dealId` en of hij is geaccepteerd. Dat is
extra werk maar juist gunstig: het geeft een spoor dat na een verbroken
verbinding terug te vinden is, en daar rust de bescherming tegen dubbele orders
op.

De demo-rate-limits liggen lager dan live en zijn zonder aankondiging gewijzigd.
Bij twintig seconden verversing zit je ruim onder elke limiet.

## Capital.com

Hun API is gemodelleerd naar die van IG: zelfde sessie met `CST`- en
`X-SECURITY-TOKEN`-headers, vergelijkbare endpoints. Daarom delen beide
adapters het meeste van hun code.

**Voordeel:** geen live account nodig voor demo-toegang.

### Een sleutel aanmaken

1. Zet **2FA** aan (verplicht vóór je een sleutel kunt maken)
2. Instellingen → API integraties → **Generate API key**
3. Geef de sleutel een naam en **stel een API-sleutelwachtwoord in**
4. Voer je 2FA-code in

Er bestaat maar één soort sleutel en die geeft handelsrechten; read-only sleutels
zijn er niet. Dat verklaart de tekst "alleen met Trade toestemming" op dat
scherm - informatief, geen blokkade.

### De valkuil met het wachtwoord

Bij `POST /session` verwacht Capital.com je login plus het
**API-sleutelwachtwoord** dat je bij stap 3 hebt ingesteld. **Niet** je
inlogwachtwoord.

Vul je het verkeerde in, dan krijg je een 401 die er precies zo uitziet als
verkeerde inloggegevens, en ga je in de verkeerde richting zoeken. IG gebruikt
wél gewoon je accountwachtwoord.

### Zie je geen knop?

De documentatie noemt 2FA als enige voorwaarde. Staat die aan en zie je hem
toch niet: scroll in het paneel (het lijkt afgekapt), probeer een
desktopbrowser in plaats van de app, en controleer of je accountverificatie
volledig is afgerond.

De sessie verloopt na tien minuten inactiviteit; de adapter logt automatisch
opnieuw in bij een 401.

## OANDA

De v20-adapter is gebouwd op OANDA's gepubliceerde API, maar hun Europese tak
is in 2023 verhuisd naar OANDA TMS Brokers in Warschau, en die entiteit werkt
met MetaTrader 5. Of een EU-account v20-toegang krijgt, is uit de documentatie
niet op te maken.

Controleer dat vóór gebruik: zie je onder My Services een 'Manage API Access'
met een tokenoptie, dan werkt de adapter mogelijk.

## Van broker wisselen

Integratie → driepuntsmenu → **Herconfigureren**. De adapterlaag zorgt dat
alles daarboven — analyse, strategie, database, poort, exits, rapportage —
ongewijzigd blijft.

Let op: van broker wisselen begint een nieuwe run, want resultaten met een
andere spread zijn niet vergelijkbaar. De oude run blijft in het rapport staan.

## ESMA-hefboom

Als particuliere klant in de EU:

| Instrument | Maximum |
|---|---|
| Majors | 30:1 |
| **Goud** | **20:1** |
| Overige grondstoffen | 10:1 |
| Aandelen | 5:1 |

De code rekent met 20:1. Adverteert een broker met meer, dan geldt dat voor
professionele klanten of niet-EU-entiteiten.

## De volgorde die ik zou aanhouden

1. Demo-account aanmaken bij IG of Capital.com
2. Verbinding testen; stuur me de foutmelding als er iets misgaat
3. Papermodus draaien op de **echte quotes** van die broker — pas dan is je
   spread gemeten in plaats van geraden
4. Pas daarna is de bewijsfase werkelijk bewijs
