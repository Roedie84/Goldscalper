"""Het rapport rechtstreeks serveren vanuit de integratie.

De eerdere opzet vroeg te veel van de gebruiker: knop indrukken, wachten op een
bestand in ``www/``, een herstart omdat die map nieuw was, en dan zelf een
iframe-kaart aanmaken. Vier stappen voordat je iets ziet, en elke stap kon
stilletjes misgaan.

Dit is de vervanging. De integratie registreert een eigen adres en zet een
menu-item in de zijbalk. Bij het openen wordt het rapport ter plekke gebouwd
uit de database, dus wat je ziet is altijd actueel. Geen bestand, geen
``www/``, geen herstart.

Beveiliging, eerlijk benoemd: ``requires_auth`` staat uit. Dat moet, want een
iframe in de Home Assistant-frontend stuurt geen bearer-token mee, dus met
authenticatie aan zou het paneel simpelweg leeg blijven. Gevolg is dat iedereen
die je Home Assistant kan bereiken dit rapport kan lezen.

Wat daar wel of niet in staat: handelsresultaten, posities en statistieken.
Géén API-tokens, géén account-ID, géén inloggegevens - die komen in de
rapportgenerator niet voor. Wie meeleest ziet dus wat je strategie deed, niet
hoe hij bij je geld komt. Vind je dat alsnog te veel, dan zet je het paneel uit
met ``show_panel: false`` in de opties.
"""

from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

REPORT_URL = "/api/gold_scalper/report"
PANEL_URL_PATH = "gold-scalper"


class GoldScalperReportView(HomeAssistantView):
    """Bouwt het keuringsrapport bij elke aanvraag opnieuw."""

    url = REPORT_URL
    name = "api:gold_scalper:report"
    # Zie de moduletoelichting: een iframe kan geen token meesturen.
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        coordinators = list(hass.data.get(DOMAIN, {}).values())

        if not coordinators:
            return web.Response(
                text=_placeholder(
                    "Nog geen actieve configuratie",
                    "De integratie is geladen maar er is geen actieve entry. "
                    "Controleer Instellingen, Apparaten en diensten.",
                ),
                content_type="text/html",
            )

        # Meerdere entries: kies met ?entry=... , anders de eerste.
        wanted = request.query.get("entry")
        coordinator = next(
            (c for c in coordinators if c.entry.entry_id == wanted), coordinators[0]
        )

        if coordinator.db is None or coordinator.run_id is None:
            return web.Response(
                text=_placeholder(
                    "Database nog niet gereed",
                    "De integratie is aan het opstarten. Ververs over een halve minuut.",
                ),
                content_type="text/html",
            )

        from .dashboard.report import build_report

        try:
            html = await hass.async_add_executor_job(
                build_report, coordinator.db, coordinator.run_id, coordinator.gate
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Kon rapport niet bouwen")
            return web.Response(
                text=_placeholder("Rapport kon niet worden gebouwd", str(err)),
                content_type="text/html",
                status=500,
            )

        return web.Response(
            text=html,
            content_type="text/html",
            # Niet cachen: het rapport verandert elke handelscyclus.
            headers={"Cache-Control": "no-store, must-revalidate"},
        )


def _placeholder(title: str, message: str) -> str:
    """Nette pagina voor de gevallen waarin er nog niets te tonen valt.

    Beter dan een lege iframe of een stacktrace: de gebruiker moet kunnen zien
    dát er iets werkt en wát er nog ontbreekt.
    """
    return f"""<!DOCTYPE html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gold Scalper</title><style>
body{{margin:0;background:#E4E7E2;color:#22271F;display:flex;align-items:center;
justify-content:center;min-height:100vh;
font:15px/1.6 ui-monospace,"SF Mono",Menlo,monospace}}
.box{{max-width:460px;padding:32px;border-left:3px solid #8F7334;background:#D5DAD3}}
h1{{margin:0 0 10px;font-size:13px;letter-spacing:.18em;text-transform:uppercase}}
p{{margin:0;color:#5A6156;font-size:13px}}
</style></head><body><div class="box">
<h1>{title}</h1><p>{message}</p></div></body></html>"""


async def async_register_frontend(hass: HomeAssistant, show_panel: bool = True) -> None:
    """Registreer het adres en het menu-item. Veilig om vaker aan te roepen."""
    if not hass.data.get(f"{DOMAIN}_view_registered"):
        hass.http.register_view(GoldScalperReportView())
        hass.data[f"{DOMAIN}_view_registered"] = True
        _LOGGER.debug("Rapport bereikbaar op %s", REPORT_URL)

    if not show_panel:
        return

    from homeassistant.components import frontend

    if hass.data.get(f"{DOMAIN}_panel_registered"):
        return
    try:
        frontend.async_register_built_in_panel(
            hass,
            component_name="iframe",
            sidebar_title="Gold Scalper",
            sidebar_icon="mdi:gold",
            frontend_url_path=PANEL_URL_PATH,
            config={"url": REPORT_URL},
            require_admin=True,
        )
        hass.data[f"{DOMAIN}_panel_registered"] = True
        _LOGGER.info("Menu-item 'Gold Scalper' toegevoegd aan de zijbalk")
    except ValueError:
        # Al geregistreerd door een eerdere entry; geen probleem.
        hass.data[f"{DOMAIN}_panel_registered"] = True


async def async_unregister_frontend(hass: HomeAssistant) -> None:
    """Haal het menu-item weg als de laatste entry verdwijnt."""
    if len(hass.data.get(DOMAIN, {})) > 0:
        return
    if not hass.data.get(f"{DOMAIN}_panel_registered"):
        return
    from homeassistant.components import frontend

    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    hass.data[f"{DOMAIN}_panel_registered"] = False
