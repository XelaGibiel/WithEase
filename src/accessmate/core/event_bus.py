"""Central event bus for communication between modules."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

_log = logging.getLogger(__name__)


class EventBus:
    """Simple publish/subscribe event bus.

    Modules communicate exclusively through events – never by direct reference.
    This keeps modules fully decoupled so they can be loaded or unloaded independently.
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event: str, callback: Callable) -> None:
        self._listeners[event].append(callback)

    def unsubscribe(self, event: str, callback: Callable) -> None:
        try:
            self._listeners[event].remove(callback)
        except ValueError:
            pass

    def publish(self, event: str, **kwargs: Any) -> None:
        # A subscriber must never break the publisher.  Events are frequently
        # published from pynput listener threads; an uncaught exception there
        # would terminate that listener (e.g. mouse events would stop firing).
        for callback in list(self._listeners[event]):
            try:
                callback(**kwargs)
            except Exception:
                _log.exception("event bus subscriber for %r failed", event)


bus = EventBus()
