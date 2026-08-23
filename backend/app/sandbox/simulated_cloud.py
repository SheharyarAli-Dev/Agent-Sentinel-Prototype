"""
app/sandbox/simulated_cloud.py
───────────────────────────────
LiveOps Increment 1 — deterministic local simulated-cloud state store.

Pure-python, fully offline, no external cloud SDKs. Provides a small state
store over two resource families (VMs and snapshots) backed by a single JSON
file, seeded from a canonical seed file that is NEVER written to.

Design notes
────────────
  * Both paths are explicit in the constructor: seed_path (read-only, must
    exist) and state_path (runtime state; created from the seed the first time
    only).
  * Runtime state is created from the seed ONLY when state_path does not exist.
    Constructing a client against an existing state file never erases it, so an
    in-progress demo is not lost by a re-connect.
  * State is guarded by a threading lock for every read and write. The lock is a
    process-level RLock keyed by the canonical resolved state_path, so concurrent
    instances targeting the same file share one lock and cannot lose each other's
    read-modify-write updates; different state paths do not share a global lock.
  * Writes are atomic: JSON is serialised to a collision-safe temporary file
    (tempfile.mkstemp, unique per writer) in the same directory and moved into
    place with os.replace.
  * Resource identifiers are validated with a strict charset
    (letters, digits, hyphen, underscore) so a client-supplied value can never
    be interpreted as a filesystem path (no traversal).
  * This class is deliberately policy-N-AWARE. It performs the operation it is
    asked to perform; ALLOW / WARN / BLOCK enforcement belongs to the execution
    gateway in a later increment. Protected flags are carried in state data but
    the sandbox itself does not refuse mutations.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

# ── Exceptions ─────────────────────────────────────────────────────────────────

class SimulatedCloudError(Exception):
    """Base class for all simulated-cloud errors."""


class InvalidResourceId(SimulatedCloudError):
    """A resource id is empty or contains characters outside the safe charset."""


class ResourceNotFound(SimulatedCloudError):
    """The requested VM or snapshot is not present in current state."""


class ResourceConflict(SimulatedCloudError):
    """The requested change conflicts with existing state (e.g. duplicate id)."""


# ── Identifier validation ──────────────────────────────────────────────────────

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_id(value: str, kind: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidResourceId(f"{kind} id must be a non-empty string.")
    if not _ID_PATTERN.match(value):
        raise InvalidResourceId(
            f"Invalid {kind} id {value!r}: only letters, numbers, "
            "hyphens, and underscores are allowed."
        )
    return value


# ── Process-level lock registry ────────────────────────────────────────────────
#
# Synchronisation is per *state file*, not per instance. Two instances pointing
# at the same resolved state_path must share one RLock so concurrent
# read-modify-write operations cannot overwrite one another. Different state
# paths get their own locks instead of contending on a single global lock.

_LOCK_REGISTRY: dict[str, threading.RLock] = {}
_LOCK_REGISTRY_GUARD = threading.Lock()


def _cloud_lock_for(state_path: Path) -> threading.RLock:
    """Return the process-wide RLock for a canonical, resolved state path."""
    key = os.path.normcase(os.path.abspath(os.fspath(state_path)))
    with _LOCK_REGISTRY_GUARD:
        lock = _LOCK_REGISTRY.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCK_REGISTRY[key] = lock
        return lock


def reset_lock_registry() -> None:
    """Clear the process-wide lock registry. Primarily for testing."""
    with _LOCK_REGISTRY_GUARD:
        _LOCK_REGISTRY.clear()


# ── SimulatedCloud ─────────────────────────────────────────────────────────────

class SimulatedCloud:
    """
    Deterministic simulated-cloud state store.

    Resources live in ``state_path``, a JSON document of the shape::

        {"vms": [{"id", "environment", "state", "protected"}, ...],
         "snapshots": [{"id", "source_vm", "environment", "protected"}, ...]}

    ``seed_path`` supplies the canonical initial state and is only read.
    """

    def __init__(self, seed_path: str | Path, state_path: str | Path) -> None:
        self._seed_path = Path(seed_path)
        self._state_path = Path(state_path)
        # Shared per resolved state_path, so concurrent instances never lose
        # each other's reads/writes (see _cloud_lock_for).
        self._lock = _cloud_lock_for(self._state_path)

        if not self._seed_path.is_file():
            raise SimulatedCloudError(f"Seed file does not exist: {self._seed_path}")

        if self._state_path.exists():
            # Preserve existing runtime state — never auto-reset on construction.
            self._state = self._load_state()
        else:
            self._state = self._from_seed()
            self._save()

    # ── Internal: state lifecycle ──────────────────────────────────────────────

    @staticmethod
    def _strip_metadata(state: dict[str, Any]) -> dict[str, Any]:
        """Drop underscore-prefixed meta keys (e.g. _comment) from the payload."""
        return {k: v for k, v in state.items() if not k.startswith("_")}

    def _from_seed(self) -> dict[str, Any]:
        try:
            with open(self._seed_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise SimulatedCloudError(
                f"Could not read seed file {self._seed_path}: {exc}"
            ) from exc
        return copy.deepcopy(self._strip_metadata(raw))

    def _load_state(self) -> dict[str, Any]:
        try:
            with open(self._state_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise SimulatedCloudError(
                f"Could not read runtime state file {self._state_path}: {exc}"
            ) from exc

    def _reload_locked(self) -> None:
        """
        Re-read the latest persisted state from disk.

        Called under the lock at the start of every public operation so multiple
        SimulatedCloud instances sharing one state path each act on the newest
        committed state (read-modify-write). A stale in-memory copy could
        otherwise overwrite another instance's change.
        """
        self._state = self._load_state()

    def _save(self) -> None:
        """Atomically persist current state via a temp file + os.replace."""
        directory = self._state_path.parent
        fd, tmp_name = tempfile.mkstemp(
            prefix=self._state_path.name + ".",
            suffix=".tmp",
            dir=str(directory),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self._state_path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _snapshot(self) -> dict[str, Any]:
        """Return a deep, independent copy of current state."""
        return copy.deepcopy(self._state)

    # ── Internal: lookups ──────────────────────────────────────────────────────

    def _vm(self, vm_id: str) -> dict[str, Any]:
        for vm in self._state["vms"]:
            if vm["id"] == vm_id:
                return vm
        raise ResourceNotFound(f"VM '{vm_id}' not found.")

    def _snapshot_by_id(self, snapshot_id: str) -> dict[str, Any]:
        for snap in self._state["snapshots"]:
            if snap["id"] == snapshot_id:
                return snap
        raise ResourceNotFound(f"Snapshot '{snapshot_id}' not found.")

    # ── Public API ─────────────────────────────────────────────────────────────

    def reset(self) -> dict[str, Any]:
        """Restore canonical seed state and persist it. Returns the new state."""
        with self._lock:
            self._state = self._from_seed()
            self._save()
            return self._snapshot()

    def get_state(self) -> dict[str, Any]:
        """Return a deep copy of the full current state."""
        with self._lock:
            self._reload_locked()
            return self._snapshot()

    def list_resources(self) -> dict[str, Any]:
        """Return a deep copy of current VMs and snapshots."""
        with self._lock:
            self._reload_locked()
            return self._snapshot()

    def start_vm(self, vm_id: str) -> dict[str, Any]:
        """Start a VM. Idempotent when already running."""
        _validate_id(vm_id, "VM")
        with self._lock:
            self._reload_locked()
            vm = self._vm(vm_id)
            if vm["state"] != "running":
                vm["state"] = "running"
                self._save()
            return self._snapshot()

    def stop_vm(self, vm_id: str) -> dict[str, Any]:
        """Stop a VM. Idempotent when already stopped."""
        _validate_id(vm_id, "VM")
        with self._lock:
            self._reload_locked()
            vm = self._vm(vm_id)
            if vm["state"] != "stopped":
                vm["state"] = "stopped"
                self._save()
            return self._snapshot()

    def create_snapshot(
        self, vm_id: str, snapshot_id: str
    ) -> dict[str, Any]:
        """Create a snapshot of a VM. Rejects duplicate snapshot ids."""
        _validate_id(vm_id, "VM")
        _validate_id(snapshot_id, "snapshot")
        with self._lock:
            self._reload_locked()
            vm = self._vm(vm_id)
            for snap in self._state["snapshots"]:
                if snap["id"] == snapshot_id:
                    raise ResourceConflict(
                        f"Snapshot '{snapshot_id}' already exists."
                    )
            self._state["snapshots"].append(
                {
                    "id": snapshot_id,
                    "source_vm": vm["id"],
                    "environment": vm["environment"],
                    "protected": bool(vm.get("protected", False)),
                }
            )
            self._save()
            return self._snapshot()

    def delete_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        """Delete a snapshot. Rejects unknown snapshot ids."""
        _validate_id(snapshot_id, "snapshot")
        with self._lock:
            self._reload_locked()
            snap = self._snapshot_by_id(snapshot_id)
            self._state["snapshots"].remove(snap)
            self._save()
            return self._snapshot()