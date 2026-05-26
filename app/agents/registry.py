from __future__ import annotations

from app.agents.base import Agent

_REGISTRY: dict[str, type[Agent]] = {}


def register(cls: type[Agent]) -> type[Agent]:
    if not cls.key:
        raise ValueError(f"{cls.__name__} must set a non-empty `key`")
    _REGISTRY[cls.key] = cls
    return cls


def get_agent(key: str) -> Agent:
    if key not in _REGISTRY:
        raise KeyError(f"No builtin agent registered for key '{key}'")
    return _REGISTRY[key]()


def list_agent_keys() -> list[str]:
    return sorted(_REGISTRY)


# Import builtin agents so their @register decorators run.
from app.agents import market_intel  # noqa: E402,F401
from app.agents import content_engine  # noqa: E402,F401
