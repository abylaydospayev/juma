# juma

juma is a small hierarchical multi-agent runtime. A parent LangGraph routes each request to
one isolated crew, persists every transition, and pauses risky actions for human approval.

## Identity

Juma is an autonomous personal command assistant with strategic initiative and warm, practical
support. It thinks ahead, infers obvious next steps, chooses sensible defaults, and moves work
forward while keeping the user informed. It is composed under pressure, offers clear
recommendations, respects privacy, and asks for approval before risky, irreversible, or external
actions.

**Created by Abylay Dospayev.**

The current runtime includes:

- OpenAI Responses API with `gpt-5.6-luna` by default.
- Structured routing with an inspectable crew, confidence, and reason.
- Persistent conversation history in SQLite.
- A bounded command loop that forms an inspectable plan, chooses practical defaults, and reports
  progress before handing work to one crew.
- Ranked shared-memory recall and explicit "Remember this" support in the UI.
- Durable user preferences shared with future requests.
- Optional OpenAI speech transcription and response audio in the UI and CLI.
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
JUMA_API_TOKEN=use-a-long-random-local-token
JUMA_WORKSPACE_ROOT=C:\path\to\the\project
JUMA_VOICE_ENABLED=false
JUMA_SERVER_HOST=127.0.0.1
JUMA_SERVER_PORT=8000
JUMA_AUTO_SETUP=false
JUMA_AUTO_REPAIR=false
JUMA_MAX_REPAIR_ATTEMPTS=3
JUMA_AUTO_COMMIT=false
JUMA_AUTO_PUSH=false
JUMA_PUSH_REMOTE=origin
```

Alternatively set `OPENAI_API_KEY` and `JUMA_API_TOKEN` in the current PowerShell session. Never
commit or paste either secret into source files. The local API keeps `/health` public and requires
the API token as a Bearer token for all other endpoints.

## Use

```powershell
juma ask "research durable multi-agent memory"
juma ask "inspect the router and run the tests"
juma ask "Add a FastAPI server with GET /health returning {status: ok}, add tests, and return a unified diff" --thread feature-1
juma ask "delete file old.log" --thread cleanup-1
juma reject cleanup-1 --feedback "Keep it for 30 days"

juma remember coding "The router is deterministic unless a model route is available"
juma memories router --crew admin
juma preference-set response_style "concise but warm"
juma preferences
```

Risky actions pause. For a coding change, review the displayed patch and fingerprint, then resume
it with the exact fingerprint:

```powershell
juma approve feature-1 --fingerprint <fingerprint-from-preview>
```

After approval, juma applies only that Git patch and runs `pytest -q`. If the tests fail, the UI
offers a rollback button. The same operation is available from the CLI:

```powershell
juma rollback feature-1 --fingerprint <fingerprint-from-preview>
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

The UI keeps durable chat history, displays Juma's execution plan, selected crew, and routing
confidence, shows activity, previews generated diffs with their fingerprints, supports approval
decisions, runs and displays post-change tests, offers rollback after failures, lets you save useful
answers to shared memory, and searches shared memory in the sidebar.

To enable voice input and spoken responses, set `JUMA_VOICE_ENABLED=true`. The UI then provides a
recording control, and the CLI can transcribe or synthesize audio:

```powershell
juma voice-transcribe .\request.wav
juma voice-speak "Your request is complete." --output .\reply.mp3
```

For private phone access through Tailscale, install Tailscale on the computer and phone, set
`JUMA_SERVER_HOST=0.0.0.0`, and start the UI with `juma-ui --server.address 0.0.0.0`. Open
`http://<computer-tailscale-ip>:8501` on the phone. Keep the computer awake and keep
`JUMA_API_TOKEN` configured. The default server host remains `127.0.0.1`.

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
`JUMA_REQUEST_TIMEOUT`, `JUMA_WORKSPACE_ROOT`, `JUMA_API_TOKEN`, `JUMA_VOICE_ENABLED`,
`JUMA_VOICE_TRANSCRIPTION_MODEL`, `JUMA_VOICE_SPEECH_MODEL`, `JUMA_VOICE_NAME`,
`JUMA_SERVER_HOST`, `JUMA_SERVER_PORT`, `JUMA_AUTO_SETUP`, `JUMA_ENVIRONMENT_TIMEOUT`,
`JUMA_AUTO_REPAIR`, `JUMA_MAX_REPAIR_ATTEMPTS`, `JUMA_AUTO_COMMIT`, `JUMA_AUTO_PUSH`, and
`JUMA_PUSH_REMOTE`.

Autonomous repair is off by default. To enable a bounded local repair-and-commit workflow, use a
clean Git checkout and set `JUMA_AUTO_REPAIR=true`, `JUMA_MAX_REPAIR_ATTEMPTS=3`, and
`JUMA_AUTO_COMMIT=true`. Juma creates a `juma/auto/<fingerprint>` branch, applies the approved
patch, sends failing test output back to the coding crew, and retries only within the original file
scope. It commits only after tests pass. Set `JUMA_AUTO_PUSH=true` to push that committed branch to
`JUMA_PUSH_REMOTE` (default `origin`). Juma never force-pushes, pushes `main`, merges, or deploys.
The remote must already be configured in Git. If the retry limit is reached, the failed patch
remains available for fingerprint-protected rollback.

Checks normally use the project `.venv` or `venv` interpreter when one exists. Set
`JUMA_AUTO_SETUP=true` when a project needs a fresh environment; Juma creates a temporary virtual
environment outside the source tree and installs the project from `pyproject.toml` or
`requirements.txt` before running the fixed tests, lint, or compile checks. Environment setup is
bounded by `JUMA_ENVIRONMENT_TIMEOUT` seconds and is never performed during a research or admin
request unless a coding check is actually run.
