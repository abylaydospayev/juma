from pathlib import Path

import pytest

from juma.runner import HostBroker, RunnerJob


def test_runner_job_is_confined_and_digest_pinned(tmp_path: Path) -> None:
    broker = HostBroker(allowed_root=tmp_path, pinned_image="runner@sha256:" + "a" * 64)
    job = RunnerJob(workspace=tmp_path, command=["-c", "print(1)"], image=broker.pinned_image)
    assert broker.validate(job).workspace == tmp_path.resolve()
    with pytest.raises(ValueError):
        broker.validate(job.model_copy(update={"workspace": tmp_path.parent}))
