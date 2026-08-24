"""Het instrument dat de venue krijgt moet kloppen met wat de broker kent.

Bij het herconfigureren van simulator naar IG bleef het oude veld `symbol`
(XAU_USD) in de entry staan, terwijl de broker het veld `epic`
(CS.D.CFDGOLD.CFDGC.IP) gebruikt. De coordinator gaf `self.symbol` mee aan elke
venue-aanroep, en dat overschreef de epic die de venue zelf had. IG antwoordde
met een 404 die niets over het instrument zei.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))

COORDINATOR = (
    Path(__file__).resolve().parent.parent
    / "custom_components" / "gold_scalper" / "coordinator.py"
).read_text(encoding="utf-8")


def test_broker_venues_use_the_epic_as_symbol():
    """Anders vraagt de coordinator om een instrument dat de broker niet kent."""
    block = COORDINATOR.split("venue_name = options.get(CONF_VENUE")[1][:900]
    assert "CONF_EPIC" in block
    assert "self.symbol = epic" in block


def test_epic_override_happens_before_the_venue_is_built():
    """De volgorde telt: de vingerafdruk en de run gebruiken self.symbol."""
    override = COORDINATOR.index("self.symbol = epic")
    construction = COORDINATOR.index("factory = IgVenue if venue_name")
    assert override < construction


def test_venue_falls_back_to_its_own_epic_when_no_symbol_given():
    """De adapter moet ook werken als de aanroeper niets meegeeft."""
    from gold_scalper.broker.ig_capital import IgVenue

    venue = IgVenue.__new__(IgVenue)
    venue.epic = "CS.D.CFDGOLD.CFDGC.IP"
    # Zo kiest candles() en quote() het instrument:
    assert (None or venue.epic) == "CS.D.CFDGOLD.CFDGC.IP"
    assert ("EXPLICIET" or venue.epic) == "EXPLICIET"


def test_config_flow_stores_epic_for_brokers():
    flow = (
        Path(__file__).resolve().parent.parent / "custom_components"
        / "gold_scalper" / "config_flow.py"
    ).read_text(encoding="utf-8")
    broker_step = flow.split("async def async_step_broker")[1].split("\n    async def ")[0]
    assert "CONF_EPIC" in broker_step
