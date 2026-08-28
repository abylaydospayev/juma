"""HTTP server for the juma runtime."""

from fastapi import FastAPI

app = FastAPI(title="juma")


@app.get("/health")
def health() -> dict[str, str]:
    """Report whether the HTTP service is available."""
    return {"status": "ok"}
