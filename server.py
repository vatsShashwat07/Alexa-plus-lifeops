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
# Official MCP Tool Catalogue (Single Source of Truth)
# -----------------------------------------------------------------------------
MCP_VERSION = "2025-11-25"

OFFICIAL_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "analyze_vitals",
        "description": "Perform cardiovascular risk assessment from spot vitals (systolic, diastolic, heart rate, SpO2).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "systolic": {
                    "type": "integer",
                    "description": "Systolic blood pressure in mmHg.",
                },
                "diastolic": {
                    "type": "integer",
                    "description": "Diastolic blood pressure in mmHg.",
                },
                "heart_rate": {
                    "type": "integer",
                    "description": "Optional heart rate in beats per minute (bpm).",
                },
                "sp_o2": {
                    "type": "integer",
                    "description": "Optional peripheral capillary oxygen saturation percentage (SpO2).",
                },
            },
            "required": ["systolic", "diastolic"],
        },
    },
    {
        "name": "schedule_care_action",
        "description": "Create a scheduled care-action ledger entry with reminder alerts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "description": "Category of action (e.g. medication_reminder, doctor_appointment, lab_test).",
                },
                "details": {
                    "type": "string",
                    "description": "Human-readable description of the scheduled task.",
                },
                "schedule_time": {
                    "type": "string",
                    "description": "ISO-8601 datetime for when the action is scheduled.",
                },
            },
            "required": ["action_type", "details", "schedule_time"],
        },
    },
    {
        "name": "trigger_emergency_protocol",
        "description": "Escalate a health emergency - dispatches simulated EMS, sends caregiver SMS alerts, and activates local alarms.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Short description of the triggering event or symptom.",
                },
                "patient_location": {
                    "type": "string",
                    "description": "Patient's current physical location for dispatch.",
                },
                "vitals_summary": {
                    "type": "string",
                    "description": "Optional compact representation of current vitals.",
                },
                "severity": {
                    "type": "string",
                    "description": "Emergency severity tag (default CRITICAL).",
                    "enum": ["CRITICAL", "HIGH", "MODERATE"],
                },
            },
            "required": ["reason"],
        },
    },
    {
        "name": "get_patient_profile",
        "description": "Retrieve patient's baseline profile, active medications, resting vitals, and emergency contacts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "Unique patient identifier.",
                },
                "user_id": {
                    "type": "string",
                    "description": "Patient identifier alias.",
                },
            },
            "required": [],
        },
    },
]

# Legacy alias referencing the single source of truth
MCP_TOOLS = OFFICIAL_MCP_TOOLS

# Internal dispatcher registry
_TOOL_MAP: dict[str, Any] = {
    "get_patient_profile": tools.get_patient_profile,
    "analyze_vitals": tools.analyze_vitals,
    "schedule_care_action": tools.schedule_care_action,
    "trigger_emergency_protocol": tools.trigger_emergency_protocol,
}

# -----------------------------------------------------------------------------
# Origin Validation & Configuration
# -----------------------------------------------------------------------------
ALLOWED_ORIGINS_ENV = os.getenv("ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = {orig.strip() for orig in ALLOWED_ORIGINS_ENV.split(",") if orig.strip()}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def is_origin_allowed(origin: str | None) -> bool:
    """Validate Origin header against localhost/127.0.0.1/[::1] and ALLOWED_ORIGINS.

    Requests without Origin pass per specification.
    """
    if not origin:
        return True
    try:
        parsed = urlparse(origin)
        host = parsed.hostname or ""
        if host in LOCAL_HOSTS or origin in ALLOWED_ORIGINS or host in ALLOWED_ORIGINS:
            return True
    except Exception:
        pass
    return False


# -----------------------------------------------------------------------------
# Unified Tool Dispatcher (Shared with agent and legacy endpoints)
# -----------------------------------------------------------------------------

def dispatch_tool(tool_name: str, raw_arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a tool with argument normalization, schema validation, and error handling.

    Shared between the official /mcp JSON-RPC endpoint, legacy endpoints, and agent loop.
    Demo only, no auth.

    Returns:
        {
            "success": bool,
            "output": Any,
            "error_message": str | None,
            "error_code": int | None  # -32602 for unknown tool
        }
    """
    if tool_name not in _TOOL_MAP:
        return {
            "success": False,
            "output": None,
            "error_message": f"Unknown tool: '{tool_name}'. Available: {list(_TOOL_MAP.keys())}",
            "error_code": -32602,
        }

    args = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}

    # 1. Normalize aliases with pop()
    patient_id = args.pop("patient_id", None)
    if patient_id and "user_id" not in args:
        args["user_id"] = patient_id

    loc = args.pop("patient_location", None)
    if loc:
        existing = args.get("vitals_summary", "")
        args["vitals_summary"] = f"Location: {loc}. {existing}".strip()

    # 2. Default vitals_summary when absent for emergency protocol
    if tool_name == "trigger_emergency_protocol" and not args.get("vitals_summary"):
        args["vitals_summary"] = "Not provided"

    # 3. Validate required arguments against tool inputSchema
    tool_meta = next((t for t in OFFICIAL_MCP_TOOLS if t["name"] == tool_name), None)
    if tool_meta:
        required_fields = tool_meta.get("inputSchema", {}).get("required", [])
        for field in required_fields:
            if field not in args or args[field] is None or args[field] == "":
                return {
                    "success": False,
                    "output": None,
                    "error_message": f"Missing required argument: '{field}' for tool '{tool_name}'",
                    "error_code": None,  # Not a protocol error; reported in result with isError=True
                }

    # 4. Filter parameters against target function signature
    tool_fn = _TOOL_MAP[tool_name]
    sig = inspect.signature(tool_fn)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    if not has_var_kw:
        accepted_keys = set(sig.parameters.keys())
        filtered_args = {k: v for k, v in args.items() if k in accepted_keys}
    else:
        filtered_args = args

    # 5. Execute target function
    try:
        output = tool_fn(**filtered_args)
        return {
            "success": True,
            "output": output,
            "error_message": None,
            "error_code": None,
        }
    except TypeError as exc:
        return {
            "success": False,
            "output": None,
            "error_message": f"Invalid arguments for tool '{tool_name}': {exc}",
            "error_code": None,
        }
    except Exception as exc:
        logger.exception("Runtime error executing tool %s: %s", tool_name, exc)
        return {
            "success": False,
            "output": None,
            "error_message": f"Tool execution failed: {exc}",
            "error_code": None,
        }


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
