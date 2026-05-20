"""
MarketDataSource is the interface the market-intel agent depends on. The agent
NEVER imports ZoomInfo or Apollo directly — it talks to this interface, so the
stub (today) and the live clients (later) are interchangeable.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CompanyRecord:
    name: str
    employees: int
    revenue_usd: float
    industry: str
    fit_score: float
    intent_score: float
    source: str = "stub"


@dataclass
class KeywordRecord:
    term: str
    monthly_volume: int
    difficulty: int
    intent: str
    source: str = "stub"


class MarketDataSource(ABC):
    @abstractmethod
    def find_companies(self, icp: dict, limit: int = 100) -> list[CompanyRecord]: ...

    @abstractmethod
    def keyword_universe(self, seeds: list[str], limit: int = 50) -> list[KeywordRecord]: ...
