"""Analysemodules voor Crypto Advisor."""

from .engine import AnalysisResult, analyse, combine_timeframes, score_to_state
from .signals import Candles, Signal

__all__ = [
    "AnalysisResult",
    "Candles",
    "Signal",
    "analyse",
    "combine_timeframes",
    "score_to_state",
]
