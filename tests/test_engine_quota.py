"""Engine login and quota probe: recorded CLI output, fake runner, no real engine call."""

import json
import subprocess
from unittest.mock import patch

from test_support import ReleaseCase


def stream(*rows):
    return "\n".join(json.dumps(row) for row in rows) + "\n"


CLAUDE_OK = stream(
    {"type": "system", "subtype": "init"},
    {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "allowed",
            "rateLimitType": "five_hour",
            "utilization": 0.42,
            "unifiedWindows": {"seven_day": {"utilization": 0.135}},
        },
    },
    {"type": "result", "is_error": False, "result": "OK"},
)
CODEX_OK = stream(
    {"type": "thread.started"},
    {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "primary": {"used_percent": 7.0, "window_minutes": 300},
                "secondary": {"used_percent": 61.5, "window_minutes": 10080},
            },
        },
    },
    {"type": "turn.completed"},
)


class Runner:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.calls = []
        self.result = (returncode, stdout, stderr)

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        code, out, err = self.result
        return subprocess.CompletedProcess(argv, code, out, err)


class QuotaParsing(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.quota = self.module("quota")

    def test_claude_rate_limit_event_gives_both_windows_in_percent(self):
        self.assertEqual(
            self.quota.parse_usage("claude", CLAUDE_OK), {"five_hour_pct": 42.0, "seven_day_pct": 13.5}
        )

    def test_codex_rate_limits_map_by_window_length(self):
        self.assertEqual(
            self.quota.parse_usage("codex", CODEX_OK), {"five_hour_pct": 7.0, "seven_day_pct": 61.5}
        )

    def test_output_without_quota_reports_unknown_not_zero(self):
        text = stream({"type": "result", "is_error": False}) + "not json\n"
        self.assertEqual(self.quota.parse_usage("claude", text), {"five_hour_pct": None, "seven_day_pct": None})


class QuotaProbe(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.quota = self.module("quota")
        patcher = patch.object(self.quota, "resolve_executable", lambda name: [f"/bin/{name}"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_claude_probe_is_one_tool_free_print_call(self):
        runner = Runner(stdout=CLAUDE_OK)
        result = self.quota.probe("claude", runner=runner, cwd=self.root)
        self.assertEqual(result, {"engine": "claude", "status": "ready", "five_hour_pct": 42.0, "seven_day_pct": 13.5})
        self.assertEqual(len(runner.calls), 1)
        argv, kwargs = runner.calls[0]
        self.assertEqual(argv[0], "/bin/claude")
        self.assertIn("--print", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["cwd"], self.root)

    def test_codex_probe_uses_exec_json(self):
        runner = Runner(stdout=CODEX_OK)
        result = self.quota.probe("codex", runner=runner)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["seven_day_pct"], 61.5)
        argv = runner.calls[0][0]
        self.assertEqual(argv[:3], ["/bin/codex", "exec", "--json"])

    def test_claude_not_logged_in_is_login_required(self):
        out = stream({"type": "result", "is_error": True, "result": "Invalid API key · Please run /login"})
        result = self.quota.probe("claude", runner=Runner(1, out))
        self.assertEqual(result["status"], "login_required")
        self.assertIsNone(result["five_hour_pct"])

    def test_error_result_with_zero_exit_is_still_a_failure(self):
        out = stream({"type": "result", "is_error": True, "result": "Not logged in"})
        self.assertEqual(self.quota.probe("claude", runner=Runner(0, out))["status"], "login_required")

    def test_codex_login_hint_is_login_required(self):
        result = self.quota.probe("codex", runner=Runner(1, "", "error: run `codex login` first"))
        self.assertEqual(result["status"], "login_required")

    def test_usage_limit_is_quota_exhausted(self):
        result = self.quota.probe("claude", runner=Runner(1, "", "Claude usage limit reached"))
        self.assertEqual(result["status"], "quota_exhausted")

    def test_missing_executable_is_configuration(self):
        def missing(name):
            raise self.quota.EngineError("configuration")

        with patch.object(self.quota, "resolve_executable", missing):
            result = self.quota.probe("codex", runner=Runner())
        self.assertEqual(result["status"], "configuration")

    def test_timeout_is_reported(self):
        def slow(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 1)

        self.assertEqual(self.quota.probe("claude", runner=slow)["status"], "timeout")

    def test_unknown_engine_is_rejected(self):
        with self.assertRaises(ValueError):
            self.quota.probe("gemini", runner=Runner())

    def test_login_instructions_name_the_cli_command(self):
        self.assertIn("codex login", self.quota.login_hint("codex"))
        self.assertIn("/login", self.quota.login_hint("claude"))


class VariadicToolsRunner:
    """Like Claude Code 2.1.x: --tools takes every following non-option value as a tool name."""

    def __init__(self):
        self.prompts = []

    def __call__(self, argv, **kwargs):
        positional, index = [], 1
        while index < len(argv):
            arg = argv[index]
            if arg == "--tools":
                index += 1
                while index < len(argv) and not argv[index].startswith("--"):
                    index += 1
                continue
            if arg == "--output-format":
                index += 2
                continue
            if not arg.startswith("--"):
                positional.append(arg)
            index += 1
        prompt = positional[0] if positional else kwargs.get("input")
        if not prompt:
            message = "Error: Input must be provided either through stdin or as a prompt argument when using --print"
            return subprocess.CompletedProcess(argv, 1, "", message)
        self.prompts.append(prompt)
        return subprocess.CompletedProcess(argv, 0, CLAUDE_OK, "")


class ClaudePromptDelivery(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.quota = self.module("quota")
        patcher = patch.object(self.quota, "resolve_executable", lambda name: [f"/bin/{name}"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prompt_reaches_a_cli_whose_tools_flag_is_variadic(self):
        runner = VariadicToolsRunner()
        result = self.quota.probe("claude", runner=runner)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(runner.prompts, [self.quota.PROMPT])

    def test_failure_keeps_a_short_error_detail(self):
        message = "Error: Input must be provided either through stdin or as a prompt argument when using --print"
        result = self.quota.probe("claude", runner=Runner(1, "", "\n" + message + "\n"))
        self.assertEqual(result["status"], "engine_failure")
        self.assertEqual(result["detail"], message)

    def test_detail_is_capped(self):
        result = self.quota.probe("claude", runner=Runner(1, "", "x" * 2000))
        self.assertLessEqual(len(result["detail"]), self.quota.DETAIL_LIMIT)
