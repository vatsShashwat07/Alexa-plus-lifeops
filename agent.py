"""
agent.py — Bedrock Converse Orchestrator & Rule-Based Clinical Engine
=====================================================================
Alexa+ LifeOps Autonomous Care Assistant.

Routes user utterances through the AWS Bedrock Converse API for agentic
tool-use reasoning.  When Bedrock is unreachable (missing credentials,
network timeout, throttling), the rule-based clinical evaluator takes over
and invokes the same MCP tools via deterministic pattern matching.
"""

from __future__ import annotations

import os
import re
import json
import logging
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, EndpointConnectionError
from dotenv import load_dotenv

import tools  # local MCP tool module

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)

# ── AWS Bedrock Client ──────────────────────────────────────────────────────
MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "anthropic.claude-3-5-sonnet-20240620-v1:0",
)
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

try:
    bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    logger.info("Bedrock runtime client initialised (region=%s)", AWS_REGION)
except Exception as exc:  # noqa: BLE001
    bedrock = None
    logger.warning("Could not initialise Bedrock client: %s", exc)

# ── System Prompt ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are the Alexa+ LifeOps Autonomous Care Assistant — a warm, "
    "empathetic, and medically literate voice-first health companion.\n\n"
    "RESPONSIBILITIES:\n"
    "1. Interpret the user's spoken health query and determine intent.\n"
    "2. Decide which MCP clinical tool (if any) is required and call it "
    "   with accurate parameters.\n"
    "3. After receiving tool results, synthesise a concise, reassuring "
    "   Alexa voice utterance (≤3 sentences) suitable for speech output.\n"
    "4. Also produce a short structured summary for the Alexa screen card.\n\n"
    "TONE: Calm, professional, empathetic.  Never diagnose — always "
    "recommend professional follow-up for abnormal findings.\n\n"
    "RULES:\n"
    "- If the user reports vitals, ALWAYS call `analyze_vitals`.\n"
    "- If the user asks about their profile, medications, or allergies, "
    "  call `get_patient_profile`.\n"
    "- If the user wants to schedule a reminder, appointment, or action, "
    "  call `schedule_care_action`.\n"
    "- If the user reports a medical emergency (chest pain, stroke symptoms, "
    "  severe breathing difficulty, loss of consciousness), call "
    "  `trigger_emergency_protocol` immediately.\n"
    "- Respond ONLY in plain English — no markdown, no bullet lists."
)

# ── Bedrock Converse Tool Schemas ────────────────────────────────────────────
TOOL_CONFIG: dict[str, Any] = {
    "tools": [
        {
            "toolSpec": {
                "name": "get_patient_profile",
                "description": (
                    "Retrieve the patient's medical baseline including "
                    "allergies, active medications, resting vitals, and "
                    "emergency contacts."
                ),
                "inputSchema": {
                    "json": {
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
            },
        },
        {
            "toolSpec": {
                "name": "analyze_vitals",
                "description": (
                    "Perform a cardiovascular risk assessment from spot "
                    "vitals (heart rate, blood pressure, SpO2)."
                ),
                "inputSchema": {
                    "json": {
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
            },
        },
        {
            "toolSpec": {
                "name": "schedule_care_action",
                "description": (
                    "Create a scheduled care-action ledger entry with "
                    "reminder alerts (e.g. medication reminder, doctor "
                    "appointment, lab test)."
                ),
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "action_type": {
                                "type": "string",
                                "description": (
                                    "Category of action, e.g. "
                                    "'medication_reminder', "
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
            },
        },
        {
            "toolSpec": {
                "name": "trigger_emergency_protocol",
                "description": (
                    "Escalate a health emergency — dispatches EMS, sends "
                    "caregiver SMS alerts, and activates local alarms."
                ),
                "inputSchema": {
                    "json": {
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
            },
        },
    ],
}

# ── Tool Dispatcher ─────────────────────────────────────────────────────────
TOOL_MAP = {
    "get_patient_profile": tools.get_patient_profile,
    "analyze_vitals": tools.analyze_vitals,
    "schedule_care_action": tools.schedule_care_action,
    "trigger_emergency_protocol": tools.trigger_emergency_protocol,
}


def _execute_tool(name: str, params: dict) -> dict:
    """Dispatch a tool call by name and return its result dict."""
    fn = TOOL_MAP.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(**params)
    except TypeError as exc:
        return {"error": f"Parameter mismatch for {name}: {exc}"}


# ── Bedrock Converse Loop ───────────────────────────────────────────────────

def _bedrock_converse(user_query: str) -> dict:
    """
    Run a full Bedrock Converse turn:
      1. Send user query + toolConfig.
      2. If the model returns ``tool_use``, execute the tool and feed the
         result back for a final natural-language response.

    Raises on any boto3 / connectivity error so the caller can fall back.
    """
    messages: list[dict] = [
        {"role": "user", "content": [{"text": user_query}]},
    ]

    # ── First Converse call ──────────────────────────────────────────────
    response = bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=messages,
        toolConfig=TOOL_CONFIG,
    )

    stop_reason = response["stopReason"]
    assistant_msg = response["output"]["message"]
    messages.append(assistant_msg)

    tool_called: str = ""
    tool_params: dict = {}
    tool_result: dict = {}

    # ── Handle tool_use ──────────────────────────────────────────────────
    if stop_reason == "tool_use":
        # Extract the first tool-use block from the assistant response
        tool_use_block = next(
            (blk for blk in assistant_msg["content"] if "toolUse" in blk),
            None,
        )
        if tool_use_block:
            tool_use = tool_use_block["toolUse"]
            tool_called = tool_use["name"]
            tool_params = tool_use.get("input", {})
            tool_id = tool_use["toolUseId"]

            logger.info("Bedrock requested tool: %s(%s)", tool_called, tool_params)
            tool_result = _execute_tool(tool_called, tool_params)

            # Feed tool result back
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "toolResult": {
                                "toolUseId": tool_id,
                                "content": [{"json": tool_result}],
                            },
                        },
                    ],
                },
            )

            # ── Second Converse call (final answer) ──────────────────────
            response = bedrock.converse(
                modelId=MODEL_ID,
                system=[{"text": SYSTEM_PROMPT}],
                messages=messages,
                toolConfig=TOOL_CONFIG,
            )
            assistant_msg = response["output"]["message"]

    # ── Extract final speech text ────────────────────────────────────────
    alexa_speech = " ".join(
        blk["text"] for blk in assistant_msg["content"] if "text" in blk
    ).strip()

    return {
        "tool_called": tool_called,
        "tool_parameters": tool_params,
        "tool_result": tool_result,
        "alexa_speech": alexa_speech,
        "engine": "AWS-Bedrock-Converse",
    }


# ── Rule-Based Clinical Evaluator ───────────────────────────────────────────

# Pre-compiled patterns for clinical pattern extraction
_RE_BP = re.compile(
    r"(\d{2,3})\s*/\s*(\d{2,3})",  # e.g. "140/90"
)
_RE_HR = re.compile(
    r"(?:heart\s*rate|pulse|hr)\s*"           # keyword anchor
    r"(?:is|of|at|was|were|reads?|reading|:|=)?\s*"  # optional connector
    r"(?:about|around|roughly|approximately)?\s*"     # optional qualifier
    r"(\d{2,3})\s*(?:bpm)?"                   # capture digits + optional "bpm"
    r"|"
    r"(\d{2,3})\s*bpm\b",                     # standalone "NNN bpm"
    re.IGNORECASE,
)
_RE_SPO2 = re.compile(
    r"(?:sp\s*o\s*2|oxygen|spo2|o2\s*sat)[:\s]*(\d{2,3})",
    re.IGNORECASE,
)

_EMERGENCY_KEYWORDS = [
    "chest pain", "can't breathe", "cannot breathe", "difficulty breathing",
    "stroke", "unconscious", "heart attack", "seizure", "severe bleeding",
    "choking", "anaphylaxis", "collapse", "fainted", "not responsive",
    "emergency",
]

_SCHEDULE_KEYWORDS = [
    "schedule", "remind", "reminder", "appointment", "book",
    "set up", "plan", "alarm",
]

_PROFILE_KEYWORDS = [
    "profile", "medication", "medications", "medicine", "medicines",
    "allergy", "allergies", "my info", "my record", "my details",
    "emergency contact",
]

# Symptom-triage keyword groups (matched before generic fallback)
_FEVER_KEYWORDS = ["fever", "temperature", "chills", "feverish", "hot flash"]
_HEADACHE_KEYWORDS = ["headache", "migraine", "head hurts", "head pain"]
_IDENTITY_KEYWORDS = ["who are you", "what can you do", "help", "what do you do", "introduce yourself"]


def _rule_based_evaluator(user_query: str) -> dict:
    """Edge inference & deterministic dispatch layer.

    Parses the user utterance for clinical patterns (BP readings,
    heart-rate values, emergency phrases, symptom keywords) and invokes
    the matching MCP tool.  Builds a natural Alexa-style voice response
    from the structured tool output.
    """
    query_lower = user_query.lower()
    tool_called = ""
    tool_params: dict = {}
    tool_result: dict = {}
    alexa_speech = ""
    action_type = "general_query"

    # ── 1. Emergency detection (highest priority) ────────────────────────
    if any(kw in query_lower for kw in _EMERGENCY_KEYWORDS):
        tool_called = "trigger_emergency_protocol"
        # Try to extract vitals from the query for the summary
        vitals_parts = []
        bp_match = _RE_BP.search(user_query)
        if bp_match:
            vitals_parts.append(f"BP {bp_match.group(1)}/{bp_match.group(2)}")
        hr_match = _RE_HR.search(user_query)
        if hr_match:
            vitals_parts.append(f"HR {hr_match.group(1) or hr_match.group(2)}")
        vitals_summary = ", ".join(vitals_parts) if vitals_parts else "Not provided"

        # Determine reason from keywords
        reason = "Medical emergency reported by patient"
        for kw in _EMERGENCY_KEYWORDS:
            if kw in query_lower:
                reason = kw.title()
                break

        tool_params = {
            "reason": reason,
            "vitals_summary": vitals_summary,
            "severity": "CRITICAL",
        }
        tool_result = _execute_tool(tool_called, tool_params)
        action_type = "emergency"

        alexa_speech = (
            f"I've activated the emergency protocol. "
            f"Emergency services have been notified and your caregivers "
            f"are being contacted right now. "
            f"Help is on the way — estimated arrival is "
            f"{tool_result.get('dispatch_status', {}).get('estimated_arrival_min', 8)} minutes. "
            f"Please stay calm and stay where you are."
        )

    # ── 2. Vitals analysis ───────────────────────────────────────────────
    elif _RE_BP.search(user_query) or _RE_HR.search(user_query):
        tool_called = "analyze_vitals"
        bp_match = _RE_BP.search(user_query)
        hr_match = _RE_HR.search(user_query)
        spo2_match = _RE_SPO2.search(user_query)

        systolic = int(bp_match.group(1)) if bp_match else 120
        diastolic = int(bp_match.group(2)) if bp_match else 80
        heart_rate = int(hr_match.group(1) or hr_match.group(2)) if hr_match else 72
        sp_o2 = int(spo2_match.group(1)) if spo2_match else 98

        tool_params = {
            "heart_rate": heart_rate,
            "systolic": systolic,
            "diastolic": diastolic,
            "sp_o2": sp_o2,
        }
        tool_result = _execute_tool(tool_called, tool_params)
        action_type = "vitals_check"

        risk = tool_result.get("risk_level", "Normal")
        guidance_list = tool_result.get("clinical_guidance", [])
        guidance_text = guidance_list[0] if guidance_list else "All vitals look good."
        next_action = tool_result.get("recommended_next_action", "Continue routine monitoring")

        if risk == "Critical":
            alexa_speech = (
                f"Your vitals need immediate attention. "
                f"{guidance_text} "
                f"I strongly recommend: {next_action}."
            )
        elif risk == "Warning":
            alexa_speech = (
                f"I've reviewed your numbers. "
                f"{guidance_text} "
                f"My recommendation is to {next_action.lower()}."
            )
        else:
            alexa_speech = (
                f"Great news — your vitals look healthy. "
                f"Blood pressure is {tool_result.get('blood_pressure', 'normal')} "
                f"and heart rate is {heart_rate} bpm. Keep up the good work!"
            )

    # ── 3. Schedule / Reminder ───────────────────────────────────────────
    elif any(kw in query_lower for kw in _SCHEDULE_KEYWORDS):
        tool_called = "schedule_care_action"

        # Determine action type from context
        if "medication" in query_lower or "medicine" in query_lower:
            a_type = "medication_reminder"
        elif "appointment" in query_lower or "doctor" in query_lower:
            a_type = "doctor_appointment"
        elif "lab" in query_lower or "test" in query_lower:
            a_type = "lab_test"
        else:
            a_type = "general_reminder"

        schedule_time = datetime.now(timezone.utc).isoformat()

        tool_params = {
            "action_type": a_type,
            "details": user_query,
            "schedule_time": schedule_time,
        }
        tool_result = _execute_tool(tool_called, tool_params)
        action_type = "scheduling"

        alexa_speech = (
            f"Done! I've scheduled that for you. "
            f"Your action ID is {tool_result.get('action_id', 'N/A')}. "
            f"You'll get a reminder 30 minutes before and again at 5 minutes before."
        )

    # ── 4. Profile / Medication query ────────────────────────────────────
    elif any(kw in query_lower for kw in _PROFILE_KEYWORDS):
        tool_called = "get_patient_profile"
        tool_params = {"user_id": "default_user"}
        tool_result = _execute_tool(tool_called, tool_params)
        action_type = "profile_lookup"

        meds = tool_result.get("active_medications", [])
        med_names = ", ".join(m["name"] for m in meds) if meds else "none on file"
        allergies = ", ".join(tool_result.get("allergies", [])) or "none on file"

        alexa_speech = (
            f"Here's your profile summary. "
            f"You're currently on {med_names}. "
            f"Your known allergies are {allergies}. "
            f"Would you like more details?"
        )

    # ── 5. Fever / temperature triage ──────────────────────────────────────
    elif any(kw in query_lower for kw in _FEVER_KEYWORDS):
        action_type = "symptom_triage"
        alexa_speech = (
            "If you're experiencing an elevated temperature, stay hydrated, "
            "rest in a cool environment, and log your temperature using an "
            "oral thermometer. If your fever exceeds 101 degrees Fahrenheit "
            "or persists beyond 48 hours, consult a physician."
        )
        tool_result = {
            "triage_category": "fever_management",
            "action": "SYMPTOM_TRIAGE",
            "guidance": [
                "Stay hydrated with clear fluids.",
                "Rest in a cool, well-ventilated room.",
                "Log temperature every 4 hours with an oral thermometer.",
                "Seek physician consultation if fever > 101°F or persists > 48h.",
            ],
        }

    # ── 6. Headache / migraine triage ────────────────────────────────────
    elif any(kw in query_lower for kw in _HEADACHE_KEYWORDS):
        action_type = "symptom_triage"
        alexa_speech = (
            "For persistent headaches, rest in a dimly lit room and ensure "
            "you are adequately hydrated. If the headache is sudden and "
            "unusually severe or accompanied by vision changes, seek "
            "immediate evaluation."
        )
        tool_result = {
            "triage_category": "headache_management",
            "action": "SYMPTOM_TRIAGE",
            "guidance": [
                "Rest in a quiet, dimly lit room.",
                "Hydrate with water or electrolyte solution.",
                "Apply a cool compress to the forehead.",
                "Seek emergency care if sudden onset, worst-ever severity, or visual disturbance.",
            ],
        }

    # ── 7. Identity / capabilities query ─────────────────────────────────
    elif any(kw in query_lower for kw in _IDENTITY_KEYWORDS):
        action_type = "general_query"
        alexa_speech = (
            "I am Alexa+ LifeOps, an ambient health companion. I monitor "
            "vital signs, analyze cardiovascular trends, schedule medication "
            "reminders, and initiate emergency safety protocols."
        )

    # ── 8. Unrecognized — contextual health response ─────────────────────
    else:
        action_type = "general_query"
        # Provide varied, supportive responses instead of a static message
        _contextual_responses = [
            (
                "I'm here to help you stay on top of your health. You can "
                "tell me your blood pressure and heart rate for a quick "
                "analysis, ask me to schedule a medication reminder, or "
                "say 'emergency' if you need immediate assistance."
            ),
            (
                "I didn't catch a specific health request, but I'm standing by. "
                "Try saying something like 'my blood pressure is 130 over 85' "
                "or 'schedule my Metformin reminder for 8 PM'."
            ),
            (
                "I'm your ambient care assistant — always listening for vitals, "
                "medication schedules, or emergencies. Let me know how I can "
                "help you right now."
            ),
        ]
        # Rotate based on query length for deterministic variety
        alexa_speech = _contextual_responses[len(user_query) % len(_contextual_responses)]

    return {
        "tool_called": tool_called,
        "tool_parameters": tool_params,
        "tool_result": tool_result,
        "alexa_speech": alexa_speech,
        "action_type": action_type,
        "engine": "RuleBasedEngine",
    }


# ── Public Entry Point ──────────────────────────────────────────────────────

def run_agent_turn(user_query: str) -> dict:
    """Execute one full agent turn for the given user query.

    Routes through Bedrock Converse when credentials are available.
    On any connectivity or service error, seamlessly hands off to the
    rule-based clinical evaluator for deterministic local processing.

    Args:
        user_query: The raw natural-language utterance from the user.

    Returns:
        A structured dict containing the query, tool invocation details,
        Alexa speech output, action type, timestamp, and engine used.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    action_type = "general_query"

    try:
        if bedrock is None:
            raise NoCredentialsError()  # noqa: TRY301

        logger.info("Routing to Bedrock Converse: %r", user_query[:80])
        result = _bedrock_converse(user_query)
        action_type = _classify_action(result.get("tool_called", ""))

        return {
            "user_query": user_query,
            "tool_called": result["tool_called"],
            "tool_parameters": result["tool_parameters"],
            "tool_result": result["tool_result"],
            "alexa_speech": result["alexa_speech"],
            "action_type": action_type,
            "timestamp": timestamp,
            "engine": result["engine"],
        }

    except (NoCredentialsError, EndpointConnectionError) as exc:
        logger.warning("Bedrock unavailable (%s) — switching to local evaluator.", type(exc).__name__)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        logger.warning("Bedrock ClientError [%s] — switching to local evaluator.", error_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unexpected Bedrock error (%s) — switching to local evaluator.", exc)

    # ── Rule-based evaluator path ────────────────────────────────────────
    logger.info("RuleBasedEngine processing: %r", user_query[:80])
    fallback = _rule_based_evaluator(user_query)

    return {
        "user_query": user_query,
        "tool_called": fallback["tool_called"],
        "tool_parameters": fallback["tool_parameters"],
        "tool_result": fallback["tool_result"],
        "alexa_speech": fallback["alexa_speech"],
        "action_type": fallback.get("action_type", "general_query"),
        "timestamp": timestamp,
        "engine": fallback["engine"],
    }


def _classify_action(tool_name: str) -> str:
    """Map a tool name to a human-readable action category."""
    return {
        "get_patient_profile": "profile_lookup",
        "analyze_vitals": "vitals_check",
        "schedule_care_action": "scheduling",
        "trigger_emergency_protocol": "emergency",
    }.get(tool_name, "general_query")


# ── CLI Quick-Test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_queries = [
        "My blood pressure is 145/95 and heart rate is 110",
        "What medications am I on?",
        "Schedule a doctor appointment for tomorrow at 3 PM",
        "I'm having severe chest pain and can't breathe",
        "Hello, how are you?",
    ]
    for q in test_queries:
        print(f"\n{'='*70}")
        print(f"QUERY: {q}")
        print(f"{'='*70}")
        result = run_agent_turn(q)
        print(json.dumps(result, indent=2, default=str))
