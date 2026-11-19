"""Refill proof: which refilled fields a fresh readback actually shows.

A refill writes the user's popup answers into a held tab. The adapter's or the
engine's own report is not proof, so each answered field is compared with the
fresh readback of the tab: the field with the same ID or, on a page that only
lists answers (a multipage review page), the row with the same question. A
field counts only when every such row was observed and shows the user's own
answer; a wrong, empty, unobserved or missing value keeps it for
``questions --refill``. A checkbox group counts only when its checked set is
exactly the saved set: a missing or an extra box keeps it. This module reads evidence only and never touches a tab.
"""

from . import answer_bank

YES = {"yes", "true", "checked", "selected"}
NO = {"no", "false", "unchecked", "not selected"}


def _text(value):
    return " ".join(str(value).split())


def _shows(field, answer, observed, summary=False):
    """Whether one observed value is this answer to this field.

    ``summary`` marks a row that lists the answer as text (a review row, or a
    row found by its question): a choice is read there as an option label only.
    """
    kind = answer_bank.kind(field)
    if kind == "multi":
        return _shows_set(field, answer, observed, summary)
    if isinstance(observed, (list, tuple)):
        if len(observed) != 1:
            return False
        observed = observed[0]
    if observed is None or observed == "":
        return False
    if kind == "boolean":
        if type(observed) is bool:
            return observed is answer
        return isinstance(observed, str) and answer_bank.normalize(observed) in (YES if answer else NO)
    if type(observed) is bool:
        return False
    if kind == "text":
        return _text(observed) == _text(answer)
    # A review row shows the option's label; a control may report its value or
    # its label. Every option the observed text could name must be the chosen
    # one, so a text that reads as two different options proves nothing.
    return _named(field, observed, summary) == {str(answer)}


def _named(field, observed, summary):
    """The option values one observed text could name."""
    pairs = []
    for option in field.get("options") or []:
        value = option.get("value") if isinstance(option, dict) else option
        label = option.get("label", value) if isinstance(option, dict) else option
        if value not in ("", None):
            pairs.append((str(value), answer_bank.normalize(label)))
    named = {value for value, label in pairs if label == answer_bank.normalize(observed)}
    if not summary:
        named |= {value for value, _ in pairs if value == str(observed)}
    return named


def _shows_set(field, answer, observed, summary):
    """Whether the observed checked boxes are exactly the saved set, each naming one option."""
    if not isinstance(answer, list) or not answer:
        return False
    items = list(observed) if isinstance(observed, (list, tuple)) else [observed]
    if not items or any(type(item) is bool or item in (None, "") for item in items):
        return False
    named = [_named(field, item, summary) for item in items]
    if any(len(values) != 1 for values in named):
        return False
    checked = [next(iter(values)) for values in named]
    return len(set(checked)) == len(checked) and set(checked) == {str(item) for item in answer}


def _listed(field, row):
    return row.get("type") == "summary" or row.get("id") != field["id"]


def _rows(field, live_fields):
    same_id = [row for row in live_fields if row.get("id") == field["id"]]
    if same_id:
        return same_id
    question = answer_bank.normalize(field.get("label"))
    return [row for row in live_fields if question and answer_bank.normalize(row.get("label")) == question]


def unproven(fields, answers, live):
    """IDs of answered fields the fresh readback does not show with the user's answer."""
    live_fields = [row for row in (live or {}).get("fields") or [] if isinstance(row, dict)]
    missing = set()
    for field in fields:
        if field["id"] not in answers:
            continue
        rows = _rows(field, live_fields)
        if not rows or any(
            row.get("observed") is False or not _shows(field, answers[field["id"]], row.get("value"), _listed(field, row))
            for row in rows
        ):
            missing.add(field["id"])
    return missing


def blocked_fields(fields, blocked):
    """The open fields an engine reported blocked; an unknown ID keeps every field."""
    by_id = {field.get("id"): field for field in fields}
    held = []
    for item in blocked:
        field = by_id.get(item.get("id")) if isinstance(item, dict) else None
        if field is None:
            return list(fields)
        if field not in held:
            held.append(field)
    return held


def blocked_reason(blocked):
    """The engine's blocked reasons as one problem line, or None."""
    reasons = []
    for item in blocked:
        reason = str(item.get("reason") or "") if isinstance(item, dict) else str(item)
        if reason and reason not in reasons:
            reasons.append(reason)
    return "Engine blocked: " + ", ".join(reasons) if reasons else None
