"""Base class for all AccessMate modules.

Every module inherits from BaseModule and implements:
- start()  – activate the module's functionality
- stop()   – deactivate cleanly (must be idempotent)
- get_settings_widget() – returns the PySide6 widget shown in the settings GUI

Modules register their actions with the ActionManager on init so they are
available for hotkey assignment even when the module is disabled.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class BaseModule(ABC):
    MODULE_ID: str = ""
    DISPLAY_NAME: str = ""
    DESCRIPTION: str = ""

    def __init__(self) -> None:
        self._enabled: bool = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        if not self._enabled:
            self._enabled = True
            self.start()

    def disable(self) -> None:
        if self._enabled:
            self._enabled = False
            self.stop()

    def toggle(self) -> None:
        if self._enabled:
            self.disable()
        else:
            self.enable()

    @abstractmethod
    def start(self) -> None:
        """Activate the module. Called when user enables it."""

    @abstractmethod
    def stop(self) -> None:
        """Deactivate the module cleanly. Must be safe to call multiple times."""

    @abstractmethod
    def get_settings_widget(self) -> "QWidget":
        """Return the settings widget to be shown in the GUI settings panel."""

    def load_settings(self, settings: dict[str, Any]) -> None:
        """Apply settings from a profile dict. Override in subclasses."""

    def dump_settings(self) -> dict[str, Any]:
        """Serialize current settings for saving to a profile. Override in subclasses."""
        return {}
