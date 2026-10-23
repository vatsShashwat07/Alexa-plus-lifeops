"""server.py - Alexa+ LifeOps Streamable HTTP MCP Server
========================================================
FastAPI application exposing:
  - Official MCP 2025-11-25 Streamable HTTP endpoint (POST /mcp).
  - Legacy helper endpoints (/mcp/v1/*) for UI companion testing.
  - Alexa simulator endpoint (/api/alexa/interact) for voice/screen assistant.
  - Static file companion UI and health probe.

Demo only, no auth.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import tools
import agent

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)

# -----------------------------------------------------------------------------
# FastAPI App
# -----------------------------------------------------------------------------
app = FastAPI(
    title="Alexa+ LifeOps MCP Server",
    description=(
        "Streamable HTTP MCP Server for the Alexa+ LifeOps Autonomous "
        "Care Assistant - Build, Ship, Shape: Amazon Developer Hackathon 2026."
    ),
    version="1.0.0",
)

# CORS middleware (open for local ecosystem and hackathon companion testing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static file mount for companion UI
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# -----------------------------------------------------------------------------
# Official MCP Tool Catalogue & Dispatcher (from tools.py single source of truth)
# -----------------------------------------------------------------------------
from tools import (
    MCP_VERSION,
    OFFICIAL_MCP_TOOLS,
    MCP_TOOLS,
    _TOOL_MAP,
    dispatch_tool,
)



# -----------------------------------------------------------------------------
# Pydantic Request Models
# -----------------------------------------------------------------------------

class ToolCallRequest(BaseModel):
    """Payload for legacy POST /mcp/v1/tools/call."""
    name: str
    arguments: dict[str, Any] = {}


class AlexaInteractRequest(BaseModel):
    """Payload for POST /api/alexa/interact."""
    prompt: str


def is_origin_allowed(origin: str | None) -> bool:
    """Validate Origin header against localhost/[::1] and ALLOWED_ORIGINS env.

    Requests without an Origin header pass.
    """
    if not origin:
        return True
    try:
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        host = parsed.hostname or ""
    except Exception:
        return False

    allowed_hosts = {"localhost", "127.0.0.1", "::1"}
    env_origins = os.getenv("ALLOWED_ORIGINS", "")
    for o in env_origins.split(","):
        cleaned = o.strip()
        if not cleaned:
            continue
        try:
            o_parsed = urlparse(cleaned if "://" in cleaned else f"http://{cleaned}")
            if o_parsed.hostname:
                allowed_hosts.add(o_parsed.hostname.lower())
        except Exception:
            continue

    return host.lower() in allowed_hosts


# =============================================================================
#  OFFICIAL MCP (2025-11-25 STREAMABLE HTTP) ENDPOINT
# =============================================================================

@app.post("/mcp")
async def mcp_jsonrpc_endpoint(request: Request):
    """Official MCP 2025-11-25 Streamable HTTP JSON-RPC 2.0 endpoint.

    Handles:
      - initialize
      - notifications/initialized (202 Accepted)
      - ping
      - tools/list
      - tools/call
    """
    # 1. Origin validation
    origin = request.headers.get("origin")
    if not is_origin_allowed(origin):
        return Response(
            status_code=403,
            content="Forbidden: Invalid Origin header.",
            media_type="text/plain",
        )

    # 2. Protocol version header check
    proto_version = request.headers.get("mcp-protocol-version")
    if proto_version and proto_version != MCP_VERSION:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32600,
                    "message": f"Unsupported MCP-Protocol-Version: '{proto_version}'. Supported: '{MCP_VERSION}'.",
                },
            },
            media_type="application/json",
        )

    # 3. Parse JSON payload
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Parse error: Invalid JSON payload received.",
                },
            },
            media_type="application/json",
        )

    if not isinstance(body, dict):
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32600,
                    "message": "Invalid Request: Expected JSON object.",
                },
            },
            media_type="application/json",
        )

    has_id = "id" in body
    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})
    if not isinstance(params, dict):
        params = {}

    # 4. JSON-RPC Notifications (requests without an 'id' member)
    if not has_id:
        return Response(status_code=202)

    if method == "notifications/initialized":
        return Response(status_code=202)

    # 5. initialize
    if method == "initialize":
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {
                        "tools": {},
                    },
                    "serverInfo": {
                        "name": "alexa-plus-lifeops",
                        "version": "1.0.0",
                    },
                },
            },
            media_type="application/json",
        )

    # 6. ping
    if method == "ping":
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {},
            },
            media_type="application/json",
        )

    # 7. tools/list
    if method == "tools/list":
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": OFFICIAL_MCP_TOOLS,
                },
            },
            media_type="application/json",
        )

    # 8. tools/call
    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        dispatch_res = dispatch_tool(tool_name or "", tool_args)

        # Unknown tool name -> JSON-RPC error -32602
        if dispatch_res["error_code"] == -32602:
            return JSONResponse(
                status_code=200,
                content={
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32602,
                        "message": dispatch_res["error_message"],
                    },
                },
                media_type="application/json",
            )

        # Validation or execution failure -> isError: True
        if not dispatch_res["success"]:
            return JSONResponse(
                status_code=200,
                content={
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": dispatch_res["error_message"],
                            }
                        ],
                        "isError": True,
                    },
                },
                media_type="application/json",
            )

        # Successful tool execution
        output = dispatch_res["output"]
        result_payload: dict[str, Any] = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(output, default=str),
                }
            ],
            "isError": False,
        }
        if isinstance(output, dict):
            result_payload["structuredContent"] = output

        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": "2.0",
                "id": req_id,
                "result": result_payload,
            },
            media_type="application/json",
        )

    # 9. Method not found
    return JSONResponse(
        status_code=200,
        content={
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: '{method}'",
            },
        },
        media_type="application/json",
    )


@app.get("/mcp", include_in_schema=False)
async def mcp_get_disallowed():
    """GET /mcp is disallowed under Streamable HTTP specification; use POST /mcp."""
    return Response(
        status_code=405,
        content="Method Not Allowed. Use POST /mcp for JSON-RPC 2.0 requests.",
        media_type="text/plain",
        headers={"Allow": "POST"},
    )


# =============================================================================
#  LEGACY / EXTENSION STREAMABLE HTTP ENDPOINTS (for UI & backward compat)
# =============================================================================

def _build_manifest() -> dict[str, Any]:
    """Construct the full MCP manifest payload reusing OFFICIAL_MCP_TOOLS."""
    return {
        "mcp_version": MCP_VERSION,
        "protocol": "streamable-http",
        "server_info": {
            "name": "alexa-plus-lifeops-mcp",
            "version": "1.0.0",
        },
        "capabilities": {
            "tools": {"listChanged": True},
        },
        "tools": OFFICIAL_MCP_TOOLS,
    }


@app.get("/mcp/v1/manifest")
async def mcp_manifest():
    """Legacy MCP Manifest endpoint."""
    return _build_manifest()


@app.get("/mcp/v1/tools")
async def mcp_tools_list():
    """Legacy MCP Tool Listing endpoint."""
    return _build_manifest()


@app.post("/mcp/v1/tools/call")
async def mcp_tool_call(request: ToolCallRequest):
    """Execute an MCP tool by name via legacy envelope."""
    dispatch_res = dispatch_tool(request.name, request.arguments)
    if not dispatch_res["success"]:
        if dispatch_res["error_code"] == -32602:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "tool_not_found",
                    "message": dispatch_res["error_message"],
                },
            )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_arguments",
                "message": dispatch_res["error_message"],
            },
        )

    return {
        "tool": request.name,
        "result": dispatch_res["output"],
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# =============================================================================
#  ALEXA SIMULATOR ENDPOINT (ECHO SHOW COMPANION UI)
# =============================================================================

@app.get("/", include_in_schema=False)
async def serve_index():
    """Serve companion UI from static/index.html."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.isfile(index_path):
        return JSONResponse(
            status_code=200,
            content={
                "message": "Alexa+ LifeOps MCP Server is running.",
                "docs": "/docs",
                "mcp_endpoint": "/mcp",
            },
        )
    return FileResponse(index_path, media_type="text/html")


@app.get("/health")
async def health_check():
    """Service health probe."""
    return {
        "status": "healthy",
        "service": "alexa-plus-lifeops",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/alexa/interact")
async def alexa_interact(request: AlexaInteractRequest):
    """Alexa+ voice-assistant interaction endpoint.

    Accepts natural-language prompt, routes through agent orchestrator,
    and returns execution payload including speech output and cards.
    """
    if not request.prompt or not request.prompt.strip():
        raise HTTPException(
            status_code=422,
            detail={
                "error": "empty_prompt",
                "message": "The 'prompt' field must contain a non-empty string.",
            },
        )

    try:
        result = agent.run_agent_turn(request.prompt.strip())
    except Exception as exc:
        logger.exception("Agent turn failed for prompt: %r", request.prompt)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "agent_error",
                "message": str(exc),
            },
        ) from exc

    return result


# -----------------------------------------------------------------------------
# Entry Point (demo only, no auth)
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("server:app", host=host, port=port, reload=True)
