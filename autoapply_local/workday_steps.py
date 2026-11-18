"""Scripts for the Workday steps that move the held tab (``adapters/workday_steps.cjs``).

The fill and audit scripts of the Workday driver never click. Moving the tab
(Apply, Apply Manually, sign-in, create account, verify, Next) goes through
the adapter's ``press`` with one action of a fixed table, and the adapter
refuses any control whose text or label says Submit. Next carries a second
lock here: it is refused unless the tab is on a form step before the last one,
so the Review page's Submit is never pressed. Step scripts run with the
``step`` operation, which the engine's MCP readback does not accept.
"""

import json
from pathlib import Path

ADAPTER = "AutoApplyWorkdaySteps"
ACTIONS = (
    "apply",
    "apply_manually",
    "sign_in_with_email",
    "create_account_link",
    "sign_in",
    "create_account",
    "verify",
    "next",
)
CALLS = ("where", "typeText", "credentials", "code")
OPERATION = "step"


def _code():
    directory = Path(__file__).with_name("adapters")
    return "\n".join(
        (directory / name).read_text(encoding="utf-8") for name in ("common.cjs", "workday.cjs", "workday_steps.cjs")
    )


def _build(call, args):
    arguments = "".join(", " + json.dumps(arg, ensure_ascii=False) for arg in args)
    return "async () => {\n" + _code() + f"\nreturn await globalThis.{ADAPTER}.{call}(document{arguments});\n}}"


def script(call, *args):
    """The evaluate_script function for one read or typing call of the steps adapter."""
    if call not in CALLS:
        raise ValueError(f"Unsupported Workday step call {call!r}; presses go through press_script")
    return _build(call, args)


def press_script(action, where=None):
    """The script that presses one fixed action; Next only on a form step before Review."""
    if action not in ACTIONS:
        raise ValueError(f"Unsupported Workday action {action!r}")
    if action == "next":
        step = (where or {}).get("step") if isinstance(where, dict) else None
        if (
            not isinstance(where, dict)
            or where.get("page") != "form"
            or not isinstance(step, dict)
            or not isinstance(step.get("index"), int)
            or not isinstance(step.get("total"), int)
            or step["index"] >= step["total"]
        ):
            raise ValueError("Workday Next is refused off a form step and on the last step: the user submits")
    return _build("press", (action,))
