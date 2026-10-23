"""agent.py - Bedrock Converse Orchestrator & Rule-Based Clinical Engine
=====================================================================
Alexa+ LifeOps Autonomous Care Assistant.

Routes user utterances through the AWS Bedrock Converse API for agentic
tool-use reasoning. When Bedrock is unreachable (missing credentials,
network timeout, throttling), the rule-based clinical evaluator takes over
and invokes the same MCP tools via deterministic pattern matching.

Demo only, not a medical device.
"""

from __future__ import annotations

import os
import re
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, EndpointConnectionError
from dotenv import load_dotenv

import tools
from tools import dispatch_tool

load_dotenv()

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)

# -----------------------------------------------------------------------------
# AWS Bedrock Client
# -----------------------------------------------------------------------------
MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "anthropic.claude-3-5-sonnet-20240620-v1:0",
)
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

try:
    bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    logger.info("Bedrock runtime client initialized (model=%s, region=%s)", MODEL_ID, AWS_REGION)
except Exception as exc:  # noqa: BLE001
    bedrock = None
    logger.warning("Could not initialize Bedrock client: %s", exc)

# -----------------------------------------------------------------------------
# System Prompt
# -----------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are the Alexa+ LifeOps Autonomous Care Assistant - a warm, "
    "empathetic, and medically literate voice-first health companion.\n\n"
    "RESPONSIBILITIES:\n"
    "1. Interpret the user's spoken health query and determine intent.\n"
    "2. Decide which MCP clinical tool (if any) is required and call it "
    "   with accurate parameters.\n"
    "3. After receiving tool results, synthesize a concise, reassuring "
    "   Alexa voice utterance (<=3 sentences) suitable for speech output.\n"
    "4. Also produce a short structured summary for the Alexa screen card.\n\n"
    "TONE: Calm, professional, empathetic. Never diagnose - always "
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
    "- Respond ONLY in plain English - no markdown, no bullet lists."
)

# -----------------------------------------------------------------------------
# Bedrock Converse Tool Schemas
# -----------------------------------------------------------------------------
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
                                "description": "Heart rate in beats per minute.",
                            },
                            "sp_o2": {
                                "type": "integer",
                                "description": "Peripheral oxygen saturation percentage.",
                            },
                        },
                        "required": ["systolic", "diastolic"],
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
                    "Escalate a health emergency - dispatches simulated EMS, sends "
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
                            "patient_location": {
                                "type": "string",
                                "description": "Physical location of patient.",
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
                        "required": ["reason"],
                    },
                },
            },
        },
    ],
}


# -----------------------------------------------------------------------------
# Tool Execution via Shared MCP Dispatcher
# -----------------------------------------------------------------------------

def _execute_tool(name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Route tool execution through the unified dispatcher shared with /mcp."""
    res = dispatch_tool(name, params)
    if res["success"]:
        return res["output"] if isinstance(res["output"], dict) else {"result": res["output"]}
    return {"error": res["error_message"]}


# -----------------------------------------------------------------------------
# Bedrock Converse Multi-Turn Loop
# -----------------------------------------------------------------------------

def _bedrock_converse(user_query: str) -> dict[str, Any]:
    """Run Bedrock Converse multi-turn loop (stopReason == 'tool_use', max 5 turns)."""
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"text": user_query}]},
    ]

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
    tool_params: dict[str, Any] = {}
    tool_result: dict[str, Any] = {}
    tool_trace: list[dict[str, Any]] = []

    max_turns = 5
    iteration = 0

    while stop_reason == "tool_use" and iteration < max_turns:
        iteration += 1
        tool_results_content = []

        for blk in assistant_msg.get("content", []):
            if "toolUse" in blk:
                tool_use = blk["toolUse"]
                t_name = tool_use["name"]
                t_params = tool_use.get("input", {})
                t_id = tool_use["toolUseId"]

                logger.info("Bedrock requested tool [%d]: %s(%s)", iteration, t_name, t_params)
                t_result = _execute_tool(t_name, t_params)

                tool_called = t_name
                tool_params = t_params
                tool_result = t_result

                tool_trace.append({
                    "iteration": iteration,
                    "tool": t_name,
                    "parameters": t_params,
                    "result": t_result,
                })

                tool_results_content.append({
                    "toolResult": {
                        "toolUseId": t_id,
                        "content": [{"json": t_result}],
                    }
                })

        if not tool_results_content:
            break

        messages.append({
            "role": "user",
            "content": tool_results_content,
        })

        response = bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            toolConfig=TOOL_CONFIG,
        )
        stop_reason = response["stopReason"]
        assistant_msg = response["output"]["message"]
        messages.append(assistant_msg)

    alexa_speech = " ".join(
        blk["text"] for blk in assistant_msg.get("content", []) if "text" in blk
    ).strip()

    return {
        "tool_called": tool_called,
        "tool_parameters": tool_params,
        "tool_result": tool_result,
        "tool_trace": tool_trace,
        "alexa_speech": alexa_speech,
        "engine": "AWS-Bedrock-Converse",
    }


# -----------------------------------------------------------------------------
# Rule-Based Clinical Evaluator
# -----------------------------------------------------------------------------

_RE_BP = re.compile(r"(\d{2,3})\s*/\s*(\d{2,3})")  # e.g. "140/90"
_RE_HR = re.compile(
    r"(?:heart\s*rate|pulse|hr)\s*"
    r"(?:is|of|at|was|were|reads?|reading|:|=)?\s*"
    r"(?:about|around|roughly|approximately)?\s*"
    r"(\d{2,3})\s*(?:bpm)?"
    r"|"
    r"(\d{2,3})\s*bpm\b",
    re.IGNORECASE,
)
_RE_SPO2 = re.compile(r"(?:sp\s*o\s*2|oxygen|spo2|o2\s*sat)[:\s]*(\d{2,3})", re.IGNORECASE)

_CRITICAL_SYMPTOMS = [
    "chest pain", "can't breathe", "cannot breathe", "difficulty breathing",
    "shortness of breath", "stroke", "unconscious", "heart attack", "seizure",
    "severe bleeding", "choking", "anaphylaxis", "collapsed", "collapse",
    "fainted", "not responsive", "unresponsive",
]

_EXPLICIT_DISPATCH = [
    "call emergency", "call 911", "call an ambulance", "call ambulance",
    "dispatch ems", "dispatch ambulance", "send an ambulance", "send ambulance",
]

_NEGATION_RE = re.compile(
    r"\b(?:no|not|don't|dont|do\s+not|never|without|denies|deny|free\s+of|haven't|havent)\s+(?:\w+\s+){0,3}",
    re.IGNORECASE,
)

_SCHEDULE_PATTERNS = [
    r"\bschedule\b", r"\bremind\b", r"\breminder\b", r"\bappointment\b",
    r"\bbook\b", r"\bset\s+up\b", r"\bplan\b", r"\balarm\b",
]

_PROFILE_PATTERNS = [
    r"\bprofile\b", r"\bmedications?\b", r"\bmedicines?\b",
    r"\ballerg(?:y|ies)\b", r"\bmy\s+info\b", r"\bmy\s+record\b",
    r"\bmy\s+details\b", r"\bemergency\s+contacts?\b",
]

_FEVER_PATTERNS = [r"\bfever\b", r"\btemperature\b", r"\bchills\b", r"\bfeverish\b", r"\bhot\s+flash\b"]
_HEADACHE_PATTERNS = [r"\bheadache\b", r"\bmigraine\b", r"\bhead\s+hurts\b", r"\bhead\s+pain\b"]
_IDENTITY_PATTERNS = [r"\bwho\s+are\s+you\b", r"\bwhat\s+can\s+you\s+do\b", r"\bhelp\b", r"\bintroduce\s+yourself\b"]


def _has_active_emergency(query: str) -> tuple[bool, str]:
    """Detect non-negated emergency symptoms or explicit dispatch commands."""
    q_lower = query.lower()

    for phrase in _EXPLICIT_DISPATCH:
        if re.search(rf"\b{re.escape(phrase)}\b", q_lower):
            return True, phrase.title()

    for symptom in _CRITICAL_SYMPTOMS:
        match = re.search(rf"\b{re.escape(symptom)}\b", q_lower)
        if match:
            start_idx = match.start()
            preceding = q_lower[max(0, start_idx - 30):start_idx]
            if _NEGATION_RE.search(preceding):
                logger.info("Negated symptom '%s' detected - skipping emergency dispatch", symptom)
                continue
            return True, symptom.title()

    return False, ""


def parse_schedule_time(query: str) -> tuple[str, str, bool]:
    """Extract relative/clock time into ISO-8601 string.

    Returns (iso_time, human_description, is_fallback).
    """
    now = datetime.now(timezone.utc)
    q_lower = query.lower()

    day_offset = 1 if "tomorrow" in q_lower else 0

    time_match = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", q_lower)
    if time_match:
        h_str, m_str, am_pm = time_match.group(1), time_match.group(2), time_match.group(3)
        hour = int(h_str)
        minute = int(m_str) if m_str else 0
        if am_pm:
            if am_pm == "pm" and hour < 12:
                hour += 12
            elif am_pm == "am" and hour == 12:
                hour = 0
        elif "tonight" in q_lower and hour < 12:
            hour += 12

        if 0 <= hour <= 23 and 0 <= minute <= 59:
            target_date = (now + timedelta(days=day_offset)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            day_str = "tomorrow" if day_offset == 1 else "today"
            time_fmt = target_date.strftime("%I:%M %p").lstrip("0")
            return target_date.isoformat(), f"{day_str} at {time_fmt}", False

    fallback_dt = (now + timedelta(hours=1)).replace(microsecond=0)
    fallback_fmt = fallback_dt.strftime("%I:%M %p").lstrip("0")
    return fallback_dt.isoformat(), f"one hour from now at {fallback_fmt}", True


def _rule_based_evaluator(user_query: str) -> dict[str, Any]:
    """Deterministic local clinical evaluator."""
    query_lower = user_query.lower()
    tool_called = ""
    tool_params: dict[str, Any] = {}
    tool_result: dict[str, Any] = {}
    alexa_speech = ""
    action_type = "general_query"

    # 1. Emergency detection (highest priority, strictly non-negated)
    is_emergency, emergency_reason = _has_active_emergency(user_query)
    if is_emergency:
        tool_called = "trigger_emergency_protocol"
        vitals_parts = []
        bp_match = _RE_BP.search(user_query)
        if bp_match:
            vitals_parts.append(f"BP {bp_match.group(1)}/{bp_match.group(2)}")
        hr_match = _RE_HR.search(user_query)
        if hr_match:
            vitals_parts.append(f"HR {hr_match.group(1) or hr_match.group(2)}")
        vitals_summary = ", ".join(vitals_parts) if vitals_parts else "Not provided"

        tool_params = {
            "reason": emergency_reason,
            "vitals_summary": vitals_summary,
            "severity": "CRITICAL",
        }
        tool_result = _execute_tool(tool_called, tool_params)
        action_type = "emergency"

        alexa_speech = (
            f"This is a simulated emergency alert (demo, no real services contacted). "
            f"I've initiated the emergency protocol for {emergency_reason}. "
            f"Simulated dispatch confirmation received: help is estimated in 8 minutes. "
            f"Please stay calm and remain in a safe position."
        )

    # 2. Vitals analysis (do not fabricate vitals if unprovided)
    elif _RE_BP.search(user_query) or _RE_HR.search(user_query):
        bp_match = _RE_BP.search(user_query)
        hr_match = _RE_HR.search(user_query)
        spo2_match = _RE_SPO2.search(user_query)

        if bp_match:
            tool_called = "analyze_vitals"
            systolic = int(bp_match.group(1))
            diastolic = int(bp_match.group(2))
            heart_rate = int(hr_match.group(1) or hr_match.group(2)) if hr_match else None
            sp_o2 = int(spo2_match.group(1)) if spo2_match else None

            tool_params = {
                "systolic": systolic,
                "diastolic": diastolic,
            }
            if heart_rate is not None:
                tool_params["heart_rate"] = heart_rate
            if sp_o2 is not None:
                tool_params["sp_o2"] = sp_o2

            tool_result = _execute_tool(tool_called, tool_params)
            action_type = "vitals_check"

            risk = tool_result.get("risk_level", "Normal")
            guidance_list = tool_result.get("clinical_guidance", [])
            guidance_text = guidance_list[0] if guidance_list else "All vitals look good."
            next_action = tool_result.get("recommended_next_action", "Continue routine monitoring")

            bp_desc = tool_result.get("blood_pressure", f"{systolic}/{diastolic} mmHg")
            vitals_desc = f"Blood pressure is {bp_desc}" + (f" and heart rate is {heart_rate} bpm." if heart_rate else ".")

            if risk == "Critical":
                alexa_speech = f"Your vitals need immediate attention. {vitals_desc} {guidance_text} Recommendation: {next_action}."
            elif risk == "Warning":
                alexa_speech = f"I've reviewed your numbers. {vitals_desc} {guidance_text} Recommendation: {next_action}."
            else:
                alexa_speech = f"Great news - your vitals look healthy. {vitals_desc} Keep up the routine monitoring!"
        else:
            # HR was given without BP
            hr_val = int(hr_match.group(1) or hr_match.group(2)) if hr_match else "unknown"
            action_type = "vitals_check"
            alexa_speech = (
                f"I recorded your heart rate reading of {hr_val} bpm. "
                f"Could you also share your blood pressure reading (e.g. 120/80) "
                f"so I can perform a complete cardiovascular risk analysis?"
            )
            tool_result = {"heart_rate_bpm": hr_val, "note": "Pending blood pressure for complete risk analysis"}

    # 3. Schedule / Reminder
    elif any(re.search(pat, query_lower) for pat in _SCHEDULE_PATTERNS):
        tool_called = "schedule_care_action"

        if "medication" in query_lower or "medicine" in query_lower:
            a_type = "medication_reminder"
        elif "appointment" in query_lower or "doctor" in query_lower:
            a_type = "doctor_appointment"
        elif "lab" in query_lower or "test" in query_lower:
            a_type = "lab_test"
        else:
            a_type = "general_reminder"

        iso_time, human_time, is_fallback = parse_schedule_time(user_query)

        tool_params = {
            "action_type": a_type,
            "details": user_query,
            "schedule_time": iso_time,
        }
        tool_result = _execute_tool(tool_called, tool_params)
        action_type = "scheduling"

        if is_fallback:
            alexa_speech = (
                f"I've scheduled that for {human_time} as a default. "
                f"Your action ID is {tool_result.get('action_id', 'N/A')}. "
                f"You will receive a notification 30 minutes prior."
            )
        else:
            alexa_speech = (
                f"Done! I've scheduled your {a_type.replace('_', ' ')} for {human_time}. "
                f"Your action ID is {tool_result.get('action_id', 'N/A')}."
            )

    # 4. Profile / Medication query
    elif any(re.search(pat, query_lower) for pat in _PROFILE_PATTERNS):
        tool_called = "get_patient_profile"
        tool_params = {"user_id": "default_user"}
        tool_result = _execute_tool(tool_called, tool_params)
        action_type = "profile_lookup"

        meds = tool_result.get("active_medications", [])
        med_names = ", ".join(m["name"] for m in meds) if meds else "none on file"
        allergies = ", ".join(tool_result.get("allergies", [])) or "none on file"

        alexa_speech = (
            f"Here is your profile summary: active medications are {med_names}, "
            f"and allergies on file are {allergies}. Would you like more details?"
        )

    # 5. Fever triage
    elif any(re.search(pat, query_lower) for pat in _FEVER_PATTERNS):
        action_type = "symptom_triage"
        alexa_speech = (
            "If you are experiencing elevated body temperature, stay hydrated, "
            "rest in a cool environment, and log your temperature using an oral thermometer. "
            "If your fever exceeds 101 degrees Fahrenheit or persists beyond 48 hours, consult a physician."
        )
        tool_result = {
            "triage_category": "fever_management",
            "action": "SYMPTOM_TRIAGE",
            "guidance": [
                "Stay hydrated with clear fluids.",
                "Rest in a cool room.",
                "Seek medical attention if fever exceeds 101 F or persists > 48h.",
            ],
        }

    # 6. Headache triage
    elif any(re.search(pat, query_lower) for pat in _HEADACHE_PATTERNS):
        action_type = "symptom_triage"
        alexa_speech = (
            "For persistent headaches, rest in a dimly lit room and ensure "
            "you are adequately hydrated. If the headache is sudden and "
            "unusually severe or accompanied by vision changes, seek immediate medical evaluation."
        )
        tool_result = {
            "triage_category": "headache_management",
            "action": "SYMPTOM_TRIAGE",
            "guidance": [
                "Rest in a quiet, dark room.",
                "Hydrate with water.",
                "Seek emergency care if sudden, severe onset or accompanied by neurological symptoms.",
            ],
        }

    # 7. Identity / help
    elif any(re.search(pat, query_lower) for pat in _IDENTITY_PATTERNS):
        action_type = "general_query"
        alexa_speech = (
            "I am Alexa+ LifeOps, an ambient health companion. I monitor vital signs, "
            "analyze cardiovascular readings, schedule medication reminders, and initiate emergency protocols."
        )

    # 8. Negation or unrecognized query
    else:
        action_type = "general_query"
        if _NEGATION_RE.search(query_lower) and any(s in query_lower for s in _CRITICAL_SYMPTOMS):
            alexa_speech = (
                "Understood, I have noted that you are not experiencing those symptoms. "
                "Let me know if you would like me to check your vitals or schedule a reminder."
            )
        else:
            alexa_speech = (
                "I'm here to assist with your health routine. You can share your blood pressure and heart rate, "
                "ask me to schedule a medication reminder, or request a profile summary."
            )

    return {
        "tool_called": tool_called,
        "tool_parameters": tool_params,
        "tool_result": tool_result,
        "alexa_speech": alexa_speech,
        "action_type": action_type,
        "engine": "RuleBasedEngine",
    }


class LocalClinicalEvaluator:
    """Deterministic local clinical evaluation engine (RuleBasedEngine)."""

    @classmethod
    def evaluate(cls, user_query: str) -> dict[str, Any]:
        """Evaluate user query deterministically via the rule-based clinical engine."""
        return _rule_based_evaluator(user_query)

    def __call__(self, user_query: str) -> dict[str, Any]:
        return _rule_based_evaluator(user_query)


# -----------------------------------------------------------------------------
# Public Entry Point
# -----------------------------------------------------------------------------

def run_agent_turn(user_query: str) -> dict[str, Any]:
    """Execute one full agent turn for the given user query.

    Routes through Bedrock Converse when credentials are available.
    On any connectivity or service error, seamlessly hands off to the
    rule-based clinical evaluator with explicit logging.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    fallback_reason: str | None = None

    try:
        if bedrock is None:
            raise NoCredentialsError()  # noqa: TRY301

        logger.info("Routing to Bedrock Converse (%s): %r", MODEL_ID, user_query[:80])
        result = _bedrock_converse(user_query)
        action_type = _classify_action(result.get("tool_called", ""))

        logger.info("Response engine: AWS-Bedrock-Converse (model=%s)", MODEL_ID)
        return {
            "user_query": user_query,
            "tool_called": result["tool_called"],
            "tool_parameters": result["tool_parameters"],
            "tool_result": result["tool_result"],
            "tool_trace": result.get("tool_trace", []),
            "alexa_speech": result["alexa_speech"],
            "action_type": action_type,
            "timestamp": timestamp,
            "engine": result["engine"],
            "fallback_reason": None,
        }

    except (NoCredentialsError, EndpointConnectionError) as exc:
        fallback_reason = f"{type(exc).__name__}: AWS credentials or endpoint unreachable"
        logger.warning("Bedrock unavailable (%s) - switching to RuleBasedEngine", fallback_reason)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        fallback_reason = f"ClientError [{error_code}]: {exc}"
        logger.warning("Bedrock ClientError [%s] - switching to RuleBasedEngine", error_code)
    except Exception as exc:  # noqa: BLE001
        fallback_reason = f"Unexpected error: {exc}"
        logger.warning("Unexpected Bedrock error (%s) - switching to RuleBasedEngine", exc)

    logger.info("Response engine: RuleBasedEngine (reason=%s) processing: %r", fallback_reason, user_query[:80])
    fallback = _rule_based_evaluator(user_query)

    return {
        "user_query": user_query,
        "tool_called": fallback["tool_called"],
        "tool_parameters": fallback["tool_parameters"],
        "tool_result": fallback["tool_result"],
        "tool_trace": [{"tool": fallback["tool_called"], "parameters": fallback["tool_parameters"], "result": fallback["tool_result"]}] if fallback["tool_called"] else [],
        "alexa_speech": fallback["alexa_speech"],
        "action_type": fallback.get("action_type", "general_query"),
        "timestamp": timestamp,
        "engine": fallback["engine"],
        "fallback_reason": fallback_reason,
    }


def _classify_action(tool_name: str) -> str:
    """Map a tool name to a human-readable action category."""
    return {
        "get_patient_profile": "profile_lookup",
        "analyze_vitals": "vitals_check",
        "schedule_care_action": "scheduling",
        "trigger_emergency_protocol": "emergency",
    }.get(tool_name, "general_query")
