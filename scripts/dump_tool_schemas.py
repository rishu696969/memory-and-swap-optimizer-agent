"""Write the tool list exactly as an MCP client discovers it (docs/tool_schemas.json)."""

import json
import secrets
import sys
from pathlib import Path

import anyio

from mso_agent.orchestration.mcp_client import ToolServerConnection


async def main() -> None:
    async with ToolServerConnection(secrets.token_bytes(32)) as conn:
        out = [{"name": t.name, "read_only": t.read_only, "description": t.description,
                "input_schema": t.input_schema} for t in conn.tools]
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/tool_schemas.json")
    path.write_text(json.dumps(out, indent=2))
    print(f"wrote {len(out)} tool schemas to {path}")


anyio.run(main)
