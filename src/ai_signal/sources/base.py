"""The abstract source contract.

A ``Source`` is anything that can turn a pagination cursor plus a run
context into a :class:`~ai_signal.domain.models.SourceBatch`. Phase 1
deliberately does not implement any concrete source.

Design rule: a single source failure is expressed through the
``SourceBatch.status`` field (``partial`` / ``unavailable`` / ``failed``)
and must never force the whole pipeline to abort. Only the source adapter
itself may raise :class:`SourceError` for truly unrecoverable problems.
"""

from __future__ import annotations

import abc
from typing import Any, Mapping, Optional

from ..domain.models import SourceBatch


class SourceError(Exception):
    """Base exception for unrecoverable source problems."""


class SourceUnavailableError(SourceError):
    """The source is permanently unavailable for this run."""


class SourceTimeoutError(SourceError):
    """The source did not respond in time."""


class Source(abc.ABC):
    """Abstract base class for all collection sources.

    Concrete sources set ``name`` and ``version`` and implement
    :meth:`collect`. Implementations must not perform any work at import
    time and must not read cookies, tokens or other credentials from
    global state - any credential is passed in explicitly via ``context``
    by future phases.
    """

    name: str = "abstract"
    version: str = "0"

    @abc.abstractmethod
    def collect(
        self,
        cursor: Optional[str],
        context: Mapping[str, Any],
    ) -> SourceBatch:
        """Collect one batch of items.

        Args:
            cursor: Opaque pagination cursor returned by a previous call
                via ``SourceBatch.next_cursor``, or ``None`` for the first
                page.
            context: Run-scoped context (for example the active run id and
                week key). Implementations must treat it as read-only.

        Returns:
            A :class:`SourceBatch` whose ``status`` communicates partial or
            total failure without raising.
        """
        raise NotImplementedError
