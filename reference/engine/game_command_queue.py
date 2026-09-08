# -*- coding: utf-8 -*-
"""Thread-safe bridge from UIKit callbacks to the gameplay thread.

UIKit owns touch callbacks on iOS' main thread, while ``GameApp.update`` owns
all mutable gameplay state on the simulation thread.  This small queue keeps
that boundary explicit.  Continuous joystick samples are coalesced so a slow
frame cannot accumulate a long trail of obsolete aim positions.
"""

from collections import deque
import threading


class GameCommandQueue:
    """Bounded FIFO queue with tail and bounded-burst compaction."""

    def __init__(self, max_pending=128):
        self.max_pending = max(8, int(max_pending))
        self._lock = threading.RLock()
        self._items = deque()
        self._sequence = 0
        self.dropped = 0

    def post(
        self,
        name,
        payload=None,
        coalesce=False,
        accumulate_key=None,
        compact_through=None,
    ):
        command_name = str(name or "").strip()
        if not command_name:
            raise ValueError("command name is required")
        command_payload = dict(payload or {})
        with self._lock:
            self._sequence += 1
            item = (self._sequence, command_name, command_payload)

            # ``compact_through`` lets mutually independent selector commands
            # share one short burst.  Example: tool -> magic -> mode -> tool
            # becomes [magic, mode, tool(+2)] rather than four callbacks.  The
            # merged command is moved to the tail so the final on-screen message
            # still corresponds to the user's most recent selector tap.  A
            # non-selector command is a strict FIFO barrier and stops the scan.
            merge_index = None
            if accumulate_key and compact_through and self._items:
                # Callers pass a tiny immutable collection of command names;
                # reuse it directly so the UIKit hot path allocates no set.
                allowed = compact_through
                if command_name in allowed:
                    for index in range(len(self._items) - 1, -1, -1):
                        previous_name = self._items[index][1]
                        if previous_name not in allowed:
                            break
                        if previous_name == command_name:
                            merge_index = index
                            break
            elif self._items and self._items[-1][1] == command_name:
                merge_index = len(self._items) - 1

            if merge_index is not None:
                if accumulate_key:
                    previous = self._items[merge_index]
                    merged = dict(previous[2] or {})
                    key = str(accumulate_key)
                    try:
                        old_value = int(merged.get(key, 0))
                    except Exception:
                        old_value = 0
                    try:
                        new_value = int(command_payload.get(key, 0))
                    except Exception:
                        new_value = 0
                    # Opposite-signed quantity adjustments must stay in
                    # separate FIFO groups because InventorySystem clamps after
                    # each group.  Positive selector steps always merge.
                    opposite_sign = (
                        old_value
                        and new_value
                        and ((old_value < 0) != (new_value < 0))
                    )
                    if not opposite_sign:
                        merged.update(command_payload)
                        merged[key] = old_value + new_value
                        del self._items[merge_index]
                        self._items.append(
                            (self._sequence, command_name, merged)
                        )
                        return self._sequence
                if coalesce:
                    del self._items[merge_index]
                    self._items.append(item)
                    return self._sequence
            if len(self._items) >= self.max_pending:
                # Keep the newest user intent.  In practice continuous aim
                # samples coalesce at the tail, so this path is reserved for a
                # pathological burst of distinct one-shot callbacks.
                self._items.popleft()
                self.dropped += 1
            self._items.append(item)
            return self._sequence

    def drain(self, limit=64):
        count = max(1, int(limit))
        out = []
        with self._lock:
            while self._items and len(out) < count:
                out.append(self._items.popleft())
        return out

    def clear(self):
        with self._lock:
            self._items.clear()

    @property
    def pending(self):
        with self._lock:
            return len(self._items)
