"""Source adapters.

Phase 1 defines only the abstract contract and common exceptions. No
concrete GitHub, Twitter, Reddit, Tavily or Agent-Reach source is
implemented yet.
"""

from .base import Source, SourceError, SourceUnavailableError, SourceTimeoutError  # noqa: F401
