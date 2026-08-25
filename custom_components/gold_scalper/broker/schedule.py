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
from dataclasses import dataclass
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

    return False, (
        f"De broker meldt de markt gesloten terwijl het rooster hem open zegt "
        f"({reason}). Waarschijnlijk een feestdag of vervroegde sluiting; die "
        "staan niet in het rooster."
    )
