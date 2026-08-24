"""Paper-broker met realistische kostenmodellering.

De reden dat dit bestand zo veel aandacht aan kosten besteedt: een
paper-trading engine die op mid-price vult produceert winstcijfers die in de
echte markt niet bestaan. Bij goud-scalping is de spread groter dan de
gemiddelde bruto marge per trade, dus een simulator die de spread negeert
draait het teken van je resultaat om. Dat is erger dan helemaal niet
simuleren, want het levert vertrouwen op dat nergens op steunt.

Wat hier wél gemodelleerd wordt:
  * vullen op ask bij kopen en op bid bij verkopen (spread wordt betaald)
  * commissie per lot per zijde
  * slippage die meebeweegt met volatiliteit en ordergrootte, altijd nadelig
  * latency: de fill gebeurt op de prijs ná de vertraging, niet op de
    signaalprijs
  * swap bij posities over de rollover heen
  * marge en een simpele margin call

Wat níet gemodelleerd wordt, en waar het echte resultaat dus nóg iets slechter
zal uitvallen: partiële fills, requotes, verbreding van de spread rond nieuws,
en het feit dat een echte broker jouw orderflow ziet.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone

from ..storage.database import MODE_PAPER, Trade, TradeDatabase

_LOGGER = logging.getLogger(__name__)

#: XAU/USD: één standaardlot is 100 troy ounce.
CONTRACT_SIZE = 100.0


@dataclass(slots=True)
class BrokerCosts:
    """Kostenprofiel van de broker. Vul dit met de cijfers van jóuw account.

    De defaults zijn een gemiddeld raw-spread account. Als je een standaard
    account hebt (geen commissie, bredere spread) zet je commission_per_lot op
    0 en verhoog je de verwachte spread.
    """

    commission_per_lot_per_side: float = 3.50
    #: Basisslippage in USD per ounce, bovenop de spread.
    base_slippage: float = 0.02
    #: Extra slippage als fractie van de ATR. Grote bewegingen kosten meer.
    volatility_slippage_factor: float = 0.05
    #: Slippage groeit met ordergrootte; per lot boven 0.10.
    size_slippage_per_lot: float = 0.01
    #: Swap per lot per nacht (long/short verschillen; goud short is meestal duurder).
    swap_long_per_lot: float = -8.0
    swap_short_per_lot: float = -2.0
    #: Hefboom voor margeberekening.
    #:
    #: ESMA begrenst particuliere klanten in de EU op 20:1 voor goud (30:1 voor
    #: majors, 10:1 voor overige grondstoffen). Brokers adverteren soms met
    #: 200:1 of meer; dat geldt voor professionele klanten of niet-EU-entiteiten.
    #: Reken hier met wat je wérkelijk krijgt, anders klopt je margeberekening
    #: niet en denkt de simulatie dat er ruimte is die er niet is.
    leverage: float = 20.0


@dataclass(slots=True)
class Quote:
    """Een marktquote.

    ``high`` en ``low`` zijn de uitersten sinds de vorige quote. Zonder die
    twee controleert de simulatie stops alleen op het pollmoment, terwijl een
    broker elke tick toetst. Op een pollinterval van twintig seconden wordt zo
    ongeveer 12% van de stops gemist: posities die live waren uitgestopt lopen
    op papier door en maken soms alsnog winst.

    Dat is een systematische vertekening in je voordeel, en juist die soort
    fout zet je op een verkeerd besluit over echt geld.
    """

    bid: float
    ask: float
    time: datetime
    atr: float = 0.0
    high: float | None = None
    low: float | None = None

    @property
    def worst_bid(self) -> float:
        """Laagste bied sinds de vorige quote; waar een long-stop op afgaat."""
        if self.low is None:
            return self.bid
        return min(self.bid, self.low - (self.ask - self.bid) / 2.0)

    @property
    def worst_ask(self) -> float:
        """Hoogste laat sinds de vorige quote; waar een short-stop op afgaat."""
        if self.high is None:
            return self.ask
        return max(self.ask, self.high + (self.ask - self.bid) / 2.0)

    @property
    def best_bid(self) -> float:
        """Hoogste bied sinds de vorige quote; waar een long-TP op afgaat."""
        if self.high is None:
            return self.bid
        return max(self.bid, self.high - (self.ask - self.bid) / 2.0)

    @property
    def best_ask(self) -> float:
        """Laagste laat sinds de vorige quote; waar een short-TP op afgaat."""
        if self.low is None:
            return self.ask
        return min(self.ask, self.low + (self.ask - self.bid) / 2.0)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


class InsufficientMargin(Exception):
    """Niet genoeg vrije marge voor deze positie."""


class PaperBroker:
    """Simuleert orderuitvoering en houdt balans en posities bij."""

    def __init__(
        self,
        database: TradeDatabase,
        run_id: int,
        symbol: str,
        starting_balance: float = 10_000.0,
        costs: BrokerCosts | None = None,
        seed: int | None = None,
    ) -> None:
        self.db = database
        self.run_id = run_id
        self.symbol = symbol
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.costs = costs or BrokerCosts()
        self.cumulative_cost = 0.0
        self._rng = random.Random(seed)
        self._open: list[Trade] = database.open_trades(run_id)

    # -- state -------------------------------------------------------------- #

    @property
    def open_positions(self) -> list[Trade]:
        return list(self._open)

    def equity(self, quote: Quote) -> float:
        """Balans plus ongerealiseerd resultaat van open posities."""
        return self.balance + sum(
            self._unrealised(trade, quote) for trade in self._open
        )

    def used_margin(self) -> float:
        return sum(
            trade.volume * CONTRACT_SIZE * trade.open_mid / self.costs.leverage
            for trade in self._open
        )

    def free_margin(self, quote: Quote) -> float:
        return self.equity(quote) - self.used_margin()

    def _unrealised(self, trade: Trade, quote: Quote) -> float:
        """Waarde bij directe sluiting nú, dus inclusief de spread die je nog
        moet betalen om eruit te komen."""
        exit_price = quote.bid if trade.side == "buy" else quote.ask
        direction = 1.0 if trade.side == "buy" else -1.0
        gross = (exit_price - trade.open_price) * direction * trade.volume * CONTRACT_SIZE
        exit_commission = self.costs.commission_per_lot_per_side * trade.volume
        return gross - exit_commission

    # -- execution ---------------------------------------------------------- #

    def _slippage(self, quote: Quote, volume: float) -> float:
        """Altijd nadelige slippage, in USD per ounce.

        Er zit bewust geen gunstige slippage in het model. In de praktijk
        krijgt een retailklant bij een marktorder zelden een betere prijs dan
        gevraagd, en optimistische aannames hier zijn precies wat een
        backtest onbruikbaar maakt.
        """
        size_component = max(0.0, volume - 0.10) * self.costs.size_slippage_per_lot
        vol_component = quote.atr * self.costs.volatility_slippage_factor
        noise = self._rng.uniform(0.5, 1.5)
        return (self.costs.base_slippage + size_component + vol_component) * noise

    def open_position(
        self,
        side: str,
        volume: float,
        quote: Quote,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        signal_score: float | None = None,
        signal_confidence: float | None = None,
        regime: str | None = None,
        reason: str | None = None,
    ) -> Trade:
        if side not in ("buy", "sell"):
            raise ValueError(f"Ongeldige richting: {side}")

        slippage = self._slippage(quote, volume)
        # Kopen gebeurt op de ask, verkopen op de bid; slippage werkt tegen je.
        if side == "buy":
            fill = quote.ask + slippage
        else:
            fill = quote.bid - slippage

        notional = volume * CONTRACT_SIZE * quote.mid
        required_margin = notional / self.costs.leverage
        if required_margin > self.free_margin(quote):
            raise InsufficientMargin(
                f"Marge tekort: {required_margin:.2f} nodig, "
                f"{self.free_margin(quote):.2f} vrij"
            )

        commission = self.costs.commission_per_lot_per_side * volume
        spread_cost = quote.spread * volume * CONTRACT_SIZE
        slippage_cost = slippage * volume * CONTRACT_SIZE

        trade = Trade(
            run_id=self.run_id,
            mode=MODE_PAPER,
            symbol=self.symbol,
            side=side,
            volume=volume,
            open_time=quote.time.isoformat(timespec="seconds"),
            open_price=fill,
            open_mid=quote.mid,
            open_spread=quote.spread,
            open_slippage=slippage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            commission=commission,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            total_cost=commission + spread_cost + slippage_cost,
            signal_score=signal_score,
            signal_confidence=signal_confidence,
            regime=regime,
            open_reason=reason,
            mae=0.0,
            mfe=0.0,
        )
        self.db.insert_trade(trade)
        self._open.append(trade)
        _LOGGER.debug(
            "Paper %s %.2f lot @ %.2f (spread %.2f, slip %.3f)",
            side, volume, fill, quote.spread, slippage,
        )
        return trade

    def close_position(self, trade: Trade, quote: Quote, reason: str) -> Trade:
        slippage = self._slippage(quote, trade.volume)
        if trade.side == "buy":
            fill = quote.bid - slippage
            direction = 1.0
        else:
            fill = quote.ask + slippage
            direction = -1.0

        units = trade.volume * CONTRACT_SIZE
        exit_commission = self.costs.commission_per_lot_per_side * trade.volume
        swap = self._swap(trade, quote.time)

        # Netto: wat je werkelijk overhoudt. De fills liggen al op ask/bid en
        # bevatten dus spread én slippage; alleen commissie en swap komen er
        # nog los bij.
        realised = (fill - trade.open_price) * direction * units
        total_commission = trade.commission + exit_commission
        net_pnl = realised - total_commission + swap

        # Bruto: hetzelfde resultaat in een wrijvingsloze wereld, dus op
        # mid-price en zonder commissie. Het verschil tussen deze twee ís de
        # kostenpost — dat hoeft niet apart opgeteld te worden en kan zo ook
        # niet dubbel geteld raken.
        gross_pnl = (quote.mid - trade.open_mid) * direction * units

        trade.close_time = quote.time.isoformat(timespec="seconds")
        trade.close_price = fill
        trade.close_mid = quote.mid
        trade.close_spread = quote.spread
        trade.close_slippage = slippage
        trade.close_reason = reason
        trade.commission = total_commission
        trade.slippage_cost += slippage * units
        trade.spread_cost = (trade.open_spread + quote.spread) / 2.0 * units
        trade.swap = swap
        trade.gross_pnl = gross_pnl
        trade.net_pnl = net_pnl
        trade.total_cost = gross_pnl - net_pnl

        notional = trade.volume * CONTRACT_SIZE * trade.open_mid
        trade.return_pct = (net_pnl / notional * 100.0) if notional else 0.0

        opened = datetime.fromisoformat(trade.open_time)
        trade.duration_seconds = int((quote.time - opened).total_seconds())

        self.balance += trade.net_pnl
        self.cumulative_cost += trade.total_cost
        self.db.update_trade(trade)
        self._open = [t for t in self._open if t.id != trade.id]
        return trade

    def _swap(self, trade: Trade, now: datetime) -> float:
        """Swap per gepasseerde rollover (23:00 UTC bij de meeste brokers).

        Voor scalping vrijwel altijd nul; opgenomen omdat een strategie die per
        ongeluk posities over de nacht heen laat staan anders te gunstig oogt.
        """
        opened = datetime.fromisoformat(trade.open_time)
        nights = 0
        cursor = opened.replace(hour=23, minute=0, second=0, microsecond=0)
        if cursor < opened:
            cursor = cursor.replace(day=cursor.day)
        while cursor < now:
            if cursor > opened:
                nights += 1
            cursor = cursor.fromtimestamp(cursor.timestamp() + 86400, tz=timezone.utc)
        if not nights:
            return 0.0
        rate = (
            self.costs.swap_long_per_lot
            if trade.side == "buy"
            else self.costs.swap_short_per_lot
        )
        return rate * trade.volume * nights

    # -- per-tick bookkeeping ----------------------------------------------- #

    def update_positions(self, quote: Quote) -> list[Trade]:
        """Werk MAE/MFE bij en sluit posities die SL of TP raken.

        Belangrijk detail: de stop wordt getoetst tegen de prijs waarop je
        daadwerkelijk zou uitstappen (bid voor een long), niet tegen de mid.
        Anders lijkt een stop verder weg te liggen dan hij is.
        """
        closed: list[Trade] = []
        for trade in list(self._open):
            long = trade.side == "buy"
            direction = 1.0 if long else -1.0

            # Toets tegen de uitersten sinds de vorige quote, niet tegen de
            # momentopname. Een broker kijkt naar elke tick.
            worst = quote.worst_bid if long else quote.worst_ask
            best = quote.best_bid if long else quote.best_ask

            trade.mfe = max(trade.mfe or 0.0, (best - trade.open_price) * direction)
            trade.mae = min(trade.mae or 0.0, (worst - trade.open_price) * direction)

            stop_hit = trade.stop_loss is not None and (
                worst <= trade.stop_loss if long else worst >= trade.stop_loss
            )
            target_hit = trade.take_profit is not None and (
                best >= trade.take_profit if long else best <= trade.take_profit
            )

            # Zijn beide binnen hetzelfde interval geraakt, dan kun je uit de
            # candle niet afleiden welke eerst kwam. De stop aannemen is de
            # enige verdedigbare keuze: gokken op de gunstige volgorde is
            # precies hoe een backtest zichzelf rijk rekent.
            if stop_hit:
                closed.append(
                    self._close_at(trade, quote, trade.stop_loss, "stop_loss")
                )
                continue
            if target_hit:
                closed.append(
                    self._close_at(trade, quote, trade.take_profit, "take_profit")
                )
                continue
            self.db.update_trade(trade)

        # Simpele margin call: onder 50% margin level gaat alles dicht.
        if self._open:
            margin = self.used_margin()
            if margin > 0 and (self.equity(quote) / margin) < 0.5:
                _LOGGER.warning("Margin call in papermodus; alle posities gesloten")
                for trade in list(self._open):
                    closed.append(self.close_position(trade, quote, "margin_call"))

        self.db.record_equity(
            self.run_id,
            self.balance,
            self.equity(quote),
            len(self._open),
            self.cumulative_cost,
        )
        return closed

    def _close_at(self, trade: Trade, quote: Quote, level: float, reason: str) -> Trade:
        """Sluit op het opgegeven niveau in plaats van op de huidige koers.

        Een stop wordt uitgevoerd op het stopniveau, niet op de prijs die
        twintig seconden later toevallig geldt. Sluiten op de latere prijs zou
        de uitkomst willekeurig gunstiger of ongunstiger maken dan hij was.
        """
        half = (quote.ask - quote.bid) / 2.0
        synthetic = Quote(
            bid=level - half if trade.side == "buy" else level,
            ask=level if trade.side == "buy" else level + half,
            time=quote.time,
            atr=quote.atr,
        )
        if trade.side == "buy":
            synthetic = Quote(bid=level, ask=level + 2 * half, time=quote.time, atr=quote.atr)
        else:
            synthetic = Quote(bid=level - 2 * half, ask=level, time=quote.time, atr=quote.atr)
        return self.close_position(trade, synthetic, reason)

    def close_all(self, quote: Quote, reason: str = "manual") -> list[Trade]:
        return [self.close_position(t, quote, reason) for t in list(self._open)]
