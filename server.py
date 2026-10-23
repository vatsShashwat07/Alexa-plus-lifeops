"""
server.py — Alexa+ LifeOps Streamable HTTP MCP Server
========================================================
FastAPI application exposing:
  - MCP v2025-11-25 Streamable HTTP manifest, tool listing, and tool execution.
  - Alexa simulator endpoint for interactive voice-assistant testing.
  - Static file serving for the companion UI.
  - Health-check endpoint.
"""

from __future__ import annotations

import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import tools
import agent

# ── Logging ──────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)

# ── FastAPI App ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="Alexa+ LifeOps MCP Server",
    description=(
        "Streamable HTTP MCP Server for the Alexa+ LifeOps Autonomous "
        "Care Assistant — Build, Ship, Shape: Amazon Developer Hackathon 2026."
    ),
    version="1.0.0",
)

# ── CORS (allow all origins for hackathon / Alexa+ ecosystem) ────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static Files ─────────────────────────────────────────────────────────────
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ── MCP Tool Schemas (2025-11-25 Streamable HTTP spec) ──────────────────────
MCP_VERSION = "2025-11-25"

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_patient_profile",
        "description": (
            "Retrieve the patient's medical baseline including allergies, "
            "active medications, resting vitals, and emergency contacts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "Unique patient identifier.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "analyze_vitals",
        "description": (
            "Perform a cardiovascular risk assessment from spot vitals "
            "(heart rate, blood pressure, SpO2)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "heart_rate": {
                    "type": "integer",
                    "description": "Heart rate in beats per minute.",
                },
                "systolic": {
                    "type": "integer",
                    "description": "Systolic blood pressure in mmHg.",
                },
                "diastolic": {
                    "type": "integer",
                    "description": "Diastolic blood pressure in mmHg.",
                },
                "sp_o2": {
                    "type": "integer",
                    "description": "Peripheral oxygen saturation percentage.",
                },
            },
            "required": ["heart_rate", "systolic", "diastolic"],
        },
    },
    {
        "name": "schedule_care_action",
        "description": (
            "Create a scheduled care-action ledger entry with reminder "
            "alerts (e.g. medication reminder, doctor appointment, lab test)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "description": (
                        "Category of action, e.g. 'medication_reminder', "
                        "'doctor_appointment', 'lab_test'."
                    ),
                },
                "details": {
                    "type": "string",
                    "description": "Human-readable description of the scheduled task.",
                },
                "schedule_time": {
                    "type": "string",
                    "description": "ISO-8601 datetime for when the action is due.",
                },
            },
            "required": ["action_type", "details", "schedule_time"],
        },
    },
    {
        "name": "trigger_emergency_protocol",
        "description": (
            "Escalate a health emergency — dispatches EMS, sends caregiver "
            "SMS alerts, and activates local alarms."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Short description of the triggering event.",
                },
                "vitals_summary": {
                    "type": "string",
                    "description": "Compact representation of the patient's current vitals.",
                },
                "severity": {
                    "type": "string",
                    "description": "Severity tag (default CRITICAL).",
                    "enum": ["CRITICAL", "HIGH", "MODERATE"],
                },
            },
            "required": ["reason", "vitals_summary"],
        },
    },
]

# Tool dispatcher (same map used by agent.py)
_TOOL_MAP = {
    "get_patient_profile": tools.get_patient_profile,
    "analyze_vitals": tools.analyze_vitals,
    "schedule_care_action": tools.schedule_care_action,
    "trigger_emergency_protocol": tools.trigger_emergency_protocol,
}


# ── Pydantic Request Models ─────────────────────────────────────────────────

class ToolCallRequest(BaseModel):
    """Payload for POST /mcp/v1/tools/call."""
    name: str
    arguments: dict[str, Any] = {}


class AlexaInteractRequest(BaseModel):
    """Payload for POST /api/alexa/interact."""
    prompt: str


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════

# ── Root: serve static UI ────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def serve_index():
    """Serve the companion UI from static/index.html."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.isfile(index_path):
        return JSONResponse(
            status_code=200,
            content={
                "message": "Alexa+ LifeOps MCP Server is running.",
                "docs": "/docs",
                "mcp_manifest": "/mcp/v1/manifest",
                "note": "Place an index.html in the /static directory to enable the UI.",
            },
        )
    return FileResponse(index_path, media_type="text/html")


# ── Health Check ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Service health probe."""
    return {
        "status": "healthy",
        "service": "alexa-plus-lifeops",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  OFFICIAL MCP JSON-RPC 2.0 (2025-11-25 STREAMABLE HTTP)
# ══════════════════════════════════════════════════════════════════════════════

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
                    "description": "Heart rate in beats per minute (bpm).",
                },
                "sp_o2": {
                    "type": "integer",
                    "description": "Peripheral capillary oxygen saturation percentage (SpO2).",
                },
            },
            "required": ["systolic", "diastolic", "heart_rate"],
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
                    "description": "Category of action (e.g., medication_reminder, doctor_appointment, lab_test).",
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
        "description": "Escalate a health emergency — dispatches EMS, sends caregiver SMS alerts, and activates local alarms.",
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


@app.post("/mcp")
async def mcp_jsonrpc_endpoint(request: Request):
    """Official MCP (Model Context Protocol 2025-11-25) Streamable HTTP endpoint.

    Strict JSON-RPC 2.0 implementation supporting:
      - initialize
      - notifications (e.g. notifications/initialized) -> 202 Accepted
      - tools/list
      - tools/call -> envelope with content and isError: false
    """
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

    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})
    if not isinstance(params, dict):
        params = {}

    # 1. JSON-RPC Notifications (requests without an 'id' or notifications/* methods)
    if method == "notifications/initialized" or (method and method.startswith("notifications/")):
        return Response(status_code=202)

    if "id" not in body and method not in ("initialize", "tools/list", "tools/call"):
        return Response(status_code=202)

    # 2. initialize
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

    # 3. tools/list
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

    # 4. tools/call
    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        if not tool_name or tool_name not in _TOOL_MAP:
            return JSONResponse(
                status_code=200,
                content={
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method or tool '{tool_name}' not found. Available: {list(_TOOL_MAP.keys())}",
                    },
                },
                media_type="application/json",
            )

        # Normalize argument aliases
        if tool_name == "get_patient_profile":
            if "patient_id" in tool_args and "user_id" not in tool_args:
                tool_args["user_id"] = tool_args["patient_id"]
        elif tool_name == "trigger_emergency_protocol":
            if "patient_location" in tool_args and "vitals_summary" not in tool_args:
                tool_args["vitals_summary"] = f"Location: {tool_args['patient_location']}"

        try:
            tool_fn = _TOOL_MAP[tool_name]
            output = tool_fn(**tool_args)
        except TypeError as exc:
            return JSONResponse(
                status_code=200,
                content={
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32602,
                        "message": f"Invalid arguments for tool '{tool_name}': {exc}",
                    },
                },
                media_type="application/json",
            )
        except Exception as exc:
            logger.exception("MCP tools/call error for tool %s: %s", tool_name, exc)
            return JSONResponse(
                status_code=200,
                content={
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32603,
                        "message": f"Internal error executing tool '{tool_name}': {exc}",
                    },
                },
                media_type="application/json",
            )

        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": str(output),
                        }
                    ],
                    "isError": False,
                },
            },
            media_type="application/json",
        )

    # 5. Method not found
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
async def mcp_service_info(request: Request):
    """Information and SSE endpoint for the Streamable HTTP MCP service."""
    accept = request.headers.get("accept", "")
    if "text/event-stream" in accept:
        async def event_generator():
            yield "event: endpoint\ndata: /mcp\n\n"
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    await asyncio.sleep(5)
                    yield ": ping\n\n"
            except (asyncio.CancelledError, GeneratorExit):
                pass

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    return JSONResponse(
        content={
            "service": "alexa-plus-lifeops",
            "protocol": "mcp-streamable-http",
            "version": "2025-11-25",
            "rpc_endpoint": "/mcp",
            "supported_methods": ["initialize", "notifications/initialized", "tools/list", "tools/call"],
        },
        media_type="application/json",
    )


# ══════════════════════════════════════════════════════════════════════════════
#  LEGACY / EXTENSION STREAMABLE HTTP ENDPOINTS (for UI & backward compat)
# ══════════════════════════════════════════════════════════════════════════════

def _build_manifest() -> dict[str, Any]:
    """Construct the full MCP manifest payload."""
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
        "tools": MCP_TOOLS,
    }


@app.get("/mcp/v1/manifest")
async def mcp_manifest():
    """
    MCP Manifest (2025-11-25 Streamable HTTP spec).

    Returns server identity, capabilities, and the full tool catalogue.
    """
    return _build_manifest()


@app.get("/mcp/v1/tools")
async def mcp_tools_list():
    """
    MCP Tool Listing — identical payload to the manifest.

    Provided as a convenience alias per the Streamable HTTP spec.
    """
    return _build_manifest()


# ── Tool Execution ───────────────────────────────────────────────────────────

@app.post("/mcp/v1/tools/call")
async def mcp_tool_call(request: ToolCallRequest):
    """
    Execute an MCP tool by name.

    Accepts:
        ``{"name": "<tool_name>", "arguments": {...}}``

    Returns the tool's JSON result wrapped in a standard MCP response
    envelope.
    """
    tool_fn = _TOOL_MAP.get(request.name)
    if tool_fn is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "tool_not_found",
                "message": f"No tool named '{request.name}'. "
                           f"Available: {list(_TOOL_MAP.keys())}",
            },
        )

    try:
        result = tool_fn(**request.arguments)
    except TypeError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_arguments",
                "message": str(exc),
                "tool": request.name,
                "arguments_received": request.arguments,
            },
        ) from exc
    except Exception as exc:
        logger.exception("Tool execution failed: %s", request.name)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "tool_execution_error",
                "message": str(exc),
                "tool": request.name,
            },
        ) from exc

    return {
        "tool": request.name,
        "result": result,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  ALEXA SIMULATOR ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/alexa/interact")
async def alexa_interact(request: AlexaInteractRequest):
    """Alexa+ voice-assistant interaction endpoint.

    Accepts a natural-language prompt, routes it through the Bedrock
    agent orchestrator (with rule-based evaluator for local processing),
    and returns the complete execution payload including speech output
    and screen cards.
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


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
