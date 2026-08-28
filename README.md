# juma

juma is a small hierarchical multi-agent runtime. A parent LangGraph routes each request to
one isolated crew, persists every transition, and pauses risky actions for human approval.

The current runtime includes:

- OpenAI Responses API with `gpt-5.6-luna` by default.
- Structured routing with an inspectable crew, confidence, and reason.
- Persistent conversation history in SQLite.
- Ranked shared-memory recall and explicit "Remember this" support in the UI.
- Live web search for the research crew, with source links when returned by the API.
- Read-only workspace tools for the coding crew: list, read, search, and fixed checks.
- Path traversal protection and fixed command allowlists for workspace tools.
- Coding patches as reviewable unified diffs, strict structured patch generation, exact-fingerprint
  approval, automatic tests, and rollback after failed post-change tests. Coding responses provide
  final file contents; juma builds the Git diff against the current checkout so stale hunks are
  rejected before approval.
- Action fingerprints, approval interrupts, retries, and append-only local audit events.
- CLI, Streamlit UI, and an MCP memory server.

## Architecture

```text
CLI or Streamlit UI
        |
        v
Structured router --> one isolated crew --> safety gate --> durable result
                            |                    |
                            +--> memory          +--> SQLite checkpoint
                            +--> web search
                            +--> read-only workspace tools
                            +--> validated patch --> tests --> rollback on failure
```

Crews never call each other. They communicate through the parent state, durable conversation
history, or the memory service. External communication, file writes/deletes, publishing, and
deployment are proposed actions and require approval. Requested coding changes are handled as a
special case: the coding crew returns a unified diff, the UI previews it, and the safety gate
binds approval to the diff's action fingerprint. Only that approved diff is applied with Git.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Create `.env` in the project root. It is excluded from Git:

```dotenv
OPENAI_API_KEY=your-new-api-key
JUMA_OPENAI_MODEL=gpt-5.6-luna
JUMA_REASONING_EFFORT=medium
JUMA_ENABLE_WEB_SEARCH=true
JUMA_WORKSPACE_ROOT=C:\path\to\the\project
```

Alternatively set `OPENAI_API_KEY` in the current PowerShell session. Never commit or paste the
key into source files.

## Use

```powershell
juma ask "research durable multi-agent memory"
juma ask "inspect the router and run the tests"
juma ask "Add a FastAPI server with GET /health returning {status: ok}, add tests, and return a unified diff" --thread feature-1
juma ask "delete file old.log" --thread cleanup-1
juma reject cleanup-1 --feedback "Keep it for 30 days"

juma remember coding "The router is deterministic unless a model route is available"
juma memories router --crew admin
```

Risky actions pause. For a coding change, review the displayed patch and fingerprint, then resume
it with the exact fingerprint:

```powershell
juma approve feature-1 --fingerprint <fingerprint-from-preview>
```

After approval, juma applies only that Git patch and runs `pytest -q`. If the tests fail, the UI
offers a rollback button. The same operation is available from the CLI:

```powershell
juma rollback feature-1
```

Patch application expects the configured workspace to be a Git repository with a committed
baseline. Keep unrelated edits out of the patch's target files while reviewing it. Non-code
actions such as email, deletion, publishing, and deployment remain approval-gated proposals until
their dedicated least-privilege adapters are connected.

Thread state survives process restarts in `data/checkpoints.sqlite`; conversation history is
stored in `data/conversations.sqlite`. Operational events are written to `data/audit.jsonl`.

## Browser UI

Start the local UI with:

```powershell
juma-ui
```

Or run Streamlit directly:

```powershell
streamlit run src/juma/ui.py
```

The UI keeps durable chat history, displays the selected crew and routing confidence, shows
activity, previews generated diffs with their fingerprints, supports approval decisions, runs and
displays post-change tests, offers rollback after failures, lets you save useful answers to shared
memory, and searches shared memory in the sidebar.

## MCP memory server

Run the memory MCP server over local stdio:

```powershell
juma-mcp
```

For development with MCP Inspector:

```powershell
mcp dev src/juma/mcp_server.py --with-editable .
```

## Configuration

`JUMA_DATA_DIR` moves the SQLite databases and audit log. Other optional settings are
`JUMA_OPENAI_MODEL`, `JUMA_REASONING_EFFORT`, `JUMA_MAX_OUTPUT_TOKENS`,
`JUMA_ENABLE_WEB_SEARCH`, `JUMA_MAX_TOOL_ROUNDS`, `JUMA_MAX_RETRIES`,
`JUMA_REQUEST_TIMEOUT`, and `JUMA_WORKSPACE_ROOT`.
