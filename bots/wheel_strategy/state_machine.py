"""
State machine for the Wheel Strategy Bot.

States and transitions:
  no_position ──sell_put──→ short_put
  short_put   ──put_assigned──→ long_stock
  short_put   ──put_expired──→ no_position
  short_put   ──put_closed──→ no_position
  long_stock  ──sell_call──→ short_call
  short_call  ──call_assigned──→ no_position
  short_call  ──call_expired──→ long_stock
  short_call  ──call_closed──→ long_stock
  [any]       ──emergency_pause──→ halted
  halted      ──emergency_resume──→ (restore previous state)
"""
from enum import Enum
from typing import Optional, Set, Dict
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


class WheelState(Enum):
    """States in the wheel strategy lifecycle."""
    NO_POSITION = "no_position"       # Starting point, ready to sell puts
    SHORT_PUT = "short_put"           # Have an open short put position
    LONG_STOCK = "long_stock"         # Assigned — own 100+ shares
    SHORT_CALL = "short_call"         # Have an open short call position
    HALTED = "halted"                 # Emergency pause


class WheelTransition(Enum):
    """Allowed transitions (event names)."""
    SELL_PUT = "sell_put"
    PUT_EXPIRED = "put_expired"
    PUT_CLOSED = "put_closed"
    PUT_ASSIGNED = "put_assigned"
    SELL_CALL = "sell_call"
    CALL_EXPIRED = "call_expired"
    CALL_CLOSED = "call_closed"
    CALL_ASSIGNED = "call_assigned"
    EMERGENCY_PAUSE = "emergency_pause"
    EMERGENCY_RESUME = "emergency_resume"


# Define the valid transition graph: state -> {transition: next_state}
VALID_TRANSITIONS: Dict[WheelState, Dict[WheelTransition, WheelState]] = {
    WheelState.NO_POSITION: {
        WheelTransition.SELL_PUT: WheelState.SHORT_PUT,
        WheelTransition.EMERGENCY_PAUSE: WheelState.HALTED,
    },
    WheelState.SHORT_PUT: {
        WheelTransition.PUT_EXPIRED: WheelState.NO_POSITION,
        WheelTransition.PUT_CLOSED: WheelState.NO_POSITION,
        WheelTransition.PUT_ASSIGNED: WheelState.LONG_STOCK,
        WheelTransition.EMERGENCY_PAUSE: WheelState.HALTED,
    },
    WheelState.LONG_STOCK: {
        WheelTransition.SELL_CALL: WheelState.SHORT_CALL,
        WheelTransition.EMERGENCY_PAUSE: WheelState.HALTED,
    },
    WheelState.SHORT_CALL: {
        WheelTransition.CALL_EXPIRED: WheelState.LONG_STOCK,
        WheelTransition.CALL_CLOSED: WheelState.LONG_STOCK,
        WheelTransition.CALL_ASSIGNED: WheelState.NO_POSITION,
        WheelTransition.EMERGENCY_PAUSE: WheelState.HALTED,
    },
    WheelState.HALTED: {
        WheelTransition.EMERGENCY_RESUME: None,  # Restores prior state
    },
}


class InvalidTransitionError(Exception):
    """Raised when a transition is not valid from the current state."""
    def __init__(self, current_state: WheelState, transition: WheelTransition,
                 valid: Set[WheelTransition]):
        self.current_state = current_state
        self.transition = transition
        self.valid_transitions = valid
        super().__init__(
            f"Cannot apply '{transition.value}' from state '{current_state.value}'. "
            f"Valid transitions: {[t.value for t in valid]}"
        )


class WheelStateMachine:
    """
    Finite state machine for the wheel strategy.

    Tracks the current strategy phase and enforces valid transitions.
    Callers should invoke `transition(event)` to move between states.
    """

    def __init__(self, initial_state: WheelState = WheelState.NO_POSITION):
        self._state = initial_state
        self._pre_halt_state: Optional[WheelState] = None
        self._transition_log: list = []      # (timestamp, from, event, to)
        logger.info(f"WheelStateMachine initialized in state {initial_state.value}")

    @property
    def current_state(self) -> WheelState:
        return self._state

    @property
    def is_halted(self) -> bool:
        return self._state == WheelState.HALTED

    @property
    def transition_history(self) -> list:
        """Return a copy of the transition log."""
        return list(self._transition_log)

    def get_valid_transitions(self) -> Set[WheelTransition]:
        """Return the set of transitions valid from the current state."""
        mapping = VALID_TRANSITIONS.get(self._state, {})
        return set(mapping.keys())

    def can_transition(self, event: WheelTransition) -> bool:
        """Check whether a transition is valid from the current state."""
        return event in self.get_valid_transitions()

    def transition(self, event: WheelTransition) -> WheelState:
        """
        Attempt to apply a transition event.

        Args:
            event: The transition to apply.

        Returns:
            The new state after successful transition.

        Raises:
            InvalidTransitionError: If the transition is not allowed.
        """
        valid = self.get_valid_transitions()

        if event not in valid:
            logger.warning(
                f"Invalid transition: {event.value} from {self._state.value}"
            )
            raise InvalidTransitionError(self._state, event, valid)

        if event == WheelTransition.EMERGENCY_PAUSE:
            self._pre_halt_state = self._state
            old = self._state
            self._state = WheelState.HALTED
            self._log(old, event, self._state)
            logger.info(
                f"Emergency halt — saved state: {old.value}"
            )
            return self._state

        if event == WheelTransition.EMERGENCY_RESUME:
            if self._pre_halt_state is None:
                raise InvalidTransitionError(
                    self._state, event, set()
                )
            old = self._state
            restore_to = self._pre_halt_state
            self._pre_halt_state = None
            self._state = restore_to
            self._log(old, event, self._state)
            logger.info(f"Emergency resume — restored to {self._state.value}")
            return self._state

        mapping = VALID_TRANSITIONS[self._state]
        old = self._state
        self._state = mapping[event]
        self._log(old, event, self._state)
        logger.info(f"Transition: {old.value} --[{event.value}]--> {mapping[event].value}")
        return self._state

    def _log(self, from_state: WheelState, event: WheelTransition,
             to_state: WheelState) -> None:
        self._transition_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "from": from_state.value,
            "event": event.value,
            "to": to_state.value,
        })

    def reset(self, state: WheelState = WheelState.NO_POSITION) -> None:
        """Reset the state machine to a given state (useful for testing)."""
        self._state = state
        self._pre_halt_state = None
        self._transition_log.clear()
