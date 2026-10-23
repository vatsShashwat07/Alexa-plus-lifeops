"""Demo only, not a medical device, no real EMS/SMS integration.

tools.py - Clinical & Lifestyle Health Tools for MCP Server
=============================================================
Deterministic, pure-function toolkit exposing patient profile retrieval,
vitals analysis, care-action scheduling, and emergency protocol escalation.

All functions return JSON-serializable dictionaries and carry no side effects.
"""

from __future__ import annotations

import inspect
import json
import logging
from datetime import datetime, timezone
from typing import Any
import uuid

logger = logging.getLogger(__name__)

MCP_VERSION = "2025-11-25"

# -----------------------------------------------------------------------------
# Patient Profile
# -----------------------------------------------------------------------------

def get_patient_profile(user_id: str = "default_user", patient_id: str | None = None) -> dict[str, Any]:
    """Retrieve a mock patient medical baseline for the given user.

    Args:
        user_id: Unique patient identifier. Defaults to 'default_user'.
        patient_id: Optional alias for user_id per MCP specifications.

    Returns:
        Demographic snapshot, known allergies, active medications, resting vitals,
        and emergency contacts.
    """
    effective_id = patient_id if patient_id is not None else user_id
    return {
        "user_id": effective_id,
        "patient_id": effective_id,
        "name": "Shashwat Mahajan",
        "age": 34,
        "sex": "Male",
        "blood_type": "O+",
        "allergies": ["Penicillin", "Shellfish"],
        "active_medications": [
            {"name": "Lisinopril", "dose": "10 mg", "frequency": "Once daily"},
            {"name": "Metformin", "dose": "500 mg", "frequency": "Twice daily"},
        ],
        "resting_vitals": {
            "heart_rate_bpm": 72,
            "systolic_mmhg": 118,
            "diastolic_mmhg": 76,
            "sp_o2_pct": 98,
        },
        "emergency_contacts": [
            {"name": "Anita Mahajan", "relation": "Spouse", "phone": "+1-555-0101"},
            {"name": "Dr. Raj Patel", "relation": "PCP", "phone": "+1-555-0202"},
        ],
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


# -----------------------------------------------------------------------------
# Vitals Analysis
# -----------------------------------------------------------------------------

def analyze_vitals(
    systolic: int,
    diastolic: int,
    heart_rate: int | None = None,
    sp_o2: int | None = None,
) -> dict[str, Any]:
    """Perform deterministic cardiovascular risk assessment from spot vitals.

    Risk matrix applied:
    - Blood-pressure classification follows AHA/ACC 2017 guidelines.
    - Heart-rate thresholds: bradycardia < 60 bpm, tachycardia > 100 bpm.
    - SpO2 < 92% is flagged as hypoxemia.

    Args:
        systolic: Systolic blood pressure in mmHg.
        diastolic: Diastolic blood pressure in mmHg.
        heart_rate: Optional heart rate in beats per minute.
        sp_o2: Optional peripheral oxygen saturation percentage.

    Returns:
        Structured dict with risk level, clinical guidance, contributing flags,
        and recommended next actions.
    """
    flags: list[str] = []
    risk_level = "Normal"
    guidance: list[str] = []
    next_action = "Continue routine monitoring"

    # Blood-pressure classification
    if systolic >= 180 or diastolic >= 120:
        risk_level = "Critical"
        flags.append("Hypertensive Crisis")
        guidance.append(
            "Blood pressure is dangerously high (>=180/120 mmHg). "
            "Seek immediate emergency medical attention."
        )
        next_action = "Call emergency services immediately"
    elif systolic >= 140 or diastolic >= 90:
        risk_level = _escalate(risk_level, "Warning")
        flags.append("Stage 2 Hypertension")
        guidance.append(
            "Blood pressure is in the Stage 2 Hypertension range (>=140/90 mmHg). "
            "Consult your physician for medication adjustment."
        )
        next_action = "Schedule urgent physician consultation"
    elif systolic >= 130 or diastolic >= 80:
        risk_level = _escalate(risk_level, "Warning")
        flags.append("Stage 1 Hypertension")
        guidance.append(
            "Blood pressure is elevated into Stage 1 Hypertension (>=130/80 mmHg). "
            "Lifestyle modifications and follow-up recommended."
        )
        next_action = "Book follow-up with primary care within 1 week"
    elif systolic < 90 or diastolic < 60:
        risk_level = _escalate(risk_level, "Warning")
        flags.append("Low Blood Pressure (Hypotension)")
        guidance.append(
            "Blood pressure is below normal (<90/60 mmHg). "
            "Monitor for dizziness or fainting; hydrate and consult physician."
        )
        next_action = "Monitor symptoms and consult physician if symptomatic"

    # Heart-rate classification (evaluated only when provided)
    if heart_rate is not None:
        if heart_rate > 150:
            risk_level = _escalate(risk_level, "Critical")
            flags.append("Severe Tachycardia")
            guidance.append(
                "Heart rate exceeds 150 bpm - possible supraventricular tachycardia. "
                "Seek emergency evaluation."
            )
            next_action = "Call emergency services immediately"
        elif heart_rate > 100:
            risk_level = _escalate(risk_level, "Warning")
            flags.append("Tachycardia")
            guidance.append(
                "Heart rate is elevated (>100 bpm). Rest, hydrate, and re-check in 15 minutes."
            )
        elif heart_rate < 40:
            risk_level = _escalate(risk_level, "Critical")
            flags.append("Severe Bradycardia")
            guidance.append(
                "Heart rate is dangerously low (<40 bpm). Seek immediate medical attention."
            )
            next_action = "Call emergency services immediately"
        elif heart_rate < 60:
            risk_level = _escalate(risk_level, "Warning")
            flags.append("Bradycardia")
            guidance.append(
                "Heart rate is below normal (<60 bpm). If symptomatic (dizziness, fatigue), consult physician."
            )

    # SpO2 classification (evaluated only when provided)
    if sp_o2 is not None:
        if sp_o2 < 90:
            risk_level = _escalate(risk_level, "Critical")
            flags.append("Severe Hypoxemia")
            guidance.append(
                "Oxygen saturation is critically low (<90%). "
                "Administer supplemental oxygen and call emergency services."
            )
            next_action = "Call emergency services immediately"
        elif sp_o2 < 92:
            risk_level = _escalate(risk_level, "Warning")
            flags.append("Hypoxemia")
            guidance.append(
                "Oxygen saturation is below safe threshold (<92%). "
                "Sit upright, practise deep breathing, and seek medical advice."
            )

    if not flags:
        guidance.append("All reported vitals are within normal physiological limits.")

    return {
        "blood_pressure": f"{systolic}/{diastolic} mmHg",
        "heart_rate_bpm": heart_rate,
        "sp_o2_pct": sp_o2,
        "risk_level": risk_level,
        "flags": flags,
        "clinical_guidance": guidance,
        "recommended_next_action": next_action,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }


def _escalate(current: str, proposed: str) -> str:
    """Return the higher-severity risk level between current and proposed."""
    severity_order = {"Normal": 0, "Warning": 1, "Critical": 2}
    return proposed if severity_order.get(proposed, 0) > severity_order.get(current, 0) else current


# -----------------------------------------------------------------------------
# Care-Action Scheduling
# -----------------------------------------------------------------------------

def schedule_care_action(
    action_type: str,
    details: str,
    schedule_time: str,
) -> dict[str, Any]:
    """Create a scheduled care-action ledger entry.

    Args:
        action_type: Category of action ('medication_reminder', 'doctor_appointment', etc.).
        details: Human-readable description of the scheduled task.
        schedule_time: ISO-8601 datetime string for when the action is due.

    Returns:
        Ledger entry with unique action ID, status, creation timestamp, and reminder alerts.
    """
    action_id = f"CA-{uuid.uuid4().hex[:8].upper()}"

    return {
        "action_id": action_id,
        "action_type": action_type,
        "details": details,
        "scheduled_for": schedule_time,
        "status": "Scheduled",
        "reminder_alerts": [
            {
                "offset_minutes": -30,
                "channel": "push_notification",
                "message": f"Reminder: '{details}' in 30 minutes.",
            },
            {
                "offset_minutes": -5,
                "channel": "voice_alert",
                "message": f"Upcoming: '{details}' in 5 minutes.",
            },
        ],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# -----------------------------------------------------------------------------
# Emergency Protocol (Simulated)
# -----------------------------------------------------------------------------

def trigger_emergency_protocol(
    reason: str,
    vitals_summary: str = "Not provided",
    severity: str = "CRITICAL",
    patient_location: str = "Primary Residence",
) -> dict[str, Any]:
    """Escalate a health anomaly through the simulated emergency-response pipeline.

    Simulates three parallel actions:
    1. EMS dispatch (simulated 911 alert).
    2. Caregiver SMS alert payload.
    3. Local on-device alarm activation.

    Args:
        reason: Short description of the triggering event.
        vitals_summary: Compact representation of vitals (defaults to 'Not provided').
        severity: Severity tag ('CRITICAL', 'HIGH', 'MODERATE').
        patient_location: Current location of the patient.

    Returns:
        Composite status dict with simulated dispatch, SMS, and local alert outcomes.
    """
    incident_id = f"EM-{uuid.uuid4().hex[:8].upper()}"

    return {
        "incident_id": incident_id,
        "simulated": True,
        "severity": severity,
        "reason": reason,
        "patient_location": patient_location,
        "vitals_snapshot": vitals_summary,
        "speech_notice": "Simulated emergency alert (demo, no real services contacted).",
        "dispatch_status": {
            "ems_notified": True,
            "simulated": True,
            "estimated_arrival_min": 8,
            "dispatch_confirmation": f"Simulated EMS dispatch - incident {incident_id}",
        },
        "caregiver_sms": {
            "recipients": ["+1-555-0101", "+1-555-0202"],
            "simulated": True,
            "message": (
                f"[SIMULATED HEALTH ALERT - {severity}]: {reason}. "
                f"Location: {patient_location}. "
                f"Current vitals: {vitals_summary}. "
                f"Incident ID: {incident_id}. "
                "Simulated alert: no real services contacted."
            ),
            "delivery_status": "SimulatedSent",
        },
        "local_alert": {
            "simulated": True,
            "audible_alarm": True,
            "visual_alarm": True,
            "smart_home_actions": ["Unlock front door", "Turn on all lights"],
        },
        "triggered_at": datetime.now(timezone.utc).isoformat(),
    }


# -----------------------------------------------------------------------------
# Official Tool Schemas & Unified Dispatcher (Single Source of Truth)
# -----------------------------------------------------------------------------

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

MCP_TOOLS = OFFICIAL_MCP_TOOLS

_TOOL_MAP: dict[str, Any] = {
    "get_patient_profile": get_patient_profile,
    "analyze_vitals": analyze_vitals,
    "schedule_care_action": schedule_care_action,
    "trigger_emergency_protocol": trigger_emergency_protocol,
}


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
