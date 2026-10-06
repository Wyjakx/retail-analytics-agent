"""Actor-bound opaque references; the reverse linkage never leaves trusted code."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading

from .analytics import ScopeViolation


class CustomerPseudonymizer:
    """Stable labels with an in-memory linkage for previously exposed references.

    Reusing the key regenerates the same labels. After restart a customer must be
    exposed again before its old reference can be resolved; no linkage is saved.
    """

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("A pseudonymization key of at least 32 bytes is required.")
        self._key = key
        self._linkage: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def label(self, actor_id: str, raw_user_id: int) -> str:
        if not isinstance(actor_id, str) or not actor_id.strip() or type(raw_user_id) is not int or raw_user_id <= 0:
            raise ScopeViolation("The trusted customer identity is invalid.")
        payload = json.dumps([actor_id, raw_user_id], separators=(",", ":")).encode()
        reference = "cust_" + hmac.new(self._key, payload, hashlib.sha256).hexdigest()[:32]
        with self._lock:
            self._linkage[(actor_id, reference)] = raw_user_id
        return reference

    def resolve(self, actor_id: str, refs: list[str] | tuple[str, ...]) -> tuple[int, ...]:
        if not isinstance(actor_id, str) or not actor_id.strip() or not refs:
            raise ScopeViolation("Customer references are unavailable for this actor; rerun the ranking first.")
        with self._lock:
            if any(not isinstance(ref, str) or not re.fullmatch(r"cust_[0-9a-f]{32}", ref)
                   or (actor_id, ref) not in self._linkage for ref in refs):
                raise ScopeViolation("Customer references are unavailable for this actor; rerun the ranking first.")
            return tuple(self._linkage[(actor_id, ref)] for ref in refs)
