# -*- coding: utf-8 -*-
from collections import defaultdict, deque


class EventBus:
    """Small event bus used by gameplay systems.

    Gameplay code publishes semantic events such as:
        message
        item_picked
        npc_talk
        attack

    The iOS UI does not need to know how the event was produced.
    """

    def __init__(self):
        self._listeners = defaultdict(list)
        self._messages = deque(maxlen=32)

    def subscribe(self, name, callback):
        self._listeners[name].append(callback)

    def emit(self, name, **payload):
        if name == "message":
            self._messages.append(payload.get("text", ""))

        for callback in list(self._listeners.get(name, ())):
            try:
                callback(payload)
            except Exception as exc:
                print("Event listener error:", name, repr(exc))

    def pop_message(self):
        if not self._messages:
            return None
        return self._messages.popleft()
