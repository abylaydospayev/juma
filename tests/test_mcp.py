import pytest
from mcp import Client


@pytest.mark.anyio
async def test_memory_mcp_tools(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JUMA_DATA_DIR", str(tmp_path))

    # Import after setting the data directory used by tool handlers.
    from juma.mcp_server import mcp

    async with Client(mcp) as client:
        stored = await client.call_tool(
            "remember", {"crew": "coding", "content": "MCP boundary works"}
        )
        found = await client.call_tool("search_memory", {"query": "boundary", "crew": "admin"})

    assert stored.structured_content == {"id": 1, "stored": True}
    assert found.structured_content["result"][0]["content"] == "MCP boundary works"
