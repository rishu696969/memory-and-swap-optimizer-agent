"""Human-approval gate enforced at the execution boundary (inside the MCP server).

How it works
------------
1. A state-changing tool (e.g. request_swappiness_change) never executes.  It
   validates its arguments and calls ``ApprovalGate.create()``, which stores a
   *pending operation* {op_id, action, args, nonce, expiry} on the server and
   returns a description for the human.
2. The REPL (host) shows the exact action and arguments to the user.  Only if
   the user types "yes" does the host compute

       token = HMAC-SHA256(approval_key, canonical(op_id, action, args, nonce))

   The approval key is a random secret created by the host at start-up and
   given to the server through its environment.  The model never sees it.
3. The host calls the host-only tool ``apply_approved_change(op_id, token)``.
   The server recomputes the HMAC from *its own stored copy* of the operation
   and compares with ``hmac.compare_digest``.  The operation is single-use and
   expires after a TTL.

Consequences: a model cannot approve anything (it lacks the key); a model
setting "approved": true is rejected by validation; an approval for
swappiness=60 cannot be replayed for swappiness=10 or reused a second time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any


class ApprovalError(PermissionError):
    """Raised when an execution request lacks a valid human approval."""


def canonical_payload(op_id: str, action: str, args: dict[str, Any], nonce: str) -> bytes:
    """Deterministic byte string that the approval token signs."""
    return json.dumps(
        {"op_id": op_id, "action": action, "args": args, "nonce": nonce},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_token(key: bytes, op_id: str, action: str, args: dict[str, Any], nonce: str) -> str:
    """Host side: sign exactly the operation the human approved."""
    return hmac.new(key, canonical_payload(op_id, action, args, nonce), hashlib.sha256).hexdigest()


@dataclass
class PendingOperation:
    op_id: str
    action: str                 # fixed action name, e.g. "set_swappiness"
    args: dict[str, Any]
    summary: str                # human-readable description shown before approval
    nonce: str
    created_at: float
    expires_at: float
    details: dict[str, Any] = field(default_factory=dict)

    def public_view(self) -> dict[str, Any]:
        """What is returned to the host (and model).  Contains no secret."""
        return {
            "status": "approval_required",
            "op_id": self.op_id,
            "action": self.action,
            "arguments": self.args,
            "nonce": self.nonce,
            "summary": self.summary,
            "details": self.details,
            "expires_in_seconds": max(0, int(self.expires_at - time.time())),
            "note": "Nothing has been executed. A human must approve this exact operation in the REPL.",
        }


class ApprovalGate:
    """Stores pending operations and verifies approval tokens."""

    def __init__(self, key: bytes, ttl_seconds: int = 300, clock=time.time):
        if len(key) < 16:
            raise ValueError("approval key too short")
        self._key = key
        self._ttl = ttl_seconds
        self._clock = clock
        self._pending: dict[str, PendingOperation] = {}
        self._lock = threading.Lock()

    def create(self, action: str, args: dict[str, Any], summary: str, details: dict[str, Any] | None = None) -> PendingOperation:
        now = self._clock()
        op = PendingOperation(
            op_id="op-" + secrets.token_hex(6),
            action=action,
            args=dict(args),
            summary=summary,
            nonce=secrets.token_hex(8),
            created_at=now,
            expires_at=now + self._ttl,
            details=details or {},
        )
        with self._lock:
            self._purge_expired(now)
            self._pending[op.op_id] = op
        return op

    def consume(self, op_id: str, token: str) -> PendingOperation:
        """Verify *token* for *op_id* and remove the operation (single use).

        Raises ApprovalError for an unknown/expired operation or a bad token.
        A bad token does not consume the operation, so a forged attempt cannot
        be used to cancel a legitimate request -- but it is audited.
        """
        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            op = self._pending.get(op_id)
            if op is None:
                raise ApprovalError(f"no pending operation '{op_id}' (unknown, expired or already used)")
            expected = compute_token(self._key, op.op_id, op.action, op.args, op.nonce)
            if not isinstance(token, str) or not hmac.compare_digest(expected, token):
                raise ApprovalError("approval token is not valid for this exact operation")
            del self._pending[op_id]
            return op

    def cancel(self, op_id: str) -> bool:
        with self._lock:
            return self._pending.pop(op_id, None) is not None

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def _purge_expired(self, now: float) -> None:
        for oid in [o for o, op in self._pending.items() if op.expires_at <= now]:
            del self._pending[oid]
