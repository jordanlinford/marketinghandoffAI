from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas import AgentContext, AgentResult


class Agent(ABC):
    """Base class for all first-party (builtin) agents.

    Subclasses set a unique `key` and implement `run`. The worker resolves the
    code class by key, builds a tenant-scoped AgentContext, and calls run().
    Read-only agents return artifacts only. Action-taking agents additionally
    return proposed_actions, which the platform gates before execution.
    """

    key: str = ""
    display_name: str = ""

    @abstractmethod
    def run(self, ctx: AgentContext) -> AgentResult:
        ...
