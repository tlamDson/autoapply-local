"""Which catalog questions guided setup leaves out of its walk.

A question is left out when terminal setup already holds its answer, when the
answer it depends on turns it off, or when another question already asked for
the same fact. The rules read only answers the user gave; nothing is inferred.
"""

import copy

from .job_filters import raw
from .storage import read_json


# Terminal setup (`setup --terminal`) saves these; the walk does not ask them again.
TERMINAL_RUNTIME = ("language", "apply_engine")
TERMINAL_RESUMES = ("resumes.main_pdf", "resumes.alternatives", "resumes.role_mapping")

# gate field -> (values that turn its questions on, the questions it gates)
GATES = {
    "runtime.mode": (("tailor", "both"), lambda field: field == "runtime.tailor_engine" or field.startswith("tailor.")),
    "resources.gmail_opt_in": ((True,), lambda field: field in (
        "resources.gmail_account", "resources.gmail_lookback", "resources.gmail_permissions")),
}

# question asked later -> question that already asked for the same fact
SHARED = {"international_student.status": "authorization.current_status"}


def terminal(root):
    """Catalog IDs whose answer terminal setup saved in config.json."""
    try:
        config = read_json(root / "config.json")
    except (OSError, ValueError):
        return []
    config = config if isinstance(config, dict) else {}
    runtime, resumes = config.get("runtime") or {}, config.get("resumes") or {}
    fields = ["runtime." + key for key in TERMINAL_RUNTIME if runtime.get(key)]
    if resumes.get("main_pdf"):
        # Terminal role tags replace the role mapping; it removes that key itself.
        fields.extend(TERMINAL_RESUMES)
    return fields


def _value(groups, field):
    group, name = field.split(".")
    return groups[group].get(name)


def gated(groups, field):
    """True when the answer this question depends on turns it off."""
    for gate, (on, covers) in GATES.items():
        if covers(field) and raw(_value(groups, gate)) not in on:
            return True
    return False


def skipped(groups, field):
    """True when the walk leaves this question out. A shared fact is copied into
    the later field when it is blank, so both records keep the one answer."""
    if gated(groups, field):
        return True
    source = SHARED.get(field)
    if source is None:
        return False
    if _value(groups, field) in (None, "") and _value(groups, source) not in (None, ""):
        group, name = field.split(".")
        groups[group][name] = copy.deepcopy(_value(groups, source))
    return True
