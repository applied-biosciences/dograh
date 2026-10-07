
from pipecat.utils.enums import RealtimeFeedbackType

SAKINAH_CONTINUITY_ACTION_EVENT_TYPE = "rtf-sakinah-continuity-action"


def _format_timestamp_range(
    payload: dict, event: dict, include_end_timestamps: bool
) -> str:
    start_timestamp = payload.get("timestamp") or event.get("timestamp", "")
    if not include_end_timestamps:
        return start_timestamp

    end_timestamp = payload.get("end_timestamp")
    if end_timestamp:
        return (
            f"{start_timestamp} -> {end_timestamp}"
            if start_timestamp
            else end_timestamp
        )
    return start_timestamp


def generate_transcript_text(
    events: list[dict], *, include_end_timestamps: bool = False
) -> str:
    """Generate transcript text from realtime feedback events.

    Formats conversational events as '[timestamp] user/assistant: text\\n' and
    preserves safe Sakinah continuity action events as internal boxes.
    """
    lines: list[str] = []
    for event in events:
        event_type = event.get("type")
        payload = event.get("payload", {})

        if (
            event_type == RealtimeFeedbackType.USER_TRANSCRIPTION.value
            and payload.get("final") is True
        ):
            timestamp = _format_timestamp_range(payload, event, include_end_timestamps)
            prefix = f"[{timestamp}] " if timestamp else ""
            lines.append(f"{prefix}user: {payload.get('text', '')}\n")
        elif event_type == RealtimeFeedbackType.BOT_TEXT.value:
            timestamp = _format_timestamp_range(payload, event, include_end_timestamps)
            prefix = f"[{timestamp}] " if timestamp else ""
            lines.append(f"{prefix}assistant: {payload.get('text', '')}\n")
        elif event_type == SAKINAH_CONTINUITY_ACTION_EVENT_TYPE:
            lines.extend(_format_sakinah_action_event(event))

    return "".join(lines)


def _format_sakinah_action_event(event: dict) -> list[str]:
    """Render only safe structured action fields into an export box."""
    payload = event.get("payload") or {}
    details = payload.get("details") or {}
    action = str(payload.get("action") or "CONTINUITY_ACTION")
    status = str(payload.get("status") or "unavailable").upper()
    timestamp = str(payload.get("timestamp") or event.get("timestamp") or "")
    turn_id = payload.get("turn_id")
    lines = [
        f"┌─ INTERNAL ACTION · {action} · {status} ─────────────┐\n",
        f"│ Turn: {turn_id if turn_id is not None else 'call setup'}   Timestamp: {timestamp}\n",
    ]
    display_fields = (
        ("caller_result", "Caller result"),
        ("choice", "Choice"),
        ("retrieval_status", "Retrieval"),
        ("injection_status", "Injection"),
        ("previous_calls_loaded", "Previous calls"),
        ("durable_facts_loaded", "Durable facts"),
        ("previous_calls_available", "Previous calls available"),
        ("durable_facts_available", "Durable facts available"),
        ("raw_transcripts_injected", "Raw transcripts injected"),
        ("storage_source", "Source"),
    )
    for key, label in display_fields:
        if key in details:
            value = details[key]
            if isinstance(value, bool):
                value = "yes" if value else "no"
            lines.append(f"│ {label}: {value}\n")
    calls = details.get("previous_calls") or []
    if calls:
        lines.append("│ Bounded previous calls:\n")
        for item in calls:
            summary = str((item or {}).get("bounded_summary") or "")[:240]
            lines.append(f"│   - {summary}\n")
    categories = details.get("durable_fact_categories") or []
    if categories:
        lines.append(f"│ Durable fact categories: {', '.join(map(str, categories))}\n")
    lines.append("└──────────────────────────────────────────────────────┘\n")
    return lines
