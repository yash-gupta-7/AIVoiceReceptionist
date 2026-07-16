"""OpenAI-format function schemas for the agent tools — the single source of
truth used both by scripts/vapi_setup.py (registers them with Vapi) and by the
eval harness (drives the same tools with a local LLM)."""

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_caller_context",
        "description": "Call FIRST on every call. Returns known patients on this number (may be several — family line), their upcoming appointments, any dropped call to resume, any pending callback context, and today's date/time.",
        "parameters": {"type": "object", "properties": {
            "phone": {"type": "string", "description": "caller's phone number"}},
            "required": []},
    },
    {
        "name": "list_clinic_info",
        "description": "Branches (addresses, hours, pricing, cancellation policy) and all doctors with departments.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "check_availability",
        "description": "Live availability search. ALWAYS call before stating any slot — never reuse earlier results. Filter by doctor, department, branch, a specific date, preferred weekdays, and/or a time window.",
        "parameters": {"type": "object", "properties": {
            "doctor_name": {"type": "string"},
            "department": {"type": "string", "description": "General Dentistry | Orthodontics | Hygiene"},
            "branch_name": {"type": "string", "description": "Bank | Canary Wharf"},
            "on_date": {"type": "string", "description": "YYYY-MM-DD"},
            "weekdays": {"type": "array", "items": {"type": "string"},
                         "description": "e.g. ['monday','wednesday']"},
            "after": {"type": "string", "description": "earliest time HH:MM"},
            "before": {"type": "string", "description": "latest time HH:MM"},
            "limit": {"type": "integer"}},
            "required": []},
    },
    {
        "name": "find_earliest_slot",
        "description": "Earliest opening compared across ALL practitioners and BOTH branches. Use for 'earliest available' / 'first free slot'. Optionally narrow by department, branch, or same-day only.",
        "parameters": {"type": "object", "properties": {
            "department": {"type": "string"},
            "branch_name": {"type": "string"},
            "same_day_only": {"type": "boolean"}},
            "required": []},
    },
    {
        "name": "book_appointment",
        "description": "Book a confirmed slot. Requires the patient's FULL NAME (mandatory even for recognized numbers), the doctor_id and ISO starts_at from an availability result. Re-checks live; on slot_no_longer_available offer the returned alternatives.",
        "parameters": {"type": "object", "properties": {
            "patient_name": {"type": "string"},
            "phone": {"type": "string"},
            "dob": {"type": "string", "description": "YYYY-MM-DD if known"},
            "doctor_id": {"type": "integer"},
            "starts_at": {"type": "string", "description": "ISO datetime from availability result"}},
            "required": ["patient_name", "doctor_id", "starts_at"]},
    },
    {
        "name": "find_appointments",
        "description": "Upcoming appointments for the caller's number, optionally filtered by patient name.",
        "parameters": {"type": "object", "properties": {
            "phone": {"type": "string"},
            "patient_name": {"type": "string"}},
            "required": []},
    },
    {
        "name": "cancel_appointment",
        "description": "Cancel an appointment after the caller confirms.",
        "parameters": {"type": "object", "properties": {
            "appointment_id": {"type": "integer"}},
            "required": ["appointment_id"]},
    },
    {
        "name": "reschedule_appointment",
        "description": "Move an appointment to a new ISO datetime. Re-checks live availability; on conflict offers alternatives.",
        "parameters": {"type": "object", "properties": {
            "appointment_id": {"type": "integer"},
            "new_starts_at": {"type": "string"}},
            "required": ["appointment_id", "new_starts_at"]},
    },
    {
        "name": "log_follow_up",
        "description": "Log anything needing a human: requests for staff, clinical questions, complaints. Staff will call back — never claim a live transfer.",
        "parameters": {"type": "object", "properties": {
            "phone": {"type": "string"},
            "reason": {"type": "string"}},
            "required": ["reason"]},
    },
]
