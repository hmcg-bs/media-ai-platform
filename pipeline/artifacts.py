"""Safe local artifact writes for resumable pipeline jobs.

Writers take an advisory lock for the whole job and publish each checkpoint
with a same-directory atomic rename.  A unique temporary name matters here:
the previous fixed ``<output>.tmp`` path let two concurrent jobs overwrite
each other's checkpoint before either rename completed.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class OutputLockedError(RuntimeError):
    """Raised when another process already owns an output artifact."""


@contextmanager
def exclusive_output(path: Path) -> Iterator[None]:
    """Fail fast when another process is writing ``path``.

    The sidecar is intentionally retained after unlock. Unlinking a lock file
    creates an inode race where a new process can lock a replacement while an
    existing waiter still holds the old inode.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OutputLockedError(
                f"another job is already writing {path} (lock: {lock_path})"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def atomic_write_json(path: Path, value: Any) -> None:
    """Durably publish JSON without exposing a partial file to readers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump(value, tmp_file, indent=2, default=str)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
