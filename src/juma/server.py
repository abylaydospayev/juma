"""HTTP server for the juma runtime."""

from fastapi import FastAPI
from pydantic import BaseModel

from .service import Juma


class AskRequest(BaseModel):
    """Payload accepted by the ask endpoint."""

    request: str
    thread_id: str | None = None


app = FastAPI(title="juma")


@app.post("/ask")
def ask(payload: AskRequest) -> dict:
    """Run a request through Juma and return its result envelope."""
    with Juma() as juma:
        return juma.ask(payload.request, thread_id=payload.thread_id)


@app.get("/health")
def health() -> dict[str, str]:
    """Report whether the HTTP service is available."""
    return {"status": "ok"}


def main() -> None:
    """Run the juma HTTP server locally."""
    import uvicorn

    uvicorn.run("juma.server:app", host="127.0.0.1", port=8000)
