from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.json import JSON

from .models import JumaModelError
from .service import Juma

app = typer.Typer(name="juma", help="A small hierarchical multi-agent runtime.")
console = Console()


def show(result: dict) -> None:
    console.print(JSON(json.dumps(result)))


def show_model_error(error: JumaModelError) -> None:
    console.print(f"[red]Model error:[/red] {error}")


@app.command()
def ask(
    request: Annotated[str, typer.Argument(help="The task to route.")],
    thread: Annotated[str | None, typer.Option(help="Optional durable thread ID.")] = None,
) -> None:
    """Send a task to juma."""
    try:
        with Juma() as juma:
            show(juma.ask(request, thread_id=thread))
    except JumaModelError as error:
        show_model_error(error)
        raise typer.Exit(2) from None


@app.command()
def approve(
    thread: Annotated[str, typer.Argument(help="Thread waiting for approval.")],
    feedback: Annotated[str, typer.Option(help="Optional reviewer feedback.")] = "",
) -> None:
    """Approve and resume a paused task."""
    with Juma() as juma:
        show(juma.resume(thread, approved=True, feedback=feedback))


@app.command()
def reject(
    thread: Annotated[str, typer.Argument(help="Thread waiting for approval.")],
    feedback: Annotated[str, typer.Option(help="Reason for rejection.")] = "",
) -> None:
    """Reject and resume a paused task."""
    with Juma() as juma:
        show(juma.resume(thread, approved=False, feedback=feedback))


@app.command("remember")
def remember_command(
    crew: str,
    content: str,
    scope: Annotated[str, typer.Option(help="shared or crew-private scope.")] = "shared",
) -> None:
    """Write an item to shared memory."""
    with Juma() as juma:
        memory_id = juma.memory.remember(crew, content, scope=scope)
        show({"id": memory_id, "stored": True})


@app.command("memories")
def memories_command(
    query: Annotated[str, typer.Argument()] = "",
    crew: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Search shared memory."""
    with Juma() as juma:
        result = juma.memory.search(query, crew=crew) if query else juma.memory.recent(crew=crew)
        show({"memories": result})


if __name__ == "__main__":
    app()
