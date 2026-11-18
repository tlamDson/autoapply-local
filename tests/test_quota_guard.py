"""Quota guard (plan aa-20, flow 3.4 step 9): stop `apply` at the user's stop percent.

The guard reads usage through the engine probe; tests feed it recorded
``rate_limit_event`` (Claude) and ``rate_limits`` (Codex) output through a fake
runner, never a live engine. The run tests reuse the synthetic Greenhouse
harness of test_apply_run: a stop keeps every held tab, leaves the rest pending
for the next run and reports progress. People and employers are invented.
"""

import json
import subprocess
from unittest.mock import patch

from test_apply_run import ApplyHarness, gh_job
from test_support import ReleaseCase

from autoapply_local import quota, quota_guard
from autoapply_local.engines import EngineError


def stream(*rows):
    return "\n".join(json.dumps(row) for row in rows) + "\n"


def claude_event(five, week=None, status="allowed"):
    info = {"status": status, "rateLimitType": "five_hour", "utilization": five}
    if week is not None:
        info["unifiedWindows"] = {"seven_day": {"utilization": week}}
    return stream({"type": "system", "subtype": "init"}, {"type": "rate_limit_event", "rate_limit_info": info},
                  {"type": "result", "is_error": False, "result": "OK"})


def codex_event(five, week):
    limits = {"primary": {"used_percent": five, "window_minutes": 300},
              "secondary": {"used_percent": week, "window_minutes": 10080}}
    return stream({"type": "thread.started"},
                  {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": limits}},
                  {"type": "turn.completed"})


CLAUDE_LIMIT = stream({"type": "result", "is_error": True, "result": "Claude usage limit reached; resets 5pm"})


class Recorded:
    """A fake CLI runner that replays one recorded output per call."""

    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), 0

    def __call__(self, argv, **kwargs):
        text = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return subprocess.CompletedProcess(argv, 0, text, "")


class GuardCase(ReleaseCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(quota, "resolve_executable", lambda name: [f"/bin/{name}"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def guard(self, engine, *outputs, stop=90):
        runner = Recorded(*outputs)
        return quota_guard.Guard(engine, stop, probe=lambda name: quota.probe(name, runner=runner)), runner


class StopPercentTests(ReleaseCase):
    def test_default_is_ninety_and_the_saved_value_wins(self):
        self.assertEqual(quota_guard.stop_percent({}), 90)
        self.assertEqual(quota_guard.stop_percent({"runtime": {"quota_stop_percent": 75}}), 75)

    def test_values_outside_one_to_one_hundred_are_refused(self):
        for value in (0, 101, "80", True, None, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                quota_guard.stop_percent({"runtime": {"quota_stop_percent": value}})


class GuardTests(GuardCase):
    def test_usage_below_the_stop_lets_the_run_continue(self):
        guard, runner = self.guard("claude", claude_event(0.42, 0.135))
        self.assertIsNone(guard.check())
        self.assertEqual(runner.calls, 1)
        report = guard.report()
        self.assertEqual((report["five_hour_pct"], report["seven_day_pct"]), (42.0, 13.5))
        self.assertEqual(report["stop_percent"], 90)
        self.assertFalse(report["stopped"])

    def test_five_hour_usage_at_the_stop_percent_stops_the_run(self):
        guard, _ = self.guard("claude", claude_event(0.90, 0.20))
        reason = guard.check()
        self.assertIn("5-hour", reason)
        self.assertIn("90%", reason)
        self.assertIn("held tabs are kept", reason)
        self.assertTrue(guard.report()["stopped"])

    def test_weekly_usage_over_the_stop_percent_stops_the_run(self):
        guard, _ = self.guard("claude", claude_event(0.10, 0.93), stop=80)
        reason = guard.check()
        self.assertIn("weekly", reason)
        self.assertIn("93", reason)
        self.assertIn("80%", reason)

    def test_codex_rate_limits_are_read_by_window_length(self):
        guard, _ = self.guard("codex", codex_event(95.0, 12.0))
        self.assertIn("5-hour", guard.check())
        self.assertEqual(guard.report()["five_hour_pct"], 95.0)

    def test_usage_limit_reached_stops_even_without_numbers(self):
        guard, _ = self.guard("claude", CLAUDE_LIMIT)
        self.assertIn("usage limit", guard.check())
        self.assertEqual(guard.report()["status"], "quota_exhausted")

    def test_unknown_usage_or_a_failed_probe_never_stops_the_run(self):
        for output in (stream({"type": "result", "is_error": False, "result": "OK"}),
                       stream({"type": "result", "is_error": True, "result": "Not logged in. Run /login"})):
            with self.subTest(output=output[:40]):
                guard, _ = self.guard("claude", output)
                self.assertIsNone(guard.check())
                self.assertIsNone(guard.report()["five_hour_pct"])

    def test_far_below_the_stop_the_probe_runs_every_few_jobs(self):
        guard, runner = self.guard("claude", claude_event(0.20, 0.10))
        for _ in range(7):
            self.assertIsNone(guard.check())
            guard.after({"jobs": [{"status": "held"}]})
        self.assertEqual(runner.calls, 1 + 6 // quota_guard.EVERY)

    def test_near_the_stop_the_probe_runs_before_every_job(self):
        guard, runner = self.guard("claude", claude_event(0.85), claude_event(0.88), claude_event(0.91))
        self.assertIsNone(guard.check())
        guard.after({"jobs": []})
        self.assertIsNone(guard.check())
        guard.after({"jobs": []})
        self.assertIn("91", guard.check())
        self.assertEqual(runner.calls, 3)

    def test_an_engine_usage_limit_during_a_job_stops_before_the_next(self):
        guard, runner = self.guard("claude", claude_event(0.30))
        self.assertIsNone(guard.check())
        reason = str(EngineError("quota_exhausted"))
        guard.after({"jobs": [{"job_id": "a", "status": "retryable", "reason": reason}]})
        self.assertIn("usage limit", guard.check())
        self.assertEqual(runner.calls, 1)

    def test_a_stopped_guard_stays_stopped_without_another_probe(self):
        guard, runner = self.guard("claude", claude_event(0.95), claude_event(0.10))
        first = guard.check()
        self.assertEqual(guard.check(), first)
        self.assertEqual(runner.calls, 1)


class GuardedRunTests(ApplyHarness):
    def usage(self, *values):
        """Patch the engine probe with recorded five-hour usage, one value per probe."""
        seen = []

        def probe(engine, **kwargs):
            seen.append(engine)
            five = values[min(len(seen) - 1, len(values) - 1)]
            return {"engine": engine, "status": "ready", "five_hour_pct": five, "seven_day_pct": 5.0}

        patcher = patch("autoapply_local.runtime.quota_probe", side_effect=probe)
        patcher.start()
        self.addCleanup(patcher.stop)
        return seen

    def test_run_stops_at_the_stop_percent_keeps_tabs_and_reports_progress(self):
        self.write_json("config.json", {**json.loads((self.root / "config.json").read_text(encoding="utf-8")),
                                        "runtime": {"apply_engine": "codex", "jobs_per_run": 3,
                                                    "quota_stop_percent": 80}})
        seen = self.usage(75.0, 82.0)
        postings = [gh_job(n, role=f"Backend Software Engineering Intern {n}") for n in (201, 202, 203)]
        report, err = self.run_apply(postings, selection=None)
        self.assertEqual(seen, ["codex", "codex"])
        held = self.store.get("greenhouse:exampleco:201")
        self.assertEqual(held["state"], "needs_manual")
        self.assertTrue(held["tab_id"])
        for job_id in ("greenhouse:exampleco:202", "greenhouse:exampleco:203"):
            self.assertEqual(self.store.get(job_id)["state"], "pending")
        self.assertEqual([row["job_id"] for row in report["rows"]], ["greenhouse:exampleco:201"])
        self.assertEqual(report["quota"]["five_hour_pct"], 82.0)
        self.assertEqual(report["quota"]["stop_percent"], 80)
        self.assertTrue(report["quota"]["stopped"])
        self.assertEqual(report["left"], ["greenhouse:exampleco:202", "greenhouse:exampleco:203"])
        self.assertIn("82", report["reason"])
        self.assertIn("Stopped:", err)
        self.assertIn("2 jobs left for the next run", err)
        self.assertEqual(self.client.calls.count("new_page"), 1)
        self.assert_never_clicked(report)

    def test_usage_already_at_the_stop_opens_nothing(self):
        self.usage(96.0)
        report, err = self.run_apply([gh_job(301)], selection=None)
        self.assertEqual(self.client.calls.count("new_page"), 0)
        self.assertEqual(self.store.get("greenhouse:exampleco:301")["state"], "pending")
        self.assertEqual(report["left"], ["greenhouse:exampleco:301"])
        self.assertIn("No form was opened", err)
        self.assertIn("96", err)

    def test_engine_usage_limit_mid_run_keeps_the_job_pending_and_stops(self):
        self.usage(10.0)

        def limit(request):
            raise EngineError("quota_exhausted")

        self.engine_reply = limit
        postings = [gh_job(n, role=f"Backend Software Engineering Intern {n}") for n in (401, 402)]
        report, err = self.run_apply(postings, selection=None)
        self.assertEqual(self.store.get("greenhouse:exampleco:401")["state"], "pending")
        self.assertEqual(self.store.get("greenhouse:exampleco:402")["state"], "pending")
        self.assertEqual(report["left"], ["greenhouse:exampleco:402"])
        self.assertIn("usage limit", report["reason"])
        self.assertEqual(self.client.calls.count("new_page"), 0)
        self.assert_never_clicked(report)

    def test_no_job_to_fill_never_probes_the_engine(self):
        seen = self.usage(99.0)
        report, _ = self.run_apply([], selection=None)
        self.assertEqual(seen, [])
        self.assertNotIn("reason", report)
