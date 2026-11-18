"""Script driver first, then a field-scoped engine request (plan aa-16).

For drivers with ``runs_before_engine`` the script driver scans, uploads and
fills the held tab. The engine then gets only the fields that need a native
click and already carry a confirmed value: no profile, no PDF, no upload and
no whole-form instructions. Required questions without a confirmed fact never
reach the engine; they hold the tab for the user.
"""

import copy

# What the engine sees of each field: enough to find it and set its value.
FIELD_KEYS = ("id", "label", "type", "options", "value")
FIELD_FILL_TOOLS = ["list_pages", "select_page", "take_snapshot", "fill", "click", "press_key"]
FIELD_FILL_INSTRUCTIONS = (
    "Set only the fields listed here on this existing tab, each to exactly its given confirmed value. "
    "Find each field by its id (the form box marked data-ui=<id>) and its label, use native fill, type or "
    "click on that field's own controls, then read the value back. Do not change, clear or fill any other "
    "field, do not upload or remove any file and do not navigate away. A field you cannot set to exactly its "
    "value goes in blocked with a reason. Preserve the tab and do not open another tab. "
    "Never click a Submit control."
)
FIELD_FILL_SCHEMA = {
    "filled": {"type": "array", "items": {"type": "string"}},
    "blocked": {"type": "array", "items": {"type": "object"}},
    "status": {"type": "string", "enum": ["filled", "needs_manual"]},
}


def field_request(fields):
    """The engine payload for the listed fields only."""
    return {
        "fields": [{key: copy.deepcopy(item.get(key)) for key in FIELD_KEYS} for item in fields],
        "instructions": FIELD_FILL_INSTRUCTIONS,
    }


def script_then_engine(bridge, driver, tab_id, profile, job, pdf):
    """Run the script driver, then hand the engine only its confirmed native-click fields."""
    report = driver.run(bridge.client, tab_id, profile, job, pdf=pdf)
    if report["status"] == "needs_manual":
        return report
    receipt = report.get("upload_receipt")
    if receipt:
        # The driver proved the upload from the browser's File readback.
        bridge.uploads[str(tab_id)] = copy.deepcopy(receipt)
        bridge.observed_uploads[str(tab_id)] = copy.deepcopy(receipt)
    pending = [item for item in report["unknown"] if item["reason"] == "agent_interaction_required"]
    rest = [item for item in report["unknown"] if item["reason"] != "agent_interaction_required"]
    filled, blocked = list(report["filled"]), list(report["blocked"])
    left, engine_status = [], "filled"
    if pending:
        result = bridge.client.interact_fields(tab_id, pending)
        engine_filled = list(result.get("filled", []))
        filled += [key for key in engine_filled if key not in filled]
        blocked += list(result.get("blocked", []))
        engine_status = result.get("status")
        left = [item for item in pending if item["id"] not in engine_filled]
    # Optional fields with no confirmed fact stay empty; required ones wait for
    # the user's answer with the tab held.
    missing = [item for item in rest if item["required"]]
    listed = {item.get("id") for item in blocked}
    blocked += [{"id": item["id"], "reason": item["reason"]} for item in missing if item["id"] not in listed]
    held = engine_status != "filled" or bool(blocked) or bool(left)
    return {
        **report,
        "filled": filled,
        "blocked": blocked,
        "unknown": left + missing,
        "upload_receipt": copy.deepcopy(receipt),
        "status": "needs_manual" if held else "filled",
    }
