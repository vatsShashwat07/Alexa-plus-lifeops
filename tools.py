"""Demo only, not a medical device, no real EMS/SMS integration.

tools.py - Clinical & Lifestyle Health Tools for MCP Server
=============================================================
Deterministic, pure-function toolkit exposing patient profile retrieval,
vitals analysis, care-action scheduling, and emergency protocol escalation.

All functions return JSON-serializable dictionaries and carry no side effects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid


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
