from pathlib import Path

from streamlit.testing.v1 import AppTest

from juma.ui import normalize_display_text


def test_repairs_mojibake_and_inline_math() -> None:
    assert normalize_display_text(r"Jacobiâ€™s formula for \(A\)") == "Jacobi’s formula for $A$"


def test_normalizes_display_math() -> None:
    assert normalize_display_text(r"Before \[x = 1\] after") == (
        "Before \n\n$$\nx = 1\n$$\n\n after"
    )


def test_ui_renders_patch_preview(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JUMA_DATA_DIR", str(tmp_path / "data"))
    app = AppTest.from_file(Path(__file__).parents[1] / "src/juma/ui.py").run()
    patch = "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    app.session_state["messages"] = [
        {
            "role": "assistant",
            "content": "Review this patch.",
            "agent": "coding",
            "status": "waiting_approval",
            "events": [],
            "interrupt": {
                "action": {
                    "patch": patch,
                    "fingerprint": "abc123",
                    "risk": "medium",
                    "kind": "code.patch",
                    "summary": "Apply the patch",
                }
            },
            "action": {
                "patch": patch,
                "fingerprint": "abc123",
                "risk": "medium",
                "kind": "code.patch",
                "summary": "Apply the patch",
            },
            "route_confidence": 0.99,
            "route_reason": "coding",
            "patch_result": None,
            "rollback_available": False,
        }
    ]
    app.run()

    assert not app.exception
    assert any(patch.rstrip() in item.value for item in app.code)
