"""
Tests for bots.wheel_strategy.state_machine — all valid and invalid transitions.

PRD specifies 4 states: no_position → short_put → long_stock → short_call → repeat.
Covers: sell put, put expires worthless, put assigned, sell call, call expires worthless,
call assigned, emergency pause/resume, and all invalid transitions.
"""
import pytest
import re
from bots.wheel_strategy.state_machine import (
    WheelState,
    WheelTransition,
    WheelStateMachine,
    InvalidTransitionError,
    VALID_TRANSITIONS,
)


# ── Valid single-step transitions ──────────────────────────────────────────


class TestValidTransitions:
    """Each allowed transition should succeed and produce the target state."""

    def setup_method(self):
        self.sm = WheelStateMachine()

    def test_initial_state_is_no_position(self):
        assert self.sm.current_state == WheelState.NO_POSITION

    def test_sell_put_from_no_position(self):
        new_state = self.sm.transition(WheelTransition.SELL_PUT)
        assert new_state == WheelState.SHORT_PUT
        assert self.sm.current_state == WheelState.SHORT_PUT

    def test_put_expired_from_short_put(self):
        self.sm.transition(WheelTransition.SELL_PUT)
        new_state = self.sm.transition(WheelTransition.PUT_EXPIRED)
        assert new_state == WheelState.NO_POSITION

    def test_put_closed_from_short_put(self):
        self.sm.transition(WheelTransition.SELL_PUT)
        new_state = self.sm.transition(WheelTransition.PUT_CLOSED)
        assert new_state == WheelState.NO_POSITION

    def test_put_assigned_from_short_put(self):
        self.sm.transition(WheelTransition.SELL_PUT)
        new_state = self.sm.transition(WheelTransition.PUT_ASSIGNED)
        assert new_state == WheelState.LONG_STOCK

    def test_sell_call_from_long_stock(self):
        self.sm.transition(WheelTransition.SELL_PUT)
        self.sm.transition(WheelTransition.PUT_ASSIGNED)
        new_state = self.sm.transition(WheelTransition.SELL_CALL)
        assert new_state == WheelState.SHORT_CALL

    def test_call_expired_from_short_call(self):
        self._reach_short_call()
        new_state = self.sm.transition(WheelTransition.CALL_EXPIRED)
        assert new_state == WheelState.LONG_STOCK

    def test_call_closed_from_short_call(self):
        self._reach_short_call()
        new_state = self.sm.transition(WheelTransition.CALL_CLOSED)
        assert new_state == WheelState.LONG_STOCK

    def test_call_assigned_from_short_call(self):
        self._reach_short_call()
        new_state = self.sm.transition(WheelTransition.CALL_ASSIGNED)
        assert new_state == WheelState.NO_POSITION

    def _reach_short_call(self):
        """Helper: NO_POSITION → SHORT_PUT → LONG_STOCK → SHORT_CALL."""
        self.sm.transition(WheelTransition.SELL_PUT)
        self.sm.transition(WheelTransition.PUT_ASSIGNED)
        self.sm.transition(WheelTransition.SELL_CALL)


# ── Full wheel cycle ───────────────────────────────────────────────────────


class TestFullWheelCycle:
    """Execute complete wheel cycles and verify state returns to start."""

    def setup_method(self):
        self.sm = WheelStateMachine()

    def test_full_cycle_via_assignment(self):
        """no_position → sell put → assigned → sell call → called away → no_position."""
        assert self.sm.current_state == WheelState.NO_POSITION
        self.sm.transition(WheelTransition.SELL_PUT)
        assert self.sm.current_state == WheelState.SHORT_PUT
        self.sm.transition(WheelTransition.PUT_ASSIGNED)
        assert self.sm.current_state == WheelState.LONG_STOCK
        self.sm.transition(WheelTransition.SELL_CALL)
        assert self.sm.current_state == WheelState.SHORT_CALL
        self.sm.transition(WheelTransition.CALL_ASSIGNED)
        assert self.sm.current_state == WheelState.NO_POSITION

    def test_full_cycle_via_puts_expiring(self):
        """no_position → sell put → expires → repeat."""
        self.sm.transition(WheelTransition.SELL_PUT)
        self.sm.transition(WheelTransition.PUT_EXPIRED)
        assert self.sm.current_state == WheelState.NO_POSITION
        # Second cycle
        self.sm.transition(WheelTransition.SELL_PUT)
        self.sm.transition(WheelTransition.PUT_EXPIRED)
        assert self.sm.current_state == WheelState.NO_POSITION

    def test_full_cycle_via_call_expiring(self):
        """no_position → sell put → assigned → sell call → call expires → long_stock."""
        self.sm.transition(WheelTransition.SELL_PUT)
        self.sm.transition(WheelTransition.PUT_ASSIGNED)
        self.sm.transition(WheelTransition.SELL_CALL)
        self.sm.transition(WheelTransition.CALL_EXPIRED)
        assert self.sm.current_state == WheelState.LONG_STOCK
        # Sell another call and repeat
        self.sm.transition(WheelTransition.SELL_CALL)
        self.sm.transition(WheelTransition.CALL_EXPIRED)
        assert self.sm.current_state == WheelState.LONG_STOCK

    def test_double_cycle(self):
        """Two complete wheel cycles back-to-back."""
        for _ in range(2):
            self.sm.transition(WheelTransition.SELL_PUT)
            self.sm.transition(WheelTransition.PUT_ASSIGNED)
            self.sm.transition(WheelTransition.SELL_CALL)
            self.sm.transition(WheelTransition.CALL_ASSIGNED)
            assert self.sm.current_state == WheelState.NO_POSITION


# ── Invalid transitions ────────────────────────────────────────────────────


class TestInvalidTransitions:
    """Any disallowed transition must raise InvalidTransitionError."""

    def test_sell_call_from_no_position(self):
        sm = WheelStateMachine()
        with pytest.raises(InvalidTransitionError) as exc:
            sm.transition(WheelTransition.SELL_CALL)
        assert exc.value.current_state == WheelState.NO_POSITION
        assert exc.value.transition == WheelTransition.SELL_CALL

    def test_put_expired_from_no_position(self):
        sm = WheelStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(WheelTransition.PUT_EXPIRED)

    def test_put_assigned_from_no_position(self):
        sm = WheelStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(WheelTransition.PUT_ASSIGNED)

    def test_sell_put_from_short_put(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        with pytest.raises(InvalidTransitionError):
            sm.transition(WheelTransition.SELL_PUT)

    def test_sell_put_from_long_stock(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        sm.transition(WheelTransition.PUT_ASSIGNED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(WheelTransition.SELL_PUT)

    def test_sell_call_from_no_position(self):
        sm = WheelStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(WheelTransition.SELL_CALL)

    def test_sell_call_from_short_put(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        with pytest.raises(InvalidTransitionError):
            sm.transition(WheelTransition.SELL_CALL)

    def test_put_expired_from_long_stock(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        sm.transition(WheelTransition.PUT_ASSIGNED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(WheelTransition.PUT_EXPIRED)

    def test_put_closed_from_short_call(self):
        self._reach_short_call()
        with pytest.raises(InvalidTransitionError):
            self.sm.transition(WheelTransition.PUT_CLOSED)

    def test_call_expired_from_no_position(self):
        sm = WheelStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(WheelTransition.CALL_EXPIRED)

    def test_call_assigned_from_no_position(self):
        sm = WheelStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(WheelTransition.CALL_ASSIGNED)

    def test_sell_call_from_short_call(self):
        """Cannot sell another call while already in short_call."""
        self._reach_short_call()
        with pytest.raises(InvalidTransitionError):
            self.sm.transition(WheelTransition.SELL_CALL)

    def _reach_short_call(self):
        self.sm = WheelStateMachine()
        self.sm.transition(WheelTransition.SELL_PUT)
        self.sm.transition(WheelTransition.PUT_ASSIGNED)
        self.sm.transition(WheelTransition.SELL_CALL)

    def test_invalid_transition_error_message_contains_state_and_event(self):
        sm = WheelStateMachine()
        with pytest.raises(InvalidTransitionError) as exc:
            sm.transition(WheelTransition.SELL_CALL)
        msg = str(exc.value)
        assert "sell_call" in msg
        assert "no_position" in msg
        assert "sell_put" in msg  # hint about valid transition


# ── Emergency pause / resume ───────────────────────────────────────────────


class TestEmergencyPauseResume:
    """Emergency halt should pause from any state and restore on resume."""

    def test_pause_from_no_position(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.EMERGENCY_PAUSE)
        assert sm.current_state == WheelState.HALTED
        assert sm.is_halted

    def test_pause_from_short_put(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        sm.transition(WheelTransition.EMERGENCY_PAUSE)
        assert sm.current_state == WheelState.HALTED

    def test_pause_from_long_stock(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        sm.transition(WheelTransition.PUT_ASSIGNED)
        sm.transition(WheelTransition.EMERGENCY_PAUSE)
        assert sm.current_state == WheelState.HALTED

    def test_pause_from_short_call(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        sm.transition(WheelTransition.PUT_ASSIGNED)
        sm.transition(WheelTransition.SELL_CALL)
        sm.transition(WheelTransition.EMERGENCY_PAUSE)
        assert sm.current_state == WheelState.HALTED

    def test_resume_from_no_position(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.EMERGENCY_PAUSE)
        sm.transition(WheelTransition.EMERGENCY_RESUME)
        assert sm.current_state == WheelState.NO_POSITION

    def test_resume_from_short_put(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        sm.transition(WheelTransition.EMERGENCY_PAUSE)
        sm.transition(WheelTransition.EMERGENCY_RESUME)
        assert sm.current_state == WheelState.SHORT_PUT

    def test_resume_from_long_stock(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        sm.transition(WheelTransition.PUT_ASSIGNED)
        sm.transition(WheelTransition.EMERGENCY_PAUSE)
        sm.transition(WheelTransition.EMERGENCY_RESUME)
        assert sm.current_state == WheelState.LONG_STOCK

    def test_resume_from_short_call(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        sm.transition(WheelTransition.PUT_ASSIGNED)
        sm.transition(WheelTransition.SELL_CALL)
        sm.transition(WheelTransition.EMERGENCY_PAUSE)
        sm.transition(WheelTransition.EMERGENCY_RESUME)
        assert sm.current_state == WheelState.SHORT_CALL

    def test_no_actions_allowed_while_halted(self):
        """When halted, only EMERGENCY_RESUME should be allowed."""
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        sm.transition(WheelTransition.EMERGENCY_PAUSE)
        assert sm.is_halted
        # None of the normal transitions should work
        for evt in [WheelTransition.SELL_PUT, WheelTransition.PUT_EXPIRED,
                     WheelTransition.PUT_ASSIGNED, WheelTransition.SELL_CALL,
                     WheelTransition.CALL_EXPIRED, WheelTransition.CALL_ASSIGNED]:
            with pytest.raises(InvalidTransitionError):
                sm.transition(evt)

    def test_resume_without_prior_pause_raises(self):
        sm = WheelStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(WheelTransition.EMERGENCY_RESUME)

    def test_pause_resume_preserves_full_cycle(self):
        """Pause mid-cycle, resume, then complete the cycle."""
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        assert sm.current_state == WheelState.SHORT_PUT
        # Emergency!
        sm.transition(WheelTransition.EMERGENCY_PAUSE)
        assert sm.is_halted
        # All clear
        sm.transition(WheelTransition.EMERGENCY_RESUME)
        assert sm.current_state == WheelState.SHORT_PUT
        # Continue the cycle
        sm.transition(WheelTransition.PUT_ASSIGNED)
        assert sm.current_state == WheelState.LONG_STOCK
        sm.transition(WheelTransition.SELL_CALL)
        sm.transition(WheelTransition.CALL_ASSIGNED)
        assert sm.current_state == WheelState.NO_POSITION

    def test_single_emergency_pause_in_transition_graph(self):
        """EMERGENCY_PAUSE should be valid from every non-halted state."""
        for state in WheelState:
            if state == WheelState.HALTED:
                continue
            sm = WheelStateMachine(initial_state=state)
            assert sm.can_transition(WheelTransition.EMERGENCY_PAUSE)

    def test_emergency_resume_only_valid_from_halted(self):
        for state in WheelState:
            if state == WheelState.HALTED:
                continue
            sm = WheelStateMachine(initial_state=state)
            assert not sm.can_transition(WheelTransition.EMERGENCY_RESUME)


# ── can_transition and get_valid_transitions ────────────────────────────────


class TestCanTransition:
    """Verify the convenience methods for introspecting valid moves."""

    def test_can_sell_put_from_no_position(self):
        sm = WheelStateMachine()
        assert sm.can_transition(WheelTransition.SELL_PUT)
        assert not sm.can_transition(WheelTransition.SELL_CALL)

    def test_valid_set_from_short_put(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        valid = sm.get_valid_transitions()
        assert WheelTransition.PUT_EXPIRED in valid
        assert WheelTransition.PUT_CLOSED in valid
        assert WheelTransition.PUT_ASSIGNED in valid
        assert WheelTransition.EMERGENCY_PAUSE in valid
        assert WheelTransition.SELL_CALL not in valid

    def test_valid_set_from_long_stock(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        sm.transition(WheelTransition.PUT_ASSIGNED)
        valid = sm.get_valid_transitions()
        assert WheelTransition.SELL_CALL in valid
        assert WheelTransition.EMERGENCY_PAUSE in valid
        assert WheelTransition.PUT_EXPIRED not in valid

    def test_valid_set_from_short_call(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.SELL_PUT)
        sm.transition(WheelTransition.PUT_ASSIGNED)
        sm.transition(WheelTransition.SELL_CALL)
        valid = sm.get_valid_transitions()
        assert WheelTransition.CALL_EXPIRED in valid
        assert WheelTransition.CALL_CLOSED in valid
        assert WheelTransition.CALL_ASSIGNED in valid
        assert WheelTransition.EMERGENCY_PAUSE in valid
        assert WheelTransition.SELL_PUT not in valid

    def test_valid_set_from_halted(self):
        sm = WheelStateMachine()
        sm.transition(WheelTransition.EMERGENCY_PAUSE)
        valid = sm.get_valid_transitions()
        assert valid == {WheelTransition.EMERGENCY_RESUME}


# ── Transition history ─────────────────────────────────────────────────────


class TestTransitionHistory:
    """The transition log should record each step with timestamps."""

    def setup_method(self):
        self.sm = WheelStateMachine()

    def test_empty_history_on_init(self):
        assert len(self.sm.transition_history) == 0

    def test_one_transition_produces_one_log_entry(self):
        self.sm.transition(WheelTransition.SELL_PUT)
        assert len(self.sm.transition_history) == 1

    def test_full_cycle_produces_four_entries(self):
        self.sm.transition(WheelTransition.SELL_PUT)
        self.sm.transition(WheelTransition.PUT_ASSIGNED)
        self.sm.transition(WheelTransition.SELL_CALL)
        self.sm.transition(WheelTransition.CALL_ASSIGNED)
        assert len(self.sm.transition_history) == 4

    def test_log_entry_has_required_keys(self):
        self.sm.transition(WheelTransition.SELL_PUT)
        entry = self.sm.transition_history[0]
        assert "timestamp" in entry
        assert entry["from"] == "no_position"
        assert entry["event"] == "sell_put"
        assert entry["to"] == "short_put"

    def test_emergency_pause_resume_produces_two_entries(self):
        self.sm.transition(WheelTransition.EMERGENCY_PAUSE)
        self.sm.transition(WheelTransition.EMERGENCY_RESUME)
        assert len(self.sm.transition_history) == 2
        assert self.sm.transition_history[0]["event"] == "emergency_pause"
        assert self.sm.transition_history[1]["event"] == "emergency_resume"

    def test_history_is_a_copy(self):
        self.sm.transition(WheelTransition.SELL_PUT)
        hist = self.sm.transition_history
        hist.clear()             # should not affect internal log
        assert len(self.sm.transition_history) == 1

    def test_reset_clears_history(self):
        self.sm.transition(WheelTransition.SELL_PUT)
        self.sm.transition(WheelTransition.PUT_ASSIGNED)
        assert len(self.sm.transition_history) == 2
        self.sm.reset()
        assert self.sm.current_state == WheelState.NO_POSITION
        assert len(self.sm.transition_history) == 0


# ── Initial state customization ────────────────────────────────────────────


class TestCustomInitialState:
    """State machine should allow starting from a non-default state."""

    def test_start_from_long_stock(self):
        sm = WheelStateMachine(initial_state=WheelState.LONG_STOCK)
        assert sm.current_state == WheelState.LONG_STOCK
        assert sm.can_transition(WheelTransition.SELL_CALL)
        assert not sm.can_transition(WheelTransition.SELL_PUT)

    def test_start_from_short_put(self):
        sm = WheelStateMachine(initial_state=WheelState.SHORT_PUT)
        assert sm.current_state == WheelState.SHORT_PUT

    def test_start_from_short_call(self):
        sm = WheelStateMachine(initial_state=WheelState.SHORT_CALL)
        assert sm.current_state == WheelState.SHORT_CALL

    def test_start_from_halted(self):
        sm = WheelStateMachine(initial_state=WheelState.HALTED)
        assert sm.current_state == WheelState.HALTED
        assert sm.can_transition(WheelTransition.EMERGENCY_RESUME)
        assert not sm.can_transition(WheelTransition.EMERGENCY_PAUSE)


# ── VALID_TRANSITIONS graph sanity ─────────────────────────────────────────


class TestTransitionGraph:
    """Ensure the transition graph itself is well-formed."""

    def test_all_states_have_entries(self):
        for state in WheelState:
            assert state in VALID_TRANSITIONS, f"{state} missing from graph"

    def test_no_state_transitions_to_halted_except_pause(self):
        for state, transitions in VALID_TRANSITIONS.items():
            if state == WheelState.HALTED:
                continue
            for trans, next_state in transitions.items():
                if next_state is not None:
                    assert next_state != WheelState.HALTED or trans == WheelTransition.EMERGENCY_PAUSE

    def test_no_path_leads_to_dead_end(self):
        """Every state should have at least one valid outgoing transition."""
        for state, transitions in VALID_TRANSITIONS.items():
            assert len(transitions) >= 1, f"Dead-end state: {state}"
