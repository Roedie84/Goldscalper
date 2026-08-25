"""Meldingen naar je telefoon.

De kern is dubbeldetectie. Dezelfde toestand duurt vaak vele cycli; zonder
onderdrukking krijg je elke tien seconden hetzelfde bericht, en dan zet je
meldingen uit - waarna je ook de berichten mist die er wél toe doen.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))
from gold_scalper.notify import REPEAT_AFTER, Notifier, NotifierConfig

T0 = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


class FakeHass:
    def __init__(self):
        self.calls = []
        self.services = self

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append({"domain": domain, "service": service, "data": data})


def _notifier(**over):
    hass = FakeHass()
    config = NotifierConfig(service="mobile_app_test", **over)
    return Notifier(hass, config), hass


def test_no_service_means_no_calls():
    notifier = Notifier(FakeHass(), NotifierConfig(service=None))
    assert not notifier.enabled
    asyncio.run(notifier.alert("x", "T", "M"))


def test_alert_is_sent_once_per_event():
    """Een noodstop duurt tot je hem opheft; elke cyclus melden is onbruikbaar."""
    notifier, hass = _notifier()
    for _ in range(5):
        asyncio.run(notifier.alert("halt", "Noodstop", "dagverlies"))
    assert len(hass.calls) == 1


def test_clearing_allows_a_new_alert():
    notifier, hass = _notifier()
    asyncio.run(notifier.alert("halt", "Noodstop", "eerste"))
    notifier.clear("halt")
    asyncio.run(notifier.alert("halt", "Noodstop", "tweede"))
    assert len(hass.calls) == 2


def test_different_events_are_not_suppressed():
    notifier, hass = _notifier()
    asyncio.run(notifier.alert("halt", "A", "1"))
    asyncio.run(notifier.alert("diverged", "B", "2"))
    assert len(hass.calls) == 2


def test_critical_alerts_carry_the_ios_payload():
    """Een noodstop op een handelsbot mag door een stille stand heen."""
    notifier, hass = _notifier()
    asyncio.run(notifier.alert("halt", "Noodstop", "x", critical=True))
    payload = hass.calls[0]["data"]["data"]
    assert payload["push"]["sound"]["critical"] == 1
    assert payload["priority"] == "high"


def test_critical_can_be_switched_off():
    notifier, hass = _notifier(critical=False)
    asyncio.run(notifier.alert("halt", "Noodstop", "x", critical=True))
    assert "push" not in hass.calls[0]["data"].get("data", {})


def test_alerts_are_tagged_so_they_replace_each_other():
    notifier, hass = _notifier()
    asyncio.run(notifier.alert("halt", "Noodstop", "x"))
    assert hass.calls[0]["data"]["data"]["tag"] == "halt"


# ---------------- uurbericht ----------------

def _data(trades=3, net=4.5, state="wachtend"):
    return {
        "status": (state, "Actief"),
        "stats": {
            "trades": trades, "net_pnl": net, "total_costs": 2.4,
            "win_rate": 60.0,
            "signals": {"evaluations": 300, "acted": trades},
        },
    }


def test_hourly_is_not_due_immediately():
    notifier, _ = _notifier()
    assert notifier.hourly_due(T0) is False


def test_hourly_becomes_due_after_an_hour():
    notifier, _ = _notifier()
    notifier.hourly_due(T0)
    assert notifier.hourly_due(T0 + timedelta(minutes=59)) is False
    assert notifier.hourly_due(T0 + timedelta(hours=1)) is True


def test_hourly_reports_the_delta_not_just_the_total():
    """Het totaal zegt weinig; wat er dít uur gebeurde is de vraag."""
    notifier, hass = _notifier()
    asyncio.run(notifier.send_hourly(_data(trades=3, net=4.5), T0))
    asyncio.run(notifier.send_hourly(
        _data(trades=8, net=1.0), T0 + timedelta(hours=1)
    ))
    message = hass.calls[-1]["data"]["message"]
    assert "5 trades dit uur" in message
    assert "-3.50" in message


def test_quiet_hour_is_skipped():
    """Een uurbericht dat steeds 'nul trades' zegt, leer je negeren."""
    notifier, hass = _notifier()
    asyncio.run(notifier.send_hourly(_data(trades=0, net=0.0), T0))
    sent = asyncio.run(notifier.send_hourly(
        _data(trades=0, net=0.0), T0 + timedelta(hours=1)
    ))
    assert sent is False
    assert hass.calls == []


def test_quiet_skip_can_be_switched_off():
    notifier, hass = _notifier(skip_quiet_hours=False)
    asyncio.run(notifier.send_hourly(_data(trades=0, net=0.0), T0))
    assert len(hass.calls) == 1


def test_halted_hour_is_never_quiet():
    """Stilliggen door een noodstop is juist wél nieuws."""
    notifier, hass = _notifier()
    sent = asyncio.run(notifier.send_hourly(
        _data(trades=0, net=0.0, state="noodstop"), T0
    ))
    assert sent is True


def test_hourly_is_not_critical():
    notifier, hass = _notifier()
    asyncio.run(notifier.send_hourly(_data(), T0))
    assert "push" not in hass.calls[0]["data"].get("data", {})


def test_a_failing_notify_service_does_not_break_the_loop():
    """Een melding mag nooit de handelslus slopen."""
    class Broken(FakeHass):
        async def async_call(self, *a, **kw):
            raise RuntimeError("notify bestaat niet")

    notifier = Notifier(Broken(), NotifierConfig(service="mobile_app_weg"))
    asyncio.run(notifier.alert("halt", "T", "M"))   # mag niet opgooien
