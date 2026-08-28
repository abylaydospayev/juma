from juma.ui import normalize_display_text


def test_repairs_mojibake_and_inline_math() -> None:
    assert normalize_display_text(r"Jacobiâ€™s formula for \(A\)") == "Jacobi’s formula for $A$"


def test_normalizes_display_math() -> None:
    assert normalize_display_text(r"Before \[x = 1\] after") == (
        "Before \n\n$$\nx = 1\n$$\n\n after"
    )
