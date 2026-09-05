from juma.guardrails import scan_patch


def test_guardrails_block_credentials() -> None:
    report = scan_patch("""diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n+OPENAI_API_KEY=secret-value\n""", [".env"])
    assert report.status == "block"
    assert any(item.code == "secret_detected" for item in report.findings)


def test_guardrails_allow_normal_patch() -> None:
    report = scan_patch("""diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n""", ["a.py"])
    assert report.status in {"pass", "warn"}
