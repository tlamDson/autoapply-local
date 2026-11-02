"""Per-task native tool boundary; no permissions are auto-approved.

The command hook is supplied for one invocation only, never installed globally.
See https://code.claude.com/docs/en/hooks (PreToolUse, exec-form, onFailure).
"""

import json
import re
import sys


MAIL_ACTIONS = {
    "profile": frozenset({"get_profile", "get_account_profile", "get_user_profile", "list_accounts", "get_accounts"}),
    "search": frozenset({"search", "search_email_ids", "search_messages", "list_messages"}),
    "read": frozenset(
        {"read_email", "batch_read_email", "read_message", "get_message", "get_email", "fetch_email", "fetch"}
    ),
}
DISCOVERY_TOOLS = frozenset({"ToolSearch", "search_tool_bm25"})


def mail_tool_action(tool):
    """Recognize a finite read-only Gmail vocabulary, never prefix-match writes."""
    parts = str(tool).split("__")
    if len(parts) != 3 or parts[0] != "mcp":
        return None
    server, operation = parts[1].casefold(), parts[2].casefold()
    if server not in {"gmail", "claude_ai_gmail"}:
        return None
    return next((action for action, names in MAIL_ACTIONS.items() if operation in names), None)


def permitted(policy, event):
    name = event.get("tool_name")
    if not isinstance(name, str):
        return False
    if name == "StructuredOutput" and policy.get("structured_output") is True:
        return True
    kind = policy.get("kind", "")
    if kind == "mail_discovery":
        if name in DISCOVERY_TOOLS:
            tool_input = event.get("tool_input")
            if not isinstance(tool_input, dict):
                return False
            query = tool_input.get("query", "")
            return isinstance(query, str) and re.search(r"\bgmail\b", query.casefold()) is not None
        return mail_tool_action(name) == "profile"
    if kind in {"mail_read", "mail_search"}:
        if name not in policy.get("tools", []) or mail_tool_action(name) != kind.removeprefix("mail_"):
            return False
        policy_account = policy.get("account")
        tool_input = event.get("tool_input")
        if not isinstance(policy_account, str) or not policy_account or not isinstance(tool_input, dict):
            return False
        account = tool_input.get("account")
        return isinstance(account, str) and account == policy_account
    parts = name.split("__")
    if len(parts) >= 2 and parts[0] == "mcp" and parts[1].casefold() in {"gmail", "claude_ai_gmail"}:
        return False
    return name in policy.get("tools", [])


def main(argv=None, stdin=None):
    try:
        policy = json.loads((sys.argv[1:] if argv is None else argv)[0])
        event = json.load(sys.stdin if stdin is None else stdin)
        if isinstance(policy, dict) and isinstance(event, dict) and permitted(policy, event):
            # No allow verdict: the runtime's ordinary approvals still apply.
            return 0
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        pass
    print("AutoApply Local: tool is outside this task's capability boundary", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
