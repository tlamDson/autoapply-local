"""Refill proof: which refilled fields a fresh readback actually shows.

A refill writes the user's popup answers into a held tab. The adapter's or the
engine's own report is not proof, so each answered field is compared with the
fresh readback of the tab: the field with the same ID or, on a page that only
lists answers (a multipage review page), the row with the same question. A
field counts only when every such row was observed and shows the user's own
answer; a wrong, empty, unobserved or missing value keeps it for
``questions --refill``. This module reads evidence only and never touches a tab.
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
    if isinstance(observed, (list, tuple)):
        if len(observed) != 1:
            return False
        observed = observed[0]
    if observed is None or observed == "":
        return False
    kind = answer_bank.kind(field)
    if kind == "boolean":
        if type(observed) is bool:
            return observed is answer
        return isinstance(observed, str) and answer_bank.normalize(observed) in (YES if answer else NO)
    if type(observed) is bool:
        return False
    if kind == "text":
        return _text(observed) == _text(answer)
    pairs = []
    for option in field.get("options") or []:
        value = option.get("value") if isinstance(option, dict) else option
        label = option.get("label", value) if isinstance(option, dict) else option
        if value not in ("", None):
            pairs.append((str(value), answer_bank.normalize(label)))
    # A review row shows the option's label; a control may report its value or
    # its label. Every option the observed text could name must be the chosen
    # one, so a text that reads as two different options proves nothing.
    named = {value for value, label in pairs if label == answer_bank.normalize(observed)}
    if not summary:
        named |= {value for value, _ in pairs if value == str(observed)}
    return named == {str(answer)}


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
