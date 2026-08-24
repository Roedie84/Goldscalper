"""IG en Capital.com tegen nagebootste antwoorden.

Geen van beide is tegen een echte verbinding getest; deze tests dekken de
parsing en de foutafhandeling, niet of de broker doet wat zijn documentatie
belooft.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.broker.adapter import TradingDisabledError, VenueError
from gold_scalper.broker.ig_capital import CapitalVenue, IgVenue


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self._payload, self.status = payload, status
        self.headers = headers or {}
    async def json(self, content_type=None): return self._payload
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class FakeSession:
    """Reageert per pad; onthoudt alle verzoeken."""

    def __init__(self, routes, login_headers=None):
        self.routes = routes
        # Expliciet op None toetsen: met `or` valt een leeg dict terug op de
        # standaard, en dan test je het ontbreken van headers niet. Precies
        # dezelfde valkuil als `unit_of_measurement or None` eerder.
        self.login_headers = (
            {"CST": "cst-token", "X-SECURITY-TOKEN": "sec-token"}
            if login_headers is None else login_headers
        )
        self.calls = []

    def _match(self, url):
        for key, value in self.routes.items():
            if key in url:
                return value
        return ({}, 404)

    def post(self, url, **kw):
        self.calls.append({"method": "POST", "url": url, **kw})
        if "/session" in url:
            return FakeResponse({"currentAccountId": "ACC1"}, 200, self.login_headers)
        payload, status = self._match(url)
        return FakeResponse(payload, status)

    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        payload, status = self._match(url)
        return FakeResponse(payload, status)


MARKET = ({"snapshot": {"bid": 3300.1, "offer": 3300.4,
                        "marketStatus": "TRADEABLE"}}, 200)
PRICES = ({"prices": [
    {"snapshotTimeUTC": f"2026-08-24T10:{m:02d}:00",
     "openPrice": {"bid": 3300.0, "ask": 3300.3},
     "highPrice": {"bid": 3300.5, "ask": 3300.8},
     "lowPrice": {"bid": 3299.5, "ask": 3299.8},
     "closePrice": {"bid": 3300.2, "ask": 3300.5},
     "lastTradedVolume": 100}
    for m in range(5)]}, 200)


def ig(routes, trading=True):
    return IgVenue(FakeSession(routes), "key", "user", "pass",
                   environment="demo", epic="GOLD", trading_enabled=trading)


def capital(routes, trading=True):
    return CapitalVenue(FakeSession(routes), "key", "user", "pass",
                        environment="demo", epic="GOLD", trading_enabled=trading)


# ---------------- sessie ----------------

def test_login_stores_both_tokens():
    venue = ig({"/markets/": MARKET})
    asyncio.run(venue.quote())
    assert venue._cst == "cst-token"
    assert venue._token == "sec-token"


def test_missing_tokens_is_explained():
    venue = IgVenue(FakeSession({}, login_headers={}), "k", "u", "p")
    with pytest.raises(VenueError, match="CST"):
        asyncio.run(venue.quote())


def test_expired_session_triggers_relogin():
    """Capital.com laat sessies verlopen na tien minuten inactiviteit."""
    calls = {"n": 0}

    class Flaky(FakeSession):
        def request(self, method, url, **kw):
            self.calls.append({"method": method, "url": url})
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResponse({"errorCode": "error.security.session"}, 401)
            return FakeResponse(MARKET[0], 200)

    venue = CapitalVenue(Flaky({}), "k", "u", "p")
    quote = asyncio.run(venue.quote())
    assert quote.bid == pytest.approx(3300.1)


# ---------------- marktdata ----------------

def test_quote_uses_bid_and_offer():
    q = asyncio.run(ig({"/markets/": MARKET}).quote())
    assert q.bid == pytest.approx(3300.1)
    assert q.ask == pytest.approx(3300.4)
    assert q.spread == pytest.approx(0.3)


def test_closed_market_is_flagged():
    payload = ({"snapshot": {"bid": 3300.0, "offer": 3300.3,
                             "marketStatus": "CLOSED"}}, 200)
    assert asyncio.run(ig({"/markets/": payload}).quote()).tradeable is False


def test_candles_use_mid_not_bid():
    """Op bid rekenen zou elke indicator een halve spread laten schuiven."""
    c = asyncio.run(ig({"/prices/": PRICES}).candles("GOLD", "1m", 100))
    assert len(c) == 5
    assert c.close[0] == pytest.approx((3300.2 + 3300.5) / 2)
    c.validate()


def test_unknown_timeframe_lists_the_options():
    with pytest.raises(VenueError, match="1m"):
        asyncio.run(ig({}).candles("GOLD", "3m", 10))


def test_empty_price_list_mentions_trading_hours():
    with pytest.raises(VenueError, match="handelsuren"):
        asyncio.run(ig({"/prices/": ({"prices": []}, 200)}).candles("GOLD", "1m", 10))


# ---------------- handelen ----------------

def test_trading_disabled_blocks_orders():
    with pytest.raises(TradingDisabledError):
        asyncio.run(ig({"/markets/": MARKET}, trading=False)
                    .place_order("GOLD", "buy", 1.0))


def test_units_cap_is_enforced():
    venue = ig({"/markets/": MARKET})
    venue.max_units = 2.0
    with pytest.raises(VenueError, match="bereik"):
        asyncio.run(venue.place_order("GOLD", "buy", 50.0))


def test_ig_confirms_in_two_steps():
    """IG geeft eerst een referentie; het dealId komt uit /confirms."""
    routes = {
        "/markets/": MARKET,
        "/positions/otc": ({"dealReference": "REF123"}, 200),
        "/confirms/": ({"dealStatus": "ACCEPTED", "dealId": "DEAL9",
                        "level": 3300.45, "size": 1.0}, 200),
    }
    r = asyncio.run(ig(routes).place_order("GOLD", "buy", 1.0, stop_loss=3299.0))
    assert r.success and r.ticket == "DEAL9"
    assert r.fill_price == pytest.approx(3300.45)
    assert r.slippage == pytest.approx(0.05, abs=1e-9)


def test_ig_rejected_order_reports_the_reason():
    routes = {
        "/markets/": MARKET,
        "/positions/otc": ({"dealReference": "REF1"}, 200),
        "/confirms/": ({"dealStatus": "REJECTED", "reason": "MARKET_CLOSED"}, 200),
    }
    r = asyncio.run(ig(routes).place_order("GOLD", "buy", 1.0))
    assert not r.success and "MARKET_CLOSED" in r.error


def test_ig_missing_confirmation_warns_it_may_still_be_filled():
    """Het gevaarlijke geval: geen bevestiging, order mogelijk wél uitgevoerd."""
    routes = {"/markets/": MARKET, "/positions/otc": ({"dealReference": "REF1"}, 200)}
    r = asyncio.run(ig(routes).place_order("GOLD", "buy", 1.0))
    assert not r.success
    assert "alsnog uitgevoerd" in r.error


def test_capital_confirms_directly():
    routes = {"/markets/": MARKET, "/positions": ({"dealReference": "D1"}, 200)}
    r = asyncio.run(capital(routes).place_order("GOLD", "buy", 1.0, stop_loss=3299.0))
    assert r.success and r.ticket == "D1"


def test_stop_and_target_are_sent():
    routes = {"/markets/": MARKET, "/positions": ({"dealReference": "D1"}, 200)}
    venue = capital(routes)
    asyncio.run(venue.place_order("GOLD", "buy", 1.0, stop_loss=3299.0,
                                  take_profit=3302.0))
    body = venue._session.calls[-1]["json"]
    assert body["stopLevel"] == 3299.0
    assert body["profitLevel"] == 3302.0


def test_ig_sends_our_own_deal_reference():
    """Hierop rust de bescherming tegen dubbele orders."""
    routes = {"/markets/": MARKET, "/positions/otc": ({"dealReference": "R"}, 200),
              "/confirms/": ({"dealStatus": "ACCEPTED", "dealId": "D"}, 200)}
    venue = ig(routes)
    asyncio.run(venue.place_order("GOLD", "buy", 1.0, comment="gold_scalper-abc123"))
    body = next(c for c in venue._session.calls if "positions/otc" in c["url"])["json"]
    assert body["dealReference"].startswith("gold_scalper-abc123")


# ---------------- posities ----------------

def test_positions_map_direction_and_stop():
    payload = ({"positions": [{
        "position": {"dealId": "D1", "direction": "SELL", "size": 2.0,
                     "level": 3300.0, "stopLevel": 3305.0, "upl": -1.5,
                     "dealReference": "gold_scalper-x"},
        "market": {"epic": "GOLD", "bid": 3301.0},
    }]}, 200)
    positions = asyncio.run(ig({"/positions": payload}).positions("GOLD"))
    assert positions[0].side == "sell"
    assert positions[0].stop_loss == 3305.0
    assert positions[0].comment == "gold_scalper-x"


def test_venues_report_real_spread():
    """In tegenstelling tot publieke bronnen meten deze de echte spread."""
    assert ig({}).has_real_spread is True
    assert capital({}).has_real_spread is True


# ---------------- diagnose van inlogfouten ----------------

def test_email_as_identifier_is_caught_before_sending():
    """IG antwoordt met 'validation.pattern.invalid...identifier', wat je niets
    vertelt. Beter vooraf afvangen met een bruikbare uitleg."""
    venue = IgVenue(FakeSession({}), "key", "ruud@example.nl", "pass")
    with pytest.raises(VenueError, match="gebruikersnaam"):
        asyncio.run(venue.quote())


def test_empty_identifier_is_caught():
    venue = IgVenue(FakeSession({}), "key", "   ", "pass")
    with pytest.raises(VenueError, match="leeg"):
        asyncio.run(venue.quote())


def test_empty_api_key_is_caught():
    venue = IgVenue(FakeSession({}), "  ", "gebruiker", "pass")
    with pytest.raises(VenueError, match="leeg"):
        asyncio.run(venue.quote())


@pytest.mark.parametrize("invisible", ["\u200b", "\u00a0", "\ufeff", "\u2060"])
def test_invisible_characters_are_stripped(invisible):
    """Een niet-afbrekende ruimte is met het oog niet te zien maar laat elke
    patroonvalidatie falen; dan zoek je in de verkeerde richting."""
    venue = IgVenue(FakeSession({}), f"key{invisible}",
                    f"{invisible}gebruiker{invisible}", "pass")
    assert venue._identifier == "gebruiker"
    assert venue._api_key == "key"


def test_password_keeps_its_spaces():
    """Wachtwoorden mogen spaties bevatten; die mogen niet weggetrimd worden."""
    venue = IgVenue(FakeSession({}), "key", "gebruiker", " wacht woord ")
    assert venue._password == " wacht woord "


def test_error_codes_get_a_readable_explanation():
    from gold_scalper.broker.ig_capital import IgStyleVenue
    message = IgStyleVenue._describe_error(
        400, {"errorCode": "validation.pattern.invalid.authenticationRequest.identifier"}
    )
    assert "e-mailadres" in message


def test_wrong_environment_key_is_explained():
    from gold_scalper.broker.ig_capital import IgStyleVenue
    message = IgStyleVenue._describe_error(
        403, {"errorCode": "error.security.api-key-invalid"}
    )
    assert "omgeving" in message


def test_unknown_error_code_still_returns_something_useful():
    from gold_scalper.broker.ig_capital import IgStyleVenue
    message = IgStyleVenue._describe_error(500, {"errorCode": "iets.nieuws"})
    assert "500" in message and "iets.nieuws" in message


def test_page_size_disables_igs_default_pagination():
    """Zonder pageSize pagineert IG met een standaard van 20, ongeacht max.
    De analyse heeft er minstens 60 nodig, dus kwam hij nooit op gang."""
    venue = ig({"/prices/": PRICES})
    asyncio.run(venue.candles("GOLD", "1m", 400))
    params = next(c for c in venue._session.calls if "prices" in c["url"])["params"]
    assert params["pageSize"] == 0
    assert params["max"] == 400


def test_historical_data_quota_is_explained():
    """IG rekent per opgehaald datapunt; op demo is dat quotum krap."""
    from gold_scalper.broker.ig_capital import IgStyleVenue
    message = IgStyleVenue._describe_error(
        403, {"errorCode": "error.public-api.exceeded-account-historical-data-allowance"}
    )
    assert "quotum" in message and "tijdsframe" in message


def test_login_failure_keeps_the_brokers_error_code():
    """Een eigen samenvatting die de foutcode weggooit, laat je raden welk
    van vijf dingen er mis is."""
    session = FakeSession({})

    class Denied(FakeSession):
        def post(self, url, **kw):
            self.calls.append({"method": "POST", "url": url})
            return FakeResponse({"errorCode": "error.security.account-locked"}, 403)

    venue = IgVenue(Denied({}), "key", "gebruiker", "pass")
    with pytest.raises(VenueError) as excinfo:
        asyncio.run(venue.quote())
    message = str(excinfo.value)
    assert "error.security.account-locked" in message
    assert "vergrendeld" in message


def test_locked_account_suggests_waiting():
    from gold_scalper.broker.ig_capital import IgStyleVenue
    message = IgStyleVenue._describe_error(
        403, {"errorCode": "error.security.too-many-failed-attempts"}
    )
    assert "kwartier" in message


def test_wrong_environment_endpoint_is_explained():
    from gold_scalper.broker.ig_capital import IgStyleVenue
    message = IgStyleVenue._describe_error(
        403, {"errorCode": "endpoint.unavailable.for.api-key"}
    )
    assert "omgeving" in message


# ---------------- gesloten markt en epic zoeken ----------------

CLOSED = ({"snapshot": {"marketStatus": "CLOSED", "bid": None, "offer": None}}, 200)


def test_closed_market_without_prices_is_not_an_error():
    """Goud sluit dagelijks kort en het hele weekend. Daar een fout op gooien
    laat de integratie 's avonds falen en vereist handmatig herstel."""
    venue = ig({"/markets/": MARKET})
    asyncio.run(venue.quote())          # eerst een geldige koers zien
    venue._session.routes = {"/markets/": CLOSED}
    q = asyncio.run(venue.quote())
    assert q.tradeable is False
    assert q.mid == pytest.approx(3300.25)   # laatst bekende koers


def test_closed_market_without_any_history_explains_itself():
    venue = ig({"/markets/": CLOSED})
    with pytest.raises(VenueError, match="gesloten"):
        asyncio.run(venue.quote())


def test_open_market_without_prices_points_at_the_epic():
    """Markt open maar geen quote: dan is de epic vrijwel zeker fout."""
    payload = ({"snapshot": {"marketStatus": "TRADEABLE"}}, 200)
    with pytest.raises(VenueError, match="epic"):
        asyncio.run(ig({"/markets/": payload}).quote())


@pytest.mark.parametrize("status", ["CLOSED", "OFFLINE", "SUSPENDED", "EDITS_ONLY"])
def test_all_closed_statuses_are_recognised(status):
    venue = ig({"/markets/": MARKET})
    asyncio.run(venue.quote())
    venue._session.routes = {
        "/markets/": ({"snapshot": {"marketStatus": status}}, 200)
    }
    assert asyncio.run(venue.quote()).tradeable is False


def test_search_markets_returns_epics():
    """Epics zijn niet te raden en verschillen per account."""
    payload = ({"markets": [
        {"epic": "CS.D.CFDGOLD.CFDGC.IP", "instrumentName": "Spot Gold",
         "instrumentType": "COMMODITIES", "marketStatus": "TRADEABLE",
         "bid": 3300.1, "offer": 3300.4},
        {"epic": "CS.D.CFEGOLD.CFE.IP", "instrumentName": "Gold Futures",
         "marketStatus": "CLOSED"},
    ]}, 200)
    found = asyncio.run(ig({"/markets": payload}).search_markets("gold"))
    assert [m["epic"] for m in found] == [
        "CS.D.CFDGOLD.CFDGC.IP", "CS.D.CFEGOLD.CFE.IP"
    ]
    assert found[0]["name"] == "Spot Gold"
