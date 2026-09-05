import uuid

import pytest

from juma.changeset import Action, Changeset


def make_changeset() -> Changeset:
    return Changeset(
        run_id=uuid.uuid4(),
        thread_id=uuid.uuid4(),
        workspace_id="ws-1",
        intent="change one file",
        actions=[
            Action(kind="code.patch", summary="patch", payload={"patch": "exact bytes"})
        ],
    )


def test_changeset_fingerprint_is_stable_and_excludes_execution_metadata() -> None:
    changeset = make_changeset()
    assert len(changeset.fingerprint or "") == 64
    updated = changeset.model_copy(update={"status": "succeeded", "outcomes": [{"ok": True}]})
    assert updated.compute_fingerprint() == changeset.fingerprint


def test_changeset_rejects_non_sha256_fingerprint() -> None:
    with pytest.raises(ValueError):
        make_changeset().model_copy(update={"fingerprint": "wrong"})
