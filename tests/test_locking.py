from __future__ import annotations

import subprocess
import sys
import time

import pytest

from juma.locking import CrossProcessLock, LockBusyError


def test_cross_process_lock_excludes_another_process() -> None:
    key = "test-cross-process-lock"
    script = (
        "from juma.locking import CrossProcessLock; "
        "import sys, time; lock = CrossProcessLock(sys.argv[1]); "
        "lock.acquire(); print('locked', flush=True); time.sleep(10)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, key],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and process.poll() is None:
            if process.stdout.readline().strip() == "locked":
                break
        else:
            pytest.fail(process.stderr.read() if process.stderr else "Lock process did not start")
        with pytest.raises(LockBusyError):
            CrossProcessLock(key).acquire()
    finally:
        process.terminate()
        process.wait(timeout=5)
