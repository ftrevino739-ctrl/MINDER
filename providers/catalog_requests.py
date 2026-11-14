"""Concurrency gate for asynchronous provider model/voice catalogue requests.

The Qt settings panel uses this small, UI-independent helper to coalesce duplicate
requests and reject results that belong to an older provider configuration.
"""
from __future__ import annotations

import threading


class CatalogRequestGate:
    """Track one current catalogue request per provider ID.

    A second request with the same fingerprint is coalesced. A request for a
    changed fingerprint supersedes the previous generation. Completion from an
    older worker can never clear or validate the newer request.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._generations: dict[str, int] = {}
        self._requests: dict[str, tuple[int, str]] = {}

    def begin(self, provider_id: str, fingerprint: str) -> int | None:
        pid, fp = str(provider_id), str(fingerprint)
        with self._lock:
            active = self._requests.get(pid)
            if active is not None and active[1] == fp:
                return None
            generation = self._generations.get(pid, 0) + 1
            self._generations[pid] = generation
            self._requests[pid] = (generation, fp)
            return generation

    def is_current(self, provider_id: str, generation: int,
                   fingerprint: str) -> bool:
        pid = str(provider_id)
        with self._lock:
            return self._requests.get(pid) == (int(generation), str(fingerprint))

    def complete(self, provider_id: str, generation: int,
                 fingerprint: str) -> bool:
        """Clear a request only when the completion still owns that request."""
        pid = str(provider_id)
        with self._lock:
            if self._requests.get(pid) != (int(generation), str(fingerprint)):
                return False
            self._requests.pop(pid, None)
            return True

    def invalidate(self, provider_id: str) -> None:
        pid = str(provider_id)
        with self._lock:
            self._generations[pid] = self._generations.get(pid, 0) + 1
            self._requests.pop(pid, None)

    def invalidate_all(self) -> None:
        with self._lock:
            provider_ids = set(self._generations) | set(self._requests)
            for pid in provider_ids:
                self._generations[pid] = self._generations.get(pid, 0) + 1
            self._requests.clear()
