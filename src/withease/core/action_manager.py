"""Action Manager – decouples triggers from actions.

Instead of hardcoding "F12 = center mouse", users define an Action
(e.g. "center_mouse") and then assign any trigger to it (key, mouse button,
voice command, gamepad, foot switch, etc.).

This means adding a new input device only requires a new trigger type –
the actions themselves never need to change.
"""
from __future__ import annotations

from typing import Any, Callable


class Action:
    def __init__(self, id: str, label: str, callback: Callable) -> None:
        self.id = id
        self.label = label
        self.callback = callback
        self.trigger: str = ""

    def execute(self) -> None:
        self.callback()


class ActionManager:
    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}
        self._trigger_map: dict[str, str] = {}  # trigger_str -> action_id

    def register(self, action: Action) -> None:
        self._actions[action.id] = action

    def unregister(self, action_id: str) -> None:
        action = self._actions.pop(action_id, None)
        if action and action.trigger:
            self._trigger_map.pop(action.trigger, None)

    def assign_trigger(self, action_id: str, trigger: str) -> None:
        action = self._actions.get(action_id)
        if not action:
            return
        action.trigger = trigger
        # Rebuild the whole map so clearing one action never removes another
        # action's still-valid trigger (which a naive pop would do when two
        # actions transiently shared the same trigger).
        self._rebuild_trigger_map()

    def _rebuild_trigger_map(self) -> None:
        self._trigger_map = {
            a.trigger: a.id for a in self._actions.values() if a.trigger
        }

    def fire(self, trigger: str) -> bool:
        """Called by input listeners. Returns True if an action was executed."""
        action_id = self._trigger_map.get(trigger)
        if not action_id:
            return False
        action = self._actions.get(action_id)
        if action:
            action.execute()
            return True
        return False

    def get_all(self) -> list[Action]:
        return list(self._actions.values())

    def load_from_profile(self, actions: dict[str, Any]) -> None:
        """Restore trigger assignments from a saved profile."""
        for action_id, trigger in actions.items():
            self.assign_trigger(action_id, trigger)

    def dump_for_profile(self) -> dict[str, str]:
        """Serialize trigger assignments for saving in a profile."""
        return {a.id: a.trigger for a in self._actions.values() if a.trigger}


action_manager = ActionManager()
