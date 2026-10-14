"""Durable queue records and locks: private atomic writes, state moves and owner locks."""
from __future__ import annotations

import json
import os
from pathlib import Path
try:
    import fcntl
except ImportError:  # pragma: no cover - the durable run lifecycle is POSIX-only
    fcntl = None
from ..core import records as core_records
from ..system import files as private_files
from ..system import posix as posix_platform
from ..system import windows_io
from . import layout


def private_directory(path: Path) -> None:
    if os.name == "nt":
        windows_io.ensure_private_directory(path)
    else:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)


def ensure_daemon_dirs(root: Path) -> None:
    root_existed = root.exists()
    if os.name == "nt":
        private_directory(root)
    else:
        root.mkdir(parents=True, exist_ok=True)
    if not root_existed:
        fsync_directory(root.parent)
    created = False
    for name in (layout.ACTIVE_STATE, layout.LOCKS_STATE, "pending", "running", "done", "failed", "canceled", "acks", "goals"):
        path = root / name
        if path.exists():
            continue
        path.mkdir(parents=True, exist_ok=True)
        created = True
    if created:
        fsync_directory(root)


def acquire_path_lock(path: Path, *, blocking: bool) -> tuple[object, Path] | None:
    if os.name == "nt":
        return windows_io.acquire_path_lock(path, blocking=blocking)
    if fcntl is None:  # pragma: no cover - supported deployments are POSIX
        raise RuntimeError("durable callback ownership requires POSIX flock support")
    return posix_platform.acquire_path_lock(path, blocking=blocking)


def fsync_directory(path: Path) -> None:
    (windows_io if os.name == "nt" else posix_platform).fsync_directory(path)


def write_request(path: Path, request: dict[str, object]) -> None:
    private_files.write_request(path, request, sync_directory=fsync_directory)


def write_private_text(path: Path, text: str) -> None:
    private_files.write_private_text(path, text, sync_directory=fsync_directory)


def move_request(source: Path, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    if source == destination:
        return destination
    if destination.exists():
        try:
            source_data = json.loads(source.read_text(encoding="utf-8"))
            destination_data = json.loads(destination.read_text(encoding="utf-8"))
            source_id = source_data.get("id") if isinstance(source_data, dict) else None
            destination_id = destination_data.get("id") if isinstance(destination_data, dict) else None
        except Exception as exc:
            raise FileExistsError(f"cannot validate callback collision at {destination}") from exc
        if (
            not isinstance(source_id, str)
            or not isinstance(source_data, dict)
            or not isinstance(destination_data, dict)
            or not core_records.is_callback(source_data)
            or not core_records.is_callback(destination_data)
            or source_id != destination_id
            or source_id != source.stem
            or source_data != destination_data
        ):
            raise FileExistsError(f"refusing to collapse divergent callback records at {destination}")
        try:
            source.unlink()
        except FileNotFoundError:
            pass
        else:
            fsync_directory(source.parent)
        return destination
    if os.name == "nt":
        windows_io.replace_file(source, destination)
    else:
        os.replace(source, destination)
    if destination_dir != source.parent:
        fsync_directory(destination_dir)
    fsync_directory(source.parent)
    return destination


def acquire_owner_lock(root: Path, request_id: str, *, blocking: bool) -> tuple[object, Path] | None:
    if os.name == "nt":
        ensure_daemon_dirs(root)
        return acquire_path_lock(layout.owner_lock_path(root, request_id), blocking=blocking)
    if fcntl is None:  # pragma: no cover - supported deployments are POSIX
        raise RuntimeError("durable callback ownership requires POSIX flock support")
    ensure_daemon_dirs(root)
    path = layout.owner_lock_path(root, request_id)
    handle = path.open("a+", encoding="utf-8")
    os.set_inheritable(handle.fileno(), False)
    operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        fcntl.flock(handle.fileno(), operation)
    except BlockingIOError:
        handle.close()
        return None
    return handle, path


def release_owner_lock(lock: tuple[object, Path] | None, *, remove: bool) -> None:
    if lock is None:
        return
    handle, path = lock
    try:
        if os.name != "nt" and fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
    if remove and os.name != "nt":
        try:
            path.unlink()
            fsync_directory(path.parent)
        except FileNotFoundError:
            pass


def delivery_lock_is_held(root: Path, request_id: str) -> bool:
    lock = acquire_owner_lock(root, layout.delivery_lock_id(request_id), blocking=False)
    if lock is None:
        return True
    release_owner_lock(lock, remove=False)
    return False


def close_parent_lock_copy(lock: tuple[object, Path]) -> None:
    """Close without LOCK_UN after pass_fds transfers the lock to a child."""
    handle, _ = lock
    handle.close()


def request_already_transitioned(root: Path, request_id: str) -> bool:
    return any(layout.request_path(root, state, request_id).exists() for state in ("pending", "running", "done", "failed", "canceled"))


def is_canceled(root: Path, request_id: str) -> bool:
    return layout.request_path(root, "canceled", request_id).exists()


def remove_live_request_copies(root: Path, request_id: str) -> None:
    """Remove dispatchable copies after a durable cancellation tombstone exists."""
    for _ in range(2):
        removed = False
        for state in (layout.ACTIVE_STATE, "pending", "running", "failed"):
            path = layout.request_path(root, state, request_id)
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            fsync_directory(path.parent)
            removed = True
        if not removed:
            break
