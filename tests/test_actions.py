from juma.actions import coding_action


def test_action_fingerprint_binds_exact_request() -> None:
    first = coding_action("delete file old.log")
    second = coding_action("delete file new.log")

    assert first is not None
    assert first["kind"] == "filesystem.delete"
    assert first["fingerprint"] != second["fingerprint"]
    assert first["parameters"] == {"request": "delete file old.log"}
