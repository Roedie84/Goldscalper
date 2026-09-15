# Van 4.17.1 naar 4.23.0

Geverifieerd op een verse kloon van je GitHub: **839 tests groen**.

## De fout die je vond

Twee transacties met bijna dezelfde instapprijs, maar tegengestelde richting:

| tijd | grootte | opening | gesloten | winst |
|---|---|---|---|---|
| 09:53 | **-1.74** (sell) | 4287,29 | **4277,08** | EUR +15,27 |
| 09:11 | **+1.74** (buy) | 4287,31 | 4287,37 | EUR +0,09 |

Het verschil in instapprijs is **twee cent** — binnen de tolerantie van vijf.
De eerste kandidaat pakken koppelde de short aan de uitstapprijs van de long.

Gevolg in het rapport:

    09:45  sell  in 4287.29  uit 4287.37  netto -0.14   <- fout
    08:06  buy   in 4287.31  uit 4287.37  netto +0.10   <- klopt

Een winst van ruim vijftien euro werd een verlies van veertien cent.

## De oplossing

Er wordt nu ook op **richting** vergeleken: het teken van de grootte. Een short
kan niet meer aan een long worden gekoppeld.

En bij meerdere kandidaten binnen de tolerantie wordt de **dichtstbijzijnde**
gekozen in plaats van de eerste in de lijst.

## Nieuwe dienst: opnieuw opzoeken

    action: gold_scalper.recheck_exits

De trades die al verkeerd gekoppeld zijn, staan als `gecorrigeerd` in de
database terwijl hun uitstapprijs van een andere trade komt. Deze dienst zet ze
terug op `geschat` zodat ze opnieuw worden opgehaald, nu met de richting erbij.

Gebeurt in stappen van vijf, elke paar minuten. Stops en doelen worden
overgeslagen: die zijn op hun niveau afgerekend en daar valt niets te herzien.

## Wat er wél goed ging

Alle andere gecorrigeerde trades kloppen tot op de cent met het overzicht van
de broker. Bijvoorbeeld:

| instap | mijn netto | omgerekend | broker |
|---|---|---|---|
| 4272,11 | US$ -11,64 | EUR -10,17 | **EUR -10,17** |
| 4265,36 | US$ -11,43 | EUR -9,98 | **EUR -9,99** |
| 4276,25 | US$ +17,14 | EUR +14,74 | **EUR +14,74** |

## Na installatie

1. Herstart Home Assistant.
2. `gold_scalper.recheck_exits` — de bestaande trades opnieuw laten opzoeken.
3. Wacht een kwartier en controleer of de vijftien-euro-trade nu klopt.

Geen nieuwe run: de trades worden gecorrigeerd, niet weggegooid.
