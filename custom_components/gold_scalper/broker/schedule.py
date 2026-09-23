"""Bekende handelstijden voor goud, als onafhankelijke controle.

De integratie leunt op het veld ``marketState`` dat de broker meestuurt. Dat
werkt, maar het is één bron: klopt dat veld niet, dan handelt de bot op
verouderde koersen zonder dat iets het merkt. Een tweede, onafhankelijke bron
maakt dat zichtbaar.

**Bij onenigheid wint 'gesloten'.** Zegt de broker open en het rooster dicht, of
andersom, dan wordt er niet gehandeld. Dat is niet uit voorzichtigheid maar uit
rekenkunde: een gemiste kans kost je niets, handelen op een koers van uren
geleden kan je alles kosten.

**Het rooster is geen waarheid.** Feestdagen, vervroegde sluitingen en
uitzonderingen staan er niet in, en tijden veranderen zonder aankondiging. Het
dient dus als *vangnet* en niet als vervanging - vandaar dat een afwijking
gemeld wordt in plaats van stil gecorrigeerd. Blijft die afwijking bestaan, dan
klopt het rooster niet meer en moet het bijgesteld.

Alle tijden in Europe/Amsterdam, want dat is hoe IG ze publiceert voor
Nederlandse klanten en hoe jij ernaar kijkt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("Europe/Amsterdam")


@dataclass(slots=True)
class Session:
    """Wanneer een instrument volgens het rooster open is."""

    name: str
    #: Weekdag en tijd waarop de week opent (0 = maandag).
    opens_weekday: int
    opens_at: time
    #: Weekdag en tijd waarop de week sluit.
    closes_weekday: int
    closes_at: time
    #: Dagelijkse onderbreking, of None.
    daily_break: tuple[time, time] | None = None


#: Spot goud bij IG: maandag 00:00 tot vrijdag 23:00, Nederlandse tijd.
#:
#: De dagelijkse pauze van 23:00 tot 24:00 geldt bij IG voor de futures; voor
#: spot is die er in de praktijk ook, omdat de onderliggende markt dan sluit.
#: Hij staat er daarom in - een uur niet handelen kost je weinig, handelen in
#: een markt zonder liquiditeit kost je de spread.
#: Het rooster is een *vermoeden*, geen waarheid.
#:
#: Twee bronnen geven verschillende tijden voor spot goud bij IG: de
#: Nederlandse publicatie zegt een pauze van 23:00 tot 24:00 lokale tijd, een
#: andere bron zegt 22:00 tot 23:00 UTC - dat scheelt een uur in zomertijd.
#:
#: Welke klopt valt van buitenaf niet vast te stellen, en het hoeft ook niet:
#: bij onenigheid wint altijd 'gesloten', dus er wordt nooit tegen de broker in
#: gehandeld. Het rooster dient alleen om te merken wanneer het veld
#: ``marketState`` zelf niet deugt.
#:
#: Wat wél helpt is meten wanneer de broker de markt werkelijk sluit; zie
#: ``observed_closures`` hieronder.
SPOT_GOLD = Session(
    name="spot goud",
    opens_weekday=0, opens_at=time(0, 0),
    closes_weekday=4, closes_at=time(23, 0),
    daily_break=(time(23, 0), time(23, 59, 59)),
)

#: Weekendhandel bij IG: zaterdag 09:00 tot zondag 23:40. Een apart instrument
#: met een veel bredere spread; alleen relevant als je daar bewust op handelt.
WEEKEND_GOLD = Session(
    name="weekend goud",
    opens_weekday=5, opens_at=time(9, 0),
    closes_weekday=6, closes_at=time(23, 40),
)


def is_open(session: Session, moment: datetime | None = None) -> tuple[bool, str]:
    """Zou de markt volgens het rooster open moeten zijn?"""
    moment = (moment or datetime.now(timezone.utc)).astimezone(MARKET_TZ)
    weekday = moment.weekday()
    clock = moment.time()

    if session.daily_break:
        start, end = session.daily_break
        if start <= clock <= end:
            return False, (
                f"dagelijkse onderbreking van {start:%H:%M} tot 24:00"
            )

    if session.opens_weekday <= session.closes_weekday:
        # Binnen één week, bijvoorbeeld maandag tot vrijdag.
        if weekday < session.opens_weekday or weekday > session.closes_weekday:
            return False, "buiten de handelsweek"
        if weekday == session.opens_weekday and clock < session.opens_at:
            return False, f"opent om {session.opens_at:%H:%M}"
        if weekday == session.closes_weekday and clock >= session.closes_at:
            return False, f"gesloten sinds {session.closes_at:%H:%M}"
        return True, "binnen de handelstijden"

    # Loopt over het weekeinde heen, bijvoorbeeld zaterdag tot zondag.
    if weekday == session.opens_weekday:
        return (clock >= session.opens_at), "weekendsessie"
    if weekday == session.closes_weekday:
        return (clock < session.closes_at), "weekendsessie"
    return False, "buiten de weekendsessie"


def minutes_until_close(
    session: Session, moment: datetime | None = None
) -> float | None:
    """Hoeveel minuten tot de eerstvolgende sluiting, of None als hij dicht is.

    Bedoeld om te voorkomen dat er kort voor sluiting nog een positie opengaat.
    Een trade met een tijdslimiet van vijf minuten die om 22:58 opengaat, wordt
    door de sluiting overvallen: je zit dan met een positie in een markt die
    niet meer beweegt en pas de volgende sessie weer opent - met een gat.
    """
    moment = (moment or datetime.now(timezone.utc)).astimezone(MARKET_TZ)
    open_now, _ = is_open(session, moment)
    if not open_now:
        return None

    clock = moment.time()
    grenzen = []
    if session.daily_break:
        grenzen.append(session.daily_break[0])
    if moment.weekday() == session.closes_weekday:
        grenzen.append(session.closes_at)
    if not grenzen:
        return None

    nu = clock.hour * 60 + clock.minute + clock.second / 60
    resterend = [
        (g.hour * 60 + g.minute) - nu for g in grenzen
        if (g.hour * 60 + g.minute) > nu
    ]
    return min(resterend) if resterend else None


def cross_check(
    broker_says_open: bool,
    session: Session = SPOT_GOLD,
    moment: datetime | None = None,
) -> tuple[bool, str | None]:
    """Vergelijk wat de broker zegt met het rooster.

    Geeft terug of er gehandeld mag worden, plus een melding als de twee het
    oneens zijn. Bij onenigheid wint altijd 'gesloten': een gemiste kans kost
    je niets, handelen op een koers van uren geleden kan je alles kosten.
    """
    schedule_open, reason = is_open(session, moment)

    if broker_says_open == schedule_open:
        return schedule_open, None

    if broker_says_open and not schedule_open:
        return False, (
            f"De broker meldt de markt open, maar volgens het rooster is hij "
            f"dicht ({reason}). Er wordt niet gehandeld. Klopt dit vaker, dan "
            "is het rooster verouderd en moet het bijgesteld."
        )

    # De broker weet het beter dan het rooster.
    #
    # Feestdagen en vervroegde sluitingen staan hier niet in, en een kalender
    # bijhouden zou betekenen dat je hem elk jaar moet onderhouden - met als
    # risico dat een vergeten dag je op verouderde koersen laat handelen.
    #
    # Andersom is het rooster wél nuttig: als de broker "open" zegt terwijl het
    # rooster dicht zegt, is er iets mis met het veld en wil je dat weten.
    return False, (
        f"De broker meldt de markt gesloten terwijl het rooster hem open zegt "
        f"({reason}). Vrijwel altijd een feestdag of vervroegde sluiting - de "
        "Amerikaanse kalender staat niet in het rooster. De broker heeft "
        "gelijk; er wordt niet gehandeld."
    )


@dataclass(slots=True)
class ClosureObservation:
    """Wanneer de broker de markt werkelijk gesloten meldde.

    Bestaat omdat het rooster een vermoeden is en de waarneming niet. Twee
    bronnen geven verschillende tijden voor spot goud bij IG, en welke klopt
    valt van buitenaf niet vast te stellen. Wat je wél kunt doen is bijhouden
    wanneer de broker sluit, en het rooster daarna bijstellen op grond van wat
    er werkelijk gebeurde.

    Bewust alleen waarnemen, niet automatisch aanpassen: een rooster dat
    zichzelf bijstelt op grond van een storing bij de broker, sluit je uit van
    een markt die gewoon open is.
    """

    #: Per uur van de dag (lokale tijd) hoe vaak de broker gesloten meldde.
    closed_by_hour: dict = field(default_factory=dict)
    #: Per uur hoe vaak er überhaupt gekeken is.
    seen_by_hour: dict = field(default_factory=dict)

    def record(self, moment: datetime, broker_says_open: bool) -> None:
        hour = moment.astimezone(MARKET_TZ).hour
        self.seen_by_hour[hour] = self.seen_by_hour.get(hour, 0) + 1
        if not broker_says_open:
            self.closed_by_hour[hour] = self.closed_by_hour.get(hour, 0) + 1

    def as_dict(self) -> dict:
        """Per uur het aandeel waarnemingen waarin de markt dicht was.

        Uren met minder dan twintig waarnemingen blijven weg: daaronder zegt
        een percentage niets.
        """
        uren = {}
        for hour, seen in sorted(self.seen_by_hour.items()):
            if seen < 20:
                continue
            closed = self.closed_by_hour.get(hour, 0)
            uren[hour] = {
                "waarnemingen": seen,
                "dicht": closed,
                "aandeel": round(closed / seen, 3),
            }
        return uren

    def suggest_break(self) -> str | None:
        """Welke uren zijn structureel dicht volgens de waarneming?"""
        data = self.as_dict()
        if not data:
            return None
        dicht = [h for h, v in data.items() if v["aandeel"] > 0.8]
        if not dicht:
            return None
        return (
            "Volgens de waarneming is de markt structureel gesloten in de uren "
            + ", ".join(f"{h:02d}:00" for h in sorted(dicht))
            + " (lokale tijd). Wijkt dat af van het rooster, dan is het "
            "rooster verouderd."
        )
