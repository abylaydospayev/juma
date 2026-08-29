"""Small cross-process locks used to protect Juma runs."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


class LockBusyError(RuntimeError):
    """Raised when another Juma process owns a requested lock."""


class CrossProcessLock:
    """An OS-backed non-blocking lock represented by a file in the temp directory."""

    def __init__(self, key: str):
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        self.path = Path(tempfile.gettempdir()) / "juma-locks" / f"{digest}.lock"
        self._handle = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("This lock is already acquired.")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        handle = self.path.open("r+b")
        try:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.seek(0)
                handle.write(b"0")
                handle.flush()

            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise LockBusyError(f"Lock is already held: {self.path}") from exc
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> CrossProcessLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
