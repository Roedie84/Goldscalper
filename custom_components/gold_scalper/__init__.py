"""Gold Scalper: XAU/USD-analyse en handel, volledig binnen Home Assistant."""

from __future__ import annotations

import functools
import logging
from datetime import datetime

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    SERVICE_INDICATOR_LAB,
    SERVICE_RECHECK_EXITS,
    SERVICE_NEW_RUN,
    CONF_SHOW_PANEL, DOMAIN, PLATFORMS, REPORT_FILENAME, SERVICE_BACKTEST,
    SERVICE_CLOSE_ALL, SERVICE_GENERATE_REPORT, SERVICE_IMPORT_HISTORY,
    SERVICE_PREPARE_SHUTDOWN, SERVICE_RESET_DAY, SERVICE_RESUME,
    SERVICE_VALIDATE_BACKTEST,
)
from .coordinator import GoldScalperCoordinator
from .http import async_register_frontend, async_unregister_frontend

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = GoldScalperCoordinator(hass, entry)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Zijbalk-item en rapportadres. Gebeurt automatisch: het dashboard hoort
    # er te zijn zonder dat je eerst een knop indrukt of YAML plakt.
    options = {**entry.data, **entry.options}
    await async_register_frontend(hass, options.get(CONF_SHOW_PANEL, True))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    async def _on_stop(event) -> None:
        await coordinator.async_shutdown_hook()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)
    )
    _register_services(hass)
    return True


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_CLOSE_ALL):
        return

    def _coordinators() -> list[GoldScalperCoordinator]:
        return list(hass.data.get(DOMAIN, {}).values())

    async def prepare_shutdown(call: ServiceCall) -> None:
        for coordinator in _coordinators():
            result = await coordinator.async_prepare_shutdown()
            _LOGGER.info("Afwikkelen: %s", result.get("message"))

    async def close_all(call: ServiceCall) -> None:
        for coordinator in _coordinators():
            await coordinator.async_close_all()

    async def resume(call: ServiceCall) -> None:
        for coordinator in _coordinators():
            if not await coordinator.async_resume():
                raise HomeAssistantError(
                    "Hervatten geweigerd: de daglimiet is vandaag al te vaak "
                    "opnieuw gezet.\n\nWacht tot morgen, of roep "
                    "gold_scalper.reset_day aan om de dag opnieuw te beginnen."
                )

    async def generate_report(call: ServiceCall) -> None:
        from .dashboard.report import write_report

        for coordinator in _coordinators():
            path = hass.config.path(call.data.get("path") or REPORT_FILENAME)
            from homeassistant.util import dt as dt_util

            written = await hass.async_add_executor_job(
                write_report, coordinator.db, coordinator.run_id, path,
                coordinator.gate, dt_util.DEFAULT_TIME_ZONE,
                coordinator.conversion.as_dict(),
            )
            _LOGGER.info("Keuringsrapport geschreven naar %s", written)

    async def reset_day(call: ServiceCall) -> None:
        """Begin de handelsdag opnieuw zonder op middernacht te wachten."""
        for coordinator in _coordinators():
            bericht = await coordinator.async_reset_day()
            _LOGGER.warning("Handmatige dagreset: %s", bericht)

    def _zeg_wat_er_misging(naam: str):
        """Maak van een onverwachte fout een leesbare melding.

        Home Assistant toont bij een fout die geen HomeAssistantError is
        alleen "Unknown error", zonder bestand of regel. De traceback belandt
        dan in het logboek van de WebSocket-laag in plaats van bij deze
        integratie, en dan is niet te zien wat er stukging.

        Dat kostte twee ronden gissen. Nu komt de oorzaak in de melding zelf
        te staan, en de volledige traceback onder deze logger.
        """
        def omhullen(functie):
            @functools.wraps(functie)
            async def uitvoeren(call: ServiceCall) -> None:
                try:
                    await functie(call)
                except HomeAssistantError:
                    raise
                except Exception as err:  # noqa: BLE001
                    _LOGGER.exception("Actie %s liep vast", naam)
                    raise HomeAssistantError(
                        f"{naam} liep vast op {type(err).__name__}: {err}. "
                        "De volledige traceback staat in het logboek onder "
                        "custom_components.gold_scalper."
                    ) from err
            return uitvoeren
        return omhullen

    async def backtest(call: ServiceCall) -> None:
        """Draai de strategie over de opgebouwde historie."""
        from .analysis.backtest import run_backtest

        for coordinator in _coordinators():
            # Eerst het archief: dat groeit over herstarts heen en bevat
            # doorgaans veel meer dan wat er in het geheugen staat. Zonder
            # historie is een hypothese pas na weken te toetsen; met historie
            # in een minuut.
            candles = None
            if coordinator.archive is not None:
                try:
                    candles = await hass.async_add_executor_job(
                        coordinator.archive.load,
                        coordinator.symbol, coordinator.timeframe,
                    )
                except (ValueError, RuntimeError):
                    candles = None

            if candles is None or len(candles) < 310:
                # Terugvallen op wat er in het geheugen zit.
                candles = coordinator._candles

            if candles is None or len(candles) < 310:
                raise HomeAssistantError(
                    f"Er zijn {0 if candles is None else len(candles)} bars "
                    "beschikbaar; een backtest heeft er minstens 310 nodig. "
                    "Het archief vult zich vanaf nu vanzelf."
                )

            spread = call.data.get("spread")
            if spread is None:
                quote = coordinator._last_quote
                spread = quote.spread if quote else 0.60

            # Beide richtingen doorrekenen wanneer daarom wordt gevraagd.
            #
            # De gemeten trefkans ligt ver onder wat willekeurig instappen zou
            # opleveren bij dezelfde exits. Een signaal dat structureel
            # slechter is dan toeval bevat informatie met het verkeerde teken -
            # of dat zo is, valt alleen te zien door het om te draaien.
            #
            # Er valt niets aan af te stellen: het werkt of het werkt niet.
            # Daarom mag deze toets waar parameteroptimalisatie niet mag.
            omgekeerd = bool(call.data.get("invert", False))

            result = await hass.async_add_executor_job(
                functools.partial(
                    run_backtest, candles, coordinator.strategy_cfg,
                    coordinator.exits.config, spread=spread,
                    slippage=call.data.get("slippage", 0.02),
                    units=call.data.get("units", coordinator.units),
                    invert=omgekeerd,
                )
            )
            summary = result.summary()
            summary["invert"] = omgekeerd
            coordinator.backtest = summary
            _LOGGER.warning(
                "Backtest%s over %d bars: %d trades, trefkans %.1f%%, "
                "netto %.2f, bruto %.2f, kosten %.2f",
                " (omgekeerd)" if omgekeerd else "",
                summary["bars"], summary["trades"], summary["win_rate"],
                summary["net_pnl"], summary["gross_pnl"],
                summary["total_costs"],
            )
            hass.bus.async_fire(f"{DOMAIN}_backtest_done", summary)
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_PREPARE_SHUTDOWN, _zeg_wat_er_misging("prepare_shutdown")(prepare_shutdown))
    hass.services.async_register(DOMAIN, SERVICE_CLOSE_ALL, _zeg_wat_er_misging("close_all")(close_all))
    hass.services.async_register(DOMAIN, SERVICE_RESUME, _zeg_wat_er_misging("resume")(resume))
    hass.services.async_register(DOMAIN, SERVICE_GENERATE_REPORT, _zeg_wat_er_misging("generate_report")(generate_report))
    async def import_history(call: ServiceCall) -> None:
        """Vul het archief met historie van de broker.

        In één stap zoveel bars als de broker in één keer geeft. Bewust
        handmatig en niet automatisch: elk datapunt telt tegen het weekquotum,
        en een importlus die zichzelf start kan dat in een uur opmaken.
        """
        gevraagd = int(call.data.get("bars", 1000))

        for coordinator in _coordinators():
            if coordinator.archive is None:
                raise HomeAssistantError("Het archief is niet geopend.")

            try:
                candles = await coordinator.venue.candles(
                    coordinator.symbol, coordinator.timeframe, gevraagd
                )
            except Exception as err:  # noqa: BLE001
                raise HomeAssistantError(
                    f"Historie ophalen mislukte: {err}. Bij IG telt elk "
                    "datapunt tegen het weekquotum; is dat op, probeer het "
                    "volgende week opnieuw of vraag minder bars."
                ) from err

            nieuw = await hass.async_add_executor_job(
                coordinator.archive.store, coordinator.symbol,
                coordinator.timeframe, candles, "import",
            )
            stats = await hass.async_add_executor_job(
                coordinator.archive.stats, coordinator.symbol,
                coordinator.timeframe,
            )
            # Informatief, geen waarschuwing: een geslaagde import hoort niet
            # als rood item in het logboek te staan. Het onderscheid gaat
            # verloren als elke geslaagde handeling eruitziet als een probleem.
            _LOGGER.info(
                "Historie ingelezen: %d bars opgehaald, %d nieuw. Archief nu "
                "%d bars over %.1f dagen, %d gaten, dekking %.0f%%.",
                len(candles), nieuw, stats.bars, stats.span_days,
                stats.gaps, stats.coverage * 100,
            )
            await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_IMPORT_HISTORY, _zeg_wat_er_misging("import_history")(import_history),
        schema=vol.Schema({
            vol.Optional("bars"): vol.All(vol.Coerce(int), vol.Range(100, 5000)),
        }),
    )

    async def validate_backtest(call: ServiceCall) -> None:
        """Draai de backtest over de periode waarin de bot werkelijk handelde.

        Alle hypothesen die je op historische data toetst, rusten op de aanname
        dat de backtest de werkelijkheid nabootst. Die aanname is zelden
        gecontroleerd, en als hij niet klopt is elke toets erop waardeloos.
        """
        from .analysis.backtest import run_backtest
        from .analysis.validation import compare

        for coordinator in _coordinators():
            if coordinator.archive is None:
                raise HomeAssistantError("Het archief is niet geopend.")

            trades = await hass.async_add_executor_job(
                coordinator.db.closed_trades, coordinator.run_id
            )
            if not trades:
                raise HomeAssistantError(
                    "Geen gesloten trades in deze run om tegen te vergelijken."
                )

            # Precies de periode waarin gehandeld is, met wat aanloop voor de
            # indicatoren. Zonder die aanloop begint de backtest blind.
            eerste = min(t.open_time for t in trades)
            laatste = max(t.close_time or t.open_time for t in trades)
            start = int(
                datetime.fromisoformat(eerste).timestamp()
            ) - 400 * 900
            eind = int(datetime.fromisoformat(laatste).timestamp())

            try:
                candles = await hass.async_add_executor_job(
                    lambda: coordinator.archive.load(
                        coordinator.symbol, coordinator.timeframe, start, eind
                    )
                )
            except (ValueError, RuntimeError) as err:
                raise HomeAssistantError(
                    f"Het archief heeft geen bars over deze periode: {err}. "
                    "Vul het eerst met gold_scalper.import_history."
                ) from err

            quote = coordinator._last_quote
            # Met sleutelwoorden: `run_backtest` heeft keyword-only
            # argumenten, en positioneel doorgeven faalt altijd.
            result = await hass.async_add_executor_job(
                functools.partial(
                    run_backtest, candles, coordinator.strategy_cfg,
                    coordinator.exits.config,
                    spread=quote.spread if quote else 0.60,
                    slippage=coordinator.strategy_cfg.expected_slippage,
                    units=coordinator.units,
                )
            )
            validatie = await hass.async_add_executor_job(
                compare, trades, result
            )
            coordinator.validation = validatie.as_dict()

            _LOGGER.info(
                "Backtestvalidatie: %s. %s",
                validatie.verdict, validatie.explanation.split("\n")[0],
            )
            hass.bus.async_fire(
                f"{DOMAIN}_validation_done", coordinator.validation
            )
            await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_VALIDATE_BACKTEST,
        _zeg_wat_er_misging("validate_backtest")(validate_backtest),
    )

    async def new_run(call: ServiceCall) -> None:
        """Begin bewust een nieuwe bewijsfase.

        Nodig wanneer blijkt dat de meting fout was: dan wil je opnieuw
        beginnen zonder iets aan de strategie te veranderen. Zonder deze dienst
        zou je een instelling moeten verzinnen om aan te passen, en dan meet je
        twee dingen tegelijk.
        """
        reden = call.data.get("note")
        for coordinator in _coordinators():
            nieuw = await coordinator.async_new_run(reden)
            _LOGGER.warning("Nieuwe bewijsfase: run %s", nieuw)

    hass.services.async_register(
        DOMAIN, SERVICE_NEW_RUN, _zeg_wat_er_misging("new_run")(new_run),
        schema=vol.Schema({vol.Optional("note"): cv.string}),
    )

    async def recheck_exits(call: ServiceCall) -> None:
        """Laat alle uitstapprijzen opnieuw bij de broker opzoeken.

        Nodig omdat een eerdere versie de verkeerde transactie kon koppelen:
        twee posities met bijna dezelfde instapprijs maar tegengestelde
        richting waren niet te scheiden. Die trades staan als gecorrigeerd in
        de database terwijl hun uitstapprijs van een andere trade komt.
        """
        for coordinator in _coordinators():
            aantal = await hass.async_add_executor_job(
                coordinator.db.mark_for_recheck, coordinator.run_id
            )
            _LOGGER.warning(
                "%d trade(s) worden opnieuw opgezocht bij de broker. Dat "
                "gebeurt in stappen van vijf, elke paar minuten.", aantal,
            )
            await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_RECHECK_EXITS,
        _zeg_wat_er_misging("recheck_exits")(recheck_exits),
    )

    hass.services.async_register(
        DOMAIN, SERVICE_RESET_DAY, _zeg_wat_er_misging("reset_day")(reset_day),
    )
    async def indicator_lab(call: ServiceCall) -> None:
        """Toets elke indicator op het barsarchief.

        Ontdekken en bevestigen zijn gescheiden, en de lat stijgt met het
        aantal indicatoren. Wie twintig indicatoren op dezelfde data toetst,
        vindt er anders altijd een paar die door toeval lijken te werken.
        """
        from .analysis.indicator_lab import run_lab

        for coordinator in _coordinators():
            if coordinator.archive is None:
                raise HomeAssistantError("Het archief is niet geopend.")
            try:
                candles = await hass.async_add_executor_job(
                    coordinator.archive.load,
                    coordinator.symbol, coordinator.timeframe,
                )
            except (ValueError, RuntimeError) as err:
                raise HomeAssistantError(
                    f"Geen bars in het archief: {err}"
                ) from err

            rapport = await hass.async_add_executor_job(
                functools.partial(
                    run_lab, candles,
                    doel=coordinator.strategy_cfg.take_profit_atr,
                    stop=coordinator.strategy_cfg.stop_loss_atr,
                    kosten=call.data.get("kosten", 0.75),
                )
            )
            coordinator.lab = rapport.as_dict()
            _LOGGER.warning("Indicatorlab: %s", rapport.conclusie)
            hass.bus.async_fire(f"{DOMAIN}_indicator_lab", coordinator.lab)
            await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_INDICATOR_LAB,
        _zeg_wat_er_misging("indicator_lab")(indicator_lab),
        schema=vol.Schema({vol.Optional("kosten"): vol.Coerce(float)}),
    )

    hass.services.async_register(
        DOMAIN, SERVICE_BACKTEST, _zeg_wat_er_misging("backtest")(backtest),
        # Elk veld uit services.yaml moet hier staan. Ontbreekt er een, dan
        # weigert de validatie zodra de interface dat veld meestuurt, en ziet
        # de gebruiker alleen "Unknown error" - zonder aanwijzing welk veld.
        # Er staat een test op die beide lijsten vergelijkt.
        schema=vol.Schema({
            vol.Optional("spread"): vol.Coerce(float),
            vol.Optional("slippage"): vol.Coerce(float),
            vol.Optional("units"): vol.Coerce(float),
            vol.Optional("invert"): cv.boolean,
        }),
    )


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Bewaarde toestand opruimen als de entry verdwijnt.

    Anders blijft een noodstop van een verwijderde configuratie in
    .storage staan en duikt hij op bij een gelijknamige nieuwe entry.
    """
    from .storage.state import StateStore

    await StateStore(hass, entry.entry_id).async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: GoldScalperCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown_hook()
        await async_unregister_frontend(hass)
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
