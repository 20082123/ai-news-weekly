"""The signal lifecycle state machine.

Main happy path::

    collected -> normalized -> clustered
        -> verified | insufficient_evidence
        -> packaged
        -> adopted | parked | rejected
        -> published
        -> measured

Any non-terminal business state may also transition to ``failed`` or
``quarantined``. Terminal states never recover silently: once a signal is
measured, parked, rejected, failed or quarantined it has no legal outgoing
transition, and any attempt to leave raises :class:`InvalidStateTransition`.

The state machine is intentionally *not* implemented via SQL triggers; it
lives here in code and is merely *recorded* in the ``state_transition``
audit table by :class:`~ai_signal.storage.repositories.StateTransitionRepository`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .models import ensure_aware_utc


class InvalidStateTransition(Exception):
    """Raised when a requested state transition is not legal."""

    def __init__(self, from_state, to_state, reason: Optional[str] = None) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        message = "invalid transition %r -> %r" % (from_state, to_state)
        if reason:
            message = "%s: %s" % (message, reason)
        super().__init__(message)


class SignalState(str, Enum):
    COLLECTED = "collected"
    NORMALIZED = "normalized"
    CLUSTERED = "clustered"
    VERIFIED = "verified"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PACKAGED = "packaged"
    ADOPTED = "adopted"
    PARKED = "parked"
    REJECTED = "rejected"
    PUBLISHED = "published"
    MEASURED = "measured"
    FAILED = "failed"
    QUARANTINED = "quarantined"


# States with no legal outgoing transition.
TERMINAL_STATES = frozenset(
    {
        SignalState.MEASURED.value,
        SignalState.PARKED.value,
        SignalState.REJECTED.value,
        SignalState.FAILED.value,
        SignalState.QUARANTINED.value,
    }
)


# The forward (business) edges of the graph. Failure edges (-> failed /
# -> quarantined) are added programmatically for every non-terminal state.
_FORWARD = {
    SignalState.COLLECTED.value: {SignalState.NORMALIZED.value},
    SignalState.NORMALIZED.value: {SignalState.CLUSTERED.value},
    SignalState.CLUSTERED.value: {
        SignalState.VERIFIED.value,
        SignalState.INSUFFICIENT_EVIDENCE.value,
    },
    SignalState.VERIFIED.value: {SignalState.PACKAGED.value},
    SignalState.INSUFFICIENT_EVIDENCE.value: {SignalState.PACKAGED.value},
    SignalState.PACKAGED.value: {
        SignalState.ADOPTED.value,
        SignalState.PARKED.value,
        SignalState.REJECTED.value,
    },
    SignalState.ADOPTED.value: {SignalState.PUBLISHED.value},
    SignalState.PUBLISHED.value: {SignalState.MEASURED.value},
}


def _build_allowed() -> dict:
    table = {key: set(values) for key, values in _FORWARD.items()}
    for state in SignalState:
        if state.value not in TERMINAL_STATES:
            table.setdefault(state.value, set()).update(
                {SignalState.FAILED.value, SignalState.QUARANTINED.value}
            )
    return {key: frozenset(values) for key, values in table.items()}


ALLOWED_TRANSITIONS = _build_allowed()


def _coerce(state) -> str:
    """Normalize a state-like value to its canonical string form."""
    if isinstance(state, SignalState):
        return state.value
    try:
        return SignalState(state).value
    except ValueError:
        raise InvalidStateTransition(state, None, "unknown state %r" % (state,))


def is_terminal(state) -> bool:
    """Return True if ``state`` has no legal outgoing transition."""
    return _coerce(state) in TERMINAL_STATES


def can_transition(from_state, to_state) -> bool:
    """Return True if ``from_state -> to_state`` is a legal transition."""
    try:
        source = _coerce(from_state)
        target = _coerce(to_state)
    except InvalidStateTransition:
        return False
    return target in ALLOWED_TRANSITIONS.get(source, frozenset())


@dataclass(frozen=True)
class StateTransitionRecord:
    """An immutable audit record describing one state transition."""

    entity_id: str
    from_state: str
    to_state: str
    timestamp: datetime
    reason: Optional[str] = None
    stage: Optional[str] = None
    policy_version_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.entity_id:
            raise ValueError("entity_id must not be empty")
        object.__setattr__(self, "from_state", _coerce(self.from_state))
        object.__setattr__(self, "to_state", _coerce(self.to_state))
        object.__setattr__(self, "timestamp", ensure_aware_utc(self.timestamp))


def transition(
    entity_id: str,
    from_state,
    to_state,
    *,
    timestamp: Optional[datetime] = None,
    reason: Optional[str] = None,
    stage: Optional[str] = None,
    policy_version_id: Optional[str] = None,
) -> StateTransitionRecord:
    """Validate a transition and return the resulting audit record.

    Raises :class:`InvalidStateTransition` for illegal jumps. Unknown
    states are never silently mapped to a valid state.
    """
    source = _coerce(from_state)
    target = _coerce(to_state)
    if target not in ALLOWED_TRANSITIONS.get(source, frozenset()):
        raise InvalidStateTransition(source, target, reason)
    when = ensure_aware_utc(timestamp) if timestamp is not None else datetime.now(timezone.utc)
    return StateTransitionRecord(
        entity_id=entity_id,
        from_state=source,
        to_state=target,
        timestamp=when,
        reason=reason,
        stage=stage,
        policy_version_id=policy_version_id,
    )
