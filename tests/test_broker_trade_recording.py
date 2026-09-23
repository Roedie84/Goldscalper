"""Orders die naar de broker gaan moeten in de database komen.

In papermodus schrijft de paper-broker elke trade weg. Bij demo en live ging de
order naar de broker en verdween daarna uit de eigen administratie: het
overzicht toonde "2 signalen uitgevoerd" naast "0 trades", er was geen
resultaat, geen kostenmeting, geen verliesanalyse, en de bewijsfase vorderde
nooit.

Dat maakte de demomodus zinloos, want juist het meten was het doel.
"""
import ast
import os
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "gold_scalper"
sys.path.insert(0, str(PKG.parent))

SOURCE = (PKG / "coordinator.py").read_text(encoding="utf-8")


def _method(name: str) -> str:
    """Haal de body van één methode op, via de parser.

    Met tekstmarkeringen knippen gaat twee keer mis: `_open_position` is een
    prefix van `_open_positions`, en een methode eindigt niet op een
    voorspelbare regel. De AST kent de werkelijke grenzen.
    """
    tree = ast.parse(SOURCE)
    lines = SOURCE.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            if node.name == name:
                return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"{name} bestaat niet")


def test_orders_are_written_to_the_database():
    body = _method("_open_position")
    assert "_record_broker_open" in body, (
        "een order naar de broker wordt niet vastgelegd"
    )


def test_closing_writes_the_result():
    body = _method("_close_position")
    assert "_record_broker_close" in body


def test_broker_initiated_closes_are_settled():
    """Een stop die de broker zelf uitvoert, verdwijnt zonder dat wij iets
    merken. Zonder afstemming blijft de rij eeuwig open staan."""
    assert "_settle_vanished_positions" in SOURCE
    update = SOURCE.split("async def _async_update_data")[1][:2500]
    assert "_settle_vanished_positions" in update


def test_slippage_is_measured_not_assumed():
    """Het hele punt van demo-modus: de fillprijs vergelijken met wat je
    verwachtte, in plaats van een model te vertrouwen."""
    body = _method("_record_broker_open")
    assert "open_slippage" in body
    assert "result.fill_price" in body


def test_gross_uses_mids_and_net_uses_fills():
    """Bruto is de beweging die de strategie ving; netto is wat er na spread en
    slippage overblijft. Beide op dezelfde prijzen berekenen zou de kosten
    onzichtbaar maken."""
    body = _method("_record_broker_close")
    assert "open_mid" in body and "quote.mid" in body
    assert "trade.open_price" in body and "exit_price" in body
    assert "total_cost" in body


def test_the_ticket_links_database_to_broker():
    body = _method("_record_broker_open")
    assert "broker_ticket" in body


def test_risk_manager_learns_about_the_result():
    """Anders tellen demo-verliezen niet mee voor de daglimiet."""
    body = _method("_record_broker_close")
    assert "record_close" in body


def test_no_dict_to_class_trick_for_the_ticket():
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "type" and len(node.args) == 3):
            pytest.fail(f"dict-naar-klasse-truc op regel {node.lineno}")


def test_close_reason_is_inferred_honestly():
    """De broker vertelt niet waarom hij sloot; dat afleiden mag, maar het
    resultaat hoort als afleiding gemarkeerd te zijn."""
    body = _method("_settle_vanished_positions")
    assert "broker_gesloten" in body
    assert "stop_loss" in body and "take_profit" in body


def test_excursions_are_tracked_for_broker_positions():
    """De verliesanalyse filtert op mfe; zonder die waarde slaat hij elke
    demotrade over en meldt '0 verliezende trades' naast een performance die er
    wél telt - dood in precies de modus die ertoe doet."""
    assert "_track_excursion" in SOURCE
    body = _method("_record_broker_close")
    assert "trade.mfe" in body and "trade.mae" in body


def test_tracking_happens_before_the_noop_check():
    """Bij 'hold' gebeurt er verder niets, en dat is het grootste deel van de
    tijd. Achteraf zijn de uitersten niet meer te achterhalen."""
    body = _method("_manage_open_positions")
    track = body.index("_track_excursion")
    noop = body.index("if action.is_noop")
    assert track < noop


def test_excursion_uses_the_exit_price_not_the_mid():
    """De beweging die je werkelijk had kunnen realiseren, niet de theoretische."""
    body = _method("_track_excursion")
    assert "quote.bid" in body and "quote.ask" in body


def test_the_post_mortem_can_classify_a_broker_trade():
    """Eind-tot-eind: een trade met uitersten moet een oorzaak krijgen."""
    from gold_scalper.learning.postmortem import MIN_PATTERN, analyse_losses
    from gold_scalper.storage.database import Trade

    losers = [
        Trade(
            run_id=1, mode="demo", symbol="GOLD", side="buy", volume=0.1,
            open_time=f"2026-08-25T10:{i % 60:02d}:00+00:00",
            open_price=4665.0, open_mid=4665.0, open_spread=0.6,
            close_time=f"2026-08-25T10:{i % 60:02d}:30+00:00",
            net_pnl=-4.0, gross_pnl=-3.4, total_cost=0.6,
            mfe=6.5, mae=-4.2, close_reason="stop_loss",
        )
        for i in range(MIN_PATTERN + 5)
    ]
    result = analyse_losses(losers, typical_atr=4.0)
    assert result.losses == len(losers)
    assert result.patterns, "geen enkel patroon herkend"


def test_vanished_positions_settle_at_the_level_not_the_discovery_price():
    """De lus draait elke twintig seconden; in die tijd zakt de koers verder
    door. Op de latere koers afrekenen boekt dat extra stuk als "kosten",
    waardoor de kostprijs per trade opliep tot ruim het dubbele van de spread -
    een meetfout die eruitziet als slippage.
    """
    body = _method("_settle_vanished_positions")
    assert "settle = quote" in body, "er wordt niet op het niveau afgerekend"
    assert "VenueQuote(" in body
    assert "trade.stop_loss" in body and "trade.take_profit" in body


def test_the_settle_quote_keeps_the_spread():
    """Het niveau is de mid noch de fill; de spread hoort er nog omheen,
    anders verdwijnt de werkelijke kostprijs uit de berekening."""
    body = _method("_settle_vanished_positions")
    assert "quote.spread" in body


def test_unclassified_closes_use_the_current_quote():
    """Kan de reden niet vastgesteld worden, dan is de actuele koers het beste
    dat er is - en dan hoort er geen niveau verzonnen te worden."""
    body = _method("_settle_vanished_positions")
    assert 'reason = "broker_gesloten"' in body
    assert "level: float | None = None" in body


def test_excursion_is_seeded_at_open():
    """Een positie die tussen twee cycli opent en sluit, of die de broker sluit
    voordat de beheerlus hem ziet, werd nooit gemeten. Dan blijft mfe leeg en
    slaat de verliesanalyse die trade over.

    Bij 33 trades met 18 verliezers meldde de analyse er nul, omdat zeventien
    ervan nooit door de lus waren gezien.
    """
    body = _method("_record_broker_open")
    assert "_excursions.setdefault" in body


def test_missing_excursion_falls_back_to_the_outcome():
    """Minder nauwkeurig dan een gemeten uiterste, maar veel beter dan de trade
    helemaal buiten de analyse laten."""
    body = _method("_record_broker_close")
    assert "if excursion is None:" in body
    assert "beweging" in body


def test_mfe_is_always_written():
    """Er mag geen pad zijn waarlangs mfe leeg blijft."""
    body = _method("_record_broker_close")
    # Buiten een if-blok, dus altijd uitgevoerd.
    assert "\n        trade.mfe = round(" in body
    assert "\n        trade.mae = round(" in body


def test_settlement_asks_the_broker_for_the_exit_price():
    """De ernstigste fout tot nu toe: afrekenen op de ontdekkingskoers in
    plaats van op de werkelijke uitstapprijs.

    Vijf trades op één middag: de broker boekte +28,58 euro, de eigen
    administratie -4,83. De instapprijzen klopten; de uitstapprijzen lagen
    binnen twee dollar van de instap terwijl er tien tot elf vanaf werd
    gesloten.
    """
    body = _method("_settle_vanished_positions")
    assert "closed_deal" in body, "de werkelijke uitstapprijs wordt niet opgevraagd"
    assert 'werkelijk.get("exit_price")' in body


def test_an_estimated_settlement_is_labelled():
    """Kan de prijs niet worden opgehaald, dan blijft de schatting - maar dan
    wel herkenbaar, zodat je later weet welke cijfers hard zijn."""
    body = _method("_settle_vanished_positions")
    assert "broker_gesloten_geschat" in body
    assert "broker_gesloten_gemeten" in body


def test_the_rate_is_derived_from_a_settled_trade():
    """De winst in accountvaluta die de broker meldt, geeft de wisselkoers -
    preciezer dan de afleiding uit een open positie, want dit is het bedrag
    waarmee hij werkelijk heeft afgerekend."""
    body = _method("_settle_vanished_positions")
    assert "profit_account" in body
    assert "self.conversion.rate = koers" in body


def test_an_estimated_settlement_is_reported_loudly():
    """Een schatting die stil doorgaat, produceert cijfers die eruitzien als
    metingen - precies hoe een fout van 28 euro per middag onopgemerkt bleef.
    """
    body = _method("_settle_vanished_positions")
    assert "_geschatte_afwikkelingen" in body
    assert "onbetrouwbaar" in body


def test_the_estimate_count_is_visible():
    """Zonder dit getal weet je niet welk deel van je resultaat op schattingen
    rust."""
    from pathlib import Path

    pkg = Path(__file__).resolve().parent.parent / "custom_components" / "gold_scalper"
    assert "estimated_settlements" in (pkg / "diagnostics.py").read_text(encoding="utf-8")


def test_the_entry_price_is_passed_to_the_lookup():
    """Het ticketnummer komt niet overeen met de verwijzing in het
    transactieoverzicht; de instapprijs wel."""
    body = _method("_settle_vanished_positions")
    assert "trade.open_price" in body.split("zoek(")[1][:120]


# ---------------- correctie van schattingen ----------------

def test_estimated_settlements_are_corrected_later():
    """Het transactieoverzicht van de broker loopt uren achter: de nieuwste
    transactie was van 06:07 terwijl er om 10:30 werd opgevraagd.

    Op het moment dat de lus een positie afwikkelt staat de werkelijke
    uitstapprijs er dus nog niet in, en valt de afwikkeling terug op een
    schatting die tien dollar mis kan zijn. Eén poging is niet genoeg.
    """
    body = _method("_correct_estimated_settlements")
    assert "estimated_trades" in body
    assert "broker_gesloten_gecorrigeerd" in body
    assert "update_trade" in body


def test_the_correction_runs_periodically():
    """Elke cyclus zou de broker onnodig belasten; nooit zou de schattingen
    laten staan."""
    body = _method("_async_update_data")
    assert "_correct_estimated_settlements" in body
    assert "_correctie_teller" in body


def test_the_correction_is_batched():
    """Vijftig trades in één cyclus corrigeren zou de lus laten vastlopen op
    netwerkverzoeken."""
    body = _method("_correct_estimated_settlements")
    assert "geschat[:5]" in body


def test_estimated_trades_are_findable(tmp_path):
    from gold_scalper.storage.database import Trade, TradeDatabase

    db = TradeDatabase(tmp_path / "e.db")
    db.connect()
    run = db.start_run("demo", "v1", "GOLD", {}, 10000.0, None, "fp")

    for reden in ("broker_gesloten_geschat", "broker_gesloten_gemeten",
                  "stop_loss"):
        db.insert_trade(Trade(
            run_id=run, mode="demo", symbol="GOLD", side="sell", volume=0.012,
            open_time="2026-09-11T10:00:00+00:00", open_price=4349.40,
            open_mid=4349.10, open_spread=0.6,
            close_time="2026-09-11T10:05:00+00:00", net_pnl=-0.05,
            close_reason=reden, broker_ticket=f"T-{reden}",
        ))

    geschat = db.estimated_trades(run)
    assert len(geschat) == 1
    assert geschat[0].close_reason == "broker_gesloten_geschat"


def test_the_estimate_count_comes_from_the_database():
    """Een losse teller begint bij elke herstart op nul en wordt alleen
    verhoogd bij nieuwe schattingen. Het rapport meldde daardoor nul te
    corrigeren trades terwijl er nog één stond.

    Een getal dat verkeerd kan staan is erger dan geen getal, want je
    vertrouwt erop.
    """
    body = _method("_correct_estimated_settlements")
    assert "self._geschatte_afwikkelingen = len(geschat)" in body


def test_the_correction_also_derives_the_exchange_rate():
    """De correctie heeft de winst in accountvaluta al in handen. Die niet
    gebruiken zou betekenen dat de koers onbekend blijft terwijl hij op tafel
    ligt - en dan blijft de positiegrootte acht procent naast de bedoeling."""
    body = _method("_correct_estimated_settlements")
    assert "profit_account" in body
    assert "self.conversion.rate = koers" in body


def test_the_rate_is_derived_from_the_correction():
    """De afleiding uit een open positie lukte nooit: posities sluiten te snel
    om genoeg beweging te tonen.

    Bij een correctie is het bedrag waarmee de broker werkelijk heeft
    afgerekend wél bekend, en dat is preciezer dan elke schatting.
    """
    body = _method("_correct_estimated_settlements")
    assert "profit_account" in body
    assert "self.conversion.rate = koers" in body


def test_the_correction_runs_soon_after_startup():
    """Dertig cycli is tien minuten, en dat is te lang om twee redenen: bij een
    herstart staan er vaak al schattingen uit de vorige sessie, en zolang de
    correctie niet heeft gedraaid blijft ook de wisselkoers onbekend - die komt
    uit dezelfde lus."""
    body = _method("_async_update_data")
    assert "_correctie_teller == 1" in body, "de eerste correctie wacht te lang"


def test_the_estimate_count_is_updated_while_learning():
    """Anders staat het rapport op nul tot de correctielus voor het eerst
    draait, en dan lijkt er niets te corrigeren terwijl er trades op een
    schatting staan."""
    body = _method("_relearn")
    assert "estimated_trades" in body


def test_exits_can_be_rechecked(tmp_path):
    """Trades die verkeerd zijn gekoppeld staan als gecorrigeerd in de
    database terwijl hun uitstapprijs van een andere trade komt. Zonder een
    manier om ze opnieuw te laten opzoeken, blijft die fout erin zitten.
    """
    from gold_scalper.storage.database import Trade, TradeDatabase

    db = TradeDatabase(tmp_path / "r.db")
    db.connect()
    run = db.start_run("demo", "v1", "GOLD", {}, 10000.0, None, "fp")

    for reden in ("broker_gesloten_gecorrigeerd", "broker_gesloten_gemeten",
                  "stop_loss", "take_profit"):
        db.insert_trade(Trade(
            run_id=run, mode="demo", symbol="GOLD", side="sell", volume=0.017,
            open_time="2026-09-15T09:53:00+00:00", open_price=4287.29,
            open_mid=4287.0, open_spread=0.6,
            close_time="2026-09-15T10:08:00+00:00", net_pnl=-0.14,
            close_reason=reden, broker_ticket=f"T-{reden}",
        ))

    # Alleen de broker-afgewikkelde trades opnieuw; een stop of doel is op het
    # niveau afgerekend en daar valt niets te herzien.
    aantal = db.mark_for_recheck(run)
    assert aantal == 2
    assert len(db.estimated_trades(run)) == 2


def test_an_unfindable_exit_keeps_its_price():
    """Eeuwig blijven proberen kost elke ronde een netwerkverzoek en houdt de
    trade als schatting in het rapport, ook wanneer de prijs niet meer te
    achterhalen is.

    Een eigen label maakt het verschil zichtbaar tussen "nog niet geprobeerd"
    en "niet te vinden".
    """
    body = _method("_correct_estimated_settlements")
    assert "broker_gesloten_onvindbaar" in body


def test_giving_up_is_based_on_age_not_attempts():
    """De vorige regel gaf op na drie pogingen, oftewel ruim tien minuten. Maar
    het transactieoverzicht van de broker loopt uren achter - gemeten: nieuwste
    transactie 10:51 bij een opvraging om 14:22.

    Elke nieuwe trade werd dus drie keer tevergeefs gezocht en daarna
    definitief opgegeven, uren voordat de prijs beschikbaar kwam.
    Zesentwintig van zestig trades hielden daardoor hun geschatte prijs, en die
    comprimeert naar nul: de gemiddelde winst zakte van 14,51 naar 10,61 - een
    meetfout die eruitzag als een verslechterende strategie.
    """
    body = _method("_correct_estimated_settlements")
    assert "leeftijd > 2.0" in body, "er wordt niet op leeftijd opgegeven"

    # Alleen naar code kijken, niet naar commentaar: de vorige regel staat
    # daar bewust in als toelichting.
    code = "\n".join(
        regel for regel in body.splitlines()
        if not regel.strip().startswith("#")
    )
    assert "_herzoek_pogingen" not in code, "de pogingenteller staat er nog"


def test_the_close_time_is_passed_to_the_lookup():
    """Zonder het sluitmoment ligt het zoekvenster rond nu, en dan is een trade
    van gisteren onvindbaar."""
    body = _method("_correct_estimated_settlements")
    assert "_as_datetime(trade.close_time" in body


def test_unfindable_trades_can_be_rechecked_again(tmp_path):
    """Anders blijven ze onvindbaar staan, ook nadat een fout in de
    zoekopdracht is gerepareerd."""
    from gold_scalper.storage.database import Trade, TradeDatabase

    db = TradeDatabase(tmp_path / "u.db")
    db.connect()
    run = db.start_run("demo", "v1", "GOLD", {}, 10000.0, None, "fp")
    db.insert_trade(Trade(
        run_id=run, mode="demo", symbol="GOLD", side="sell", volume=0.017,
        open_time="2026-09-15T09:53:00+00:00", open_price=4287.29,
        open_mid=4287.0, open_spread=0.6,
        close_time="2026-09-15T10:08:00+00:00", net_pnl=-0.14,
        close_reason="broker_gesloten_onvindbaar", broker_ticket="T1",
    ))
    assert db.mark_for_recheck(run) == 1


def test_the_brokers_own_amount_is_used():
    """Zelf narekenen uit prijzen leverde steeds weer afwijkingen op: bij één
    trade 7,46 tegen de 10,48 die de broker boekte.

    Elke keer was de oorzaak een detail dat niet te controleren viel -
    afronding, een halve spread, een gedeeltelijke sluiting. Het bedrag van de
    broker is per definitie juist: dat is wat er op de rekening gebeurde.
    """
    body = _method("_correct_estimated_settlements")
    assert "winst_account / koers" in body, (
        "het resultaat wordt nog zelf berekend in plaats van overgenomen"
    )


def test_a_mismatch_between_price_and_amount_is_reported():
    """Rijmen de prijs en het bedrag van de broker niet, dan is er iets aan de
    hand dat de code niet kent. Die afwijking hoort zichtbaar te zijn en niet
    weggerekend."""
    body = _method("_correct_estimated_settlements")
    assert "verschillen" in body
    assert "gedeeltelijke sluiting" in body


def test_there_is_a_fallback_without_a_rate():
    """Zonder wisselkoers is het bedrag van de broker niet om te rekenen; dan
    moet de berekening uit prijzen overblijven."""
    body = _method("_correct_estimated_settlements")
    assert "else:" in body
    assert "(exit_price - trade.open_price)" in body


def test_the_brokers_close_time_is_adopted():
    """Het eigen tijdstempel is het moment waarop de beheerlus de positie
    afwikkelde, en dat liep tot negentig minuten uit de pas met wat de broker
    meldt.

    Naast het overzicht van de broker was het rapport daardoor niet te lezen:
    je vergelijkt rijen op tijdstip en koppelt dan de verkeerde trades aan
    elkaar. De broker bepaalt wanneer een positie sloot, dus zijn tijdstip is
    het juiste.
    """
    body = _method("_correct_estimated_settlements")
    assert 'werkelijk.get("closed_at")' in body
    assert "trade.close_time = moment.isoformat()" in body


def test_a_date_without_a_time_is_ignored():
    """Het veld `date` bevat alleen de dag; dat overnemen zou het sluitmoment
    op middernacht zetten."""
    body = _method("_correct_estimated_settlements")
    assert "moment.hour or moment.minute or moment.second" in body


def test_the_duration_follows_the_close_time():
    """Anders staat er een looptijd die niet bij de tijdstempels past."""
    body = _method("_correct_estimated_settlements")
    assert "trade.duration_seconds" in body
