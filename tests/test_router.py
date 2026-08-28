from juma.router import classify


def test_routes_domain_requests() -> None:
    assert classify("fix the Python test") == "coding"
    assert classify("research the latest paper") == "research"
    assert classify("send an email invite") == "admin"


def test_unknown_request_defaults_to_research() -> None:
    assert classify("tell me something interesting") == "research"
