from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import streamlit as st

from juma.models import JumaModelError
from juma.service import Juma


def normalize_display_text(text: str) -> str:
    """Repair repeated UTF-8/Windows decoding and normalize Markdown math."""
    for _ in range(3):
        if not any(marker in text for marker in ("\u00c3", "\u00e2", "\u00c2")):
            break
        try:
            text = text.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
    text = text.replace("\u00c2", "").replace("\u00a0", " ")
    return (
        text.replace(r"\[", "\n\n$$\n")
        .replace(r"\]", "\n$$\n\n")
        .replace(r"\(", "$")
        .replace(r"\)", "$")
    )


def load_messages(thread_id: str) -> list[dict[str, Any]]:
    return [
        {
            "role": row["role"],
            "content": row["content"],
            "agent": row.get("agent"),
            "status": row.get("status") or "completed",
            "events": row["metadata"].get("events", []),
            "interrupt": row["metadata"].get("interrupt"),
            "route_confidence": row["metadata"].get("route_confidence"),
            "route_reason": row["metadata"].get("route_reason"),
            "action": row["metadata"].get("action"),
            "patch_result": row["metadata"].get("patch_result"),
            "rollback_available": row["metadata"].get("rollback_available", False),
        }
        for row in st.session_state.juma.history(thread_id)
    ]


def initialize() -> None:
    if "thread_id" not in st.session_state:
        st.session_state.thread_id = str(uuid.uuid4())
    if "juma" not in st.session_state:
        st.session_state.juma = Juma()
    if "messages" not in st.session_state:
        st.session_state.messages = load_messages(st.session_state.thread_id)


def pending_approval() -> dict[str, Any] | None:
    for message in reversed(st.session_state.messages):
        if message["role"] == "assistant" and message["status"] == "waiting_approval":
            return message
    return None


def update_assistant(message: dict[str, Any], result: dict[str, Any]) -> None:
    state = result["state"]
    message.update(
        {
            "content": state["response"],
            "agent": state["target_agent"],
            "status": result["status"],
            "events": state.get("events", []),
            "interrupt": result.get("interrupts", [None])[0],
            "route_confidence": state.get("route_confidence"),
            "route_reason": state.get("route_reason"),
            "action": state.get("proposed_action"),
            "patch_result": state.get("patch_result"),
            "rollback_available": state.get("rollback_available", False),
        }
    )


def render_message(message: dict[str, Any], index: int) -> None:
    with st.chat_message(message["role"]):
        st.markdown(normalize_display_text(message["content"]))
        if message["role"] == "assistant":
            agent = message.get("agent") or "juma"
            status = (message.get("status") or "completed").replace("_", " ")
            st.caption(f"{agent} crew - {status}")
            confidence = message.get("route_confidence")
            if confidence is not None:
                st.caption(f"Route confidence: {confidence:.0%}")
            action = message.get("action") or (message.get("interrupt") or {}).get("action")
            if action and action.get("patch"):
                with st.expander(
                    "Patch preview",
                    expanded=message.get("status") == "waiting_approval",
                ):
                    st.code(action["patch"], language="diff")
            patch_result = message.get("patch_result") or {}
            if patch_result:
                test = patch_result.get("test") or {}
                if patch_result.get("status") == "applied_tests_failed":
                    st.error("The patch was applied, but the post-change tests failed.")
                elif patch_result.get("status") == "rolled_back":
                    st.success("The patch was rolled back.")
                elif patch_result.get("status") == "applied_tests_passed":
                    st.success("The patch was applied and the tests passed.")
                if test.get("output"):
                    with st.expander("Test output", expanded=False):
                        st.code(test["output"])
                if message.get("rollback_available") and st.button(
                    "Rollback patch", key=f"rollback-{index}", type="primary"
                ):
                    rollback_patch(index)
            events = message.get("events", [])
            if events:
                with st.expander("Activity", expanded=False):
                    for event in events:
                        detail = normalize_display_text(event["message"])
                        st.write(f"**{event['source']}** - {detail}")
            if (
                message.get("content")
                and message.get("agent")
                and st.button("Remember this", key=f"remember-{index}")
            ):
                st.session_state.juma.remember(message["agent"], message["content"], scope="shared")
                st.toast("Saved to shared memory")


def render_approval(message: dict[str, Any]) -> None:
    interrupt = message.get("interrupt")
    if not interrupt:
        return
    action = interrupt["action"]
    st.warning(f"Approval required: {action['risk']} risk - {action['kind']}")
    st.write(action["summary"])
    if action.get("fingerprint"):
        st.caption(f"Action fingerprint: {action['fingerprint']}")
    feedback = st.text_area(
        "Optional feedback",
        placeholder="Explain what should change, or leave blank to approve.",
        key="approval_feedback",
    )
    approve, reject = st.columns(2)
    if approve.button("Approve", type="primary", use_container_width=True):
        resolve(True, feedback)
    if reject.button("Reject", use_container_width=True):
        resolve(False, feedback)


def resolve(approved: bool, feedback: str) -> None:
    try:
        pending = pending_approval()
        action = (pending or {}).get("action") or ((pending or {}).get("interrupt") or {}).get(
            "action", {}
        )
        with st.spinner("Resuming juma..."):
            result = st.session_state.juma.resume(
                st.session_state.thread_id,
                approved=approved,
                feedback=feedback,
                action_fingerprint=action.get("fingerprint"),
            )
        update_assistant(pending, result)
        st.rerun()
    except (JumaModelError, ValueError) as error:
        st.error(f"Model error: {error}")


def rollback_patch(index: int) -> None:
    try:
        with st.spinner("Rolling back patch..."):
            result = st.session_state.juma.rollback(st.session_state.thread_id)
        update_assistant(st.session_state.messages[index], result)
        st.rerun()
    except (JumaModelError, ValueError) as error:
        st.error(f"Rollback error: {error}")


def reset_chat() -> None:
    st.session_state.juma.close()
    st.session_state.juma = Juma()
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.messages = []
    st.rerun()


def open_thread(thread_id: str) -> None:
    st.session_state.thread_id = thread_id
    st.session_state.messages = load_messages(thread_id)
    st.session_state.pop("approval_feedback", None)
    st.rerun()


def sidebar() -> None:
    with st.sidebar:
        st.header("juma")
        st.caption("Personal AI OS")
        st.code(st.session_state.thread_id, language=None)
        if st.button("New conversation", use_container_width=True):
            reset_chat()

        st.divider()
        st.subheader("Chats")
        threads = st.session_state.juma.threads(limit=40)
        if threads:
            for thread in threads:
                title = normalize_display_text(thread["title"]).replace("\n", " ").strip()
                if len(title) > 42:
                    title = title[:39].rstrip() + "..."
                current = "> " if thread["thread_id"] == st.session_state.thread_id else ""
                label = f"{current}{title}"
                if st.button(
                    label,
                    key=f"open-thread-{thread['thread_id']}",
                    use_container_width=True,
                ):
                    open_thread(thread["thread_id"])
        else:
            st.caption("Your conversations will appear here.")

        st.divider()
        st.subheader("Shared memory")
        query = st.text_input("Search memories", placeholder="e.g. router")
        if query:
            memories = st.session_state.juma.memory.search(query, scope="shared", limit=8)
            if memories:
                for memory in memories:
                    with st.container(border=True):
                        st.caption(f"{memory['crew']} - {memory['scope']}")
                        st.write(normalize_display_text(memory["content"]))
            else:
                st.caption("No matching memories.")

        settings = st.session_state.juma.settings
        st.divider()
        st.caption(f"Model: {settings.openai_model}")
        st.caption(f"Live research: {'on' if settings.enable_web_search else 'off'}")
        st.caption("Risky actions always require approval.")


def app() -> None:
    st.set_page_config(page_title="juma", page_icon="J", layout="wide")
    initialize()
    st.markdown(
        """
        <style>
          .block-container {max-width: 960px; padding-top: 2.5rem;}
          [data-testid="stSidebar"] {border-right: 1px solid rgba(128, 128, 128, .18);}
        </style>
        """,
        unsafe_allow_html=True,
    )
    sidebar()

    st.title("juma")
    st.caption("One request, one crew, clear boundaries.")

    if not st.session_state.messages:
        st.info("Ask juma to research, code, or organize something.")

    for index, message in enumerate(st.session_state.messages):
        render_message(message, index)

    pending = pending_approval()
    if pending:
        render_approval(pending)

    prompt = st.chat_input(
        "What would you like juma to do?",
        disabled=pending is not None,
    )
    if not prompt:
        return

    user_message = {"role": "user", "content": prompt}
    st.session_state.messages.append(user_message)
    with st.chat_message("user"):
        st.markdown(prompt)

    try:
        with st.spinner("juma is working..."):
            result = st.session_state.juma.ask(prompt, thread_id=st.session_state.thread_id)
        assistant_message: dict[str, Any] = {"role": "assistant"}
        update_assistant(assistant_message, result)
        st.session_state.messages.append(assistant_message)
        st.rerun()
    except JumaModelError as error:
        st.error(f"Model error: {error}")


def main() -> None:
    app()


def launch() -> None:
    """Start juma through Streamlit instead of running the UI module bare."""
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(Path(__file__).resolve()), *sys.argv[1:]],
        check=False,
    )


if __name__ == "__main__":
    main()
