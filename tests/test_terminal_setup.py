"""Terminal setup part: scripted stdin, fake probe and launcher, synthetic PDFs in a temporary root."""

import io
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from test_support import ReleaseCase


READY = {"engine": "claude", "status": "ready", "five_hour_pct": 42.0, "seven_day_pct": 13.5}


class Script:
    """Answers typed at each prompt; EOF after the last one."""

    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


class Base(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.ts = self.module("terminal_setup")
        self.runtime_root = self.root / "runtime"
        self.runtime_root.mkdir()
        self.launches, self.probes = [], []

    def pdf(self, name, folder="incoming"):
        path = self.root / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4\n% synthetic " + name.encode() + b"\n%%EOF")
        return path

    def probe(self, results):
        results = list(results)

        def fake(engine, cwd=None):
            self.probes.append((engine, cwd))
            return {**(results.pop(0) if len(results) > 1 else results[0]), "engine": engine}

        return fake

    def launch(self, engine, cwd):
        self.launches.append((engine, cwd))
        return 0

    def wizard(self, answers, probe=(READY,), launch=True, memory_mb=4096):
        self.script, self.out = Script(answers), io.StringIO()
        result = self.ts.run(
            self.runtime_root,
            ask=self.script,
            out=self.out,
            probe=self.probe(probe),
            launch=self.launch if launch else None,
            memory_mb=memory_mb,
        )
        return result

    def config(self):
        return json.loads((self.runtime_root / "config.json").read_text(encoding="utf-8"))


class FreshSetup(Base):
    def test_full_run_saves_every_answer_and_launches_the_engine(self):
        first, second = self.pdf("alex-swe.pdf"), self.pdf("alex-data.pdf")
        result = self.wizard(["1", "1", "", str(first), "SWE, Backend", "y", str(second), "Data", "n", ""])
        config = self.config()
        self.assertEqual(config["runtime"]["language"], "en")
        self.assertEqual(config["runtime"]["apply_engine"], "claude")
        self.assertEqual(config["runtime"]["quota_stop_percent"], 90)
        self.assertEqual(config["runtime"]["jobs_per_run"], 10)
        resumes = config["resumes"]
        copied = (self.runtime_root / "resumes" / "alex-swe.pdf").resolve()
        self.assertEqual(Path(resumes["main_pdf"]), copied)
        self.assertEqual(copied.read_bytes(), first.read_bytes())
        self.assertEqual(resumes["main_tags"], ["SWE", "Backend"])
        self.assertEqual(
            resumes["alternatives"],
            [{"id": "alex-data", "path": str((self.runtime_root / "resumes/alex-data.pdf").resolve()), "tags": ["Data"]}],
        )
        self.assertEqual(config["resources"]["held_tab_cap"], 4)
        self.assertEqual(self.probes, [("claude", self.runtime_root)])
        self.assertEqual(self.launches, [("claude", self.runtime_root)])
        self.assertEqual(result["status"], "launched")
        self.assertIn("42", self.out.getvalue())
        self.assertIn("13.5", self.out.getvalue())

    def test_existing_config_keys_are_preserved(self):
        self.write_json("runtime/config.json", {"boards": [{"ats": "lever", "slug": "example"}], "runtime": {"mode": "quick"}})
        self.wizard(["1", "2", "80", str(self.pdf("cv.pdf")), "ML", "n", "3"])
        config = self.config()
        self.assertEqual(config["boards"], [{"ats": "lever", "slug": "example"}])
        self.assertEqual(config["runtime"]["mode"], "quick")
        self.assertEqual(config["runtime"]["apply_engine"], "codex")
        self.assertEqual(config["runtime"]["quota_stop_percent"], 80)
        self.assertEqual(config["runtime"]["jobs_per_run"], 3)

    def test_vietnamese_switches_later_prompts(self):
        self.wizard(["2", "1", "", str(self.pdf("cv.pdf")), "SWE", "n", ""])
        self.assertEqual(self.config()["runtime"]["language"], "vi")
        prompts = "".join(self.script.prompts)
        self.assertIn(self.ts.MESSAGES["vi"]["jobs_per_run"], prompts)
        self.assertNotIn(self.ts.MESSAGES["en"]["jobs_per_run"], prompts)

    def test_no_launch_reports_the_next_step(self):
        result = self.wizard(["1", "1", "", str(self.pdf("cv.pdf")), "SWE", "n", ""], launch=False)
        self.assertEqual(self.launches, [])
        self.assertEqual(result["status"], "ready")
        self.assertIn("/setup", result["next"])

    def test_held_tab_cap_follows_free_ram(self):
        self.assertEqual(self.ts.held_tab_cap(1024), 1)
        self.assertEqual(self.ts.held_tab_cap(4096), 4)
        self.assertEqual(self.ts.held_tab_cap(64 * 1024), 10)


class EngineLogin(Base):
    def test_not_logged_in_prints_how_to_log_in_and_retries(self):
        missing = {"status": "login_required", "five_hour_pct": None, "seven_day_pct": None}
        self.wizard(["1", "2", "", "", str(self.pdf("cv.pdf")), "SWE", "n", ""], probe=(missing, READY))
        self.assertEqual(len(self.probes), 2)
        self.assertIn("codex login", self.out.getvalue())
        self.assertEqual(self.config()["runtime"]["apply_engine"], "codex")

    def test_quit_at_login_stops_without_saving_the_engine(self):
        missing = {"status": "login_required", "five_hour_pct": None, "seven_day_pct": None}
        with self.assertRaises(self.ts.SetupStopped):
            self.wizard(["2", "1", "q"], probe=(missing,))
        config = self.config()
        self.assertEqual(config["runtime"]["language"], "vi")
        self.assertIsNone(config["runtime"].get("apply_engine"))
        self.assertEqual(self.launches, [])

    def test_three_failed_checks_show_the_error_and_enter_stops(self):
        broken = {"status": "engine_failure", "detail": "Error: Input must be provided", "five_hour_pct": None,
                  "seven_day_pct": None}
        with self.assertRaises(self.ts.SetupStopped):
            self.wizard(["1", "1", "", "", ""], probe=(broken,))
        self.assertEqual(len(self.probes), 3)
        output = self.out.getvalue()
        self.assertIn("Error: Input must be provided", output)
        self.assertEqual(self.script.prompts[-1], self.ts.MESSAGES["en"]["retry_limit"])
        self.assertIsNone(self.config()["runtime"].get("apply_engine"))

    def test_retry_after_the_limit_needs_an_explicit_choice(self):
        broken = {"status": "engine_failure", "detail": "boom", "five_hour_pct": None, "seven_day_pct": None}
        answers = ["1", "1", "", "", "r", "", str(self.pdf("cv.pdf")), "SWE", "n", ""]
        self.wizard(answers, probe=(broken, broken, broken, READY))
        self.assertEqual(len(self.probes), 4)
        self.assertEqual(self.config()["runtime"]["apply_engine"], "claude")

    def test_unknown_quota_is_said_plainly(self):
        unknown = {"status": "ready", "five_hour_pct": None, "seven_day_pct": None}
        self.wizard(["1", "1", "", str(self.pdf("cv.pdf")), "SWE", "n", ""], probe=(unknown,))
        self.assertIn(self.ts.MESSAGES["en"]["quota_unknown"], self.out.getvalue())


class Validation(Base):
    def test_out_of_range_numbers_are_asked_again(self):
        self.wizard(["9", "1", "3", "1", "abc", "150", "0", "85", str(self.pdf("cv.pdf")), "SWE", "n", "0", "-2", "5"])
        config = self.config()
        self.assertEqual(config["runtime"]["quota_stop_percent"], 85)
        self.assertEqual(config["runtime"]["jobs_per_run"], 5)

    def test_bad_resume_paths_and_blank_tags_are_asked_again(self):
        text = self.root / "notes.pdf"
        text.write_text("plain text", encoding="utf-8")
        good = self.pdf("cv.pdf")
        self.wizard(["1", "1", "", "", str(self.root / "missing.pdf"), str(text), f'"{good}"', "", " , ", "SWE", "n", ""])
        self.assertEqual(self.config()["resumes"]["main_tags"], ["SWE"])
        self.assertEqual(sorted(p.name for p in (self.runtime_root / "resumes").iterdir()), ["cv.pdf"])

    def test_same_file_name_from_two_folders_is_not_overwritten(self):
        a, b = self.pdf("resume.pdf", "one"), self.pdf("resume.pdf", "two")
        self.wizard(["1", "1", "", str(a), "SWE", "y", str(b), "Data", "n", ""])
        resumes = self.config()["resumes"]
        self.assertEqual(Path(resumes["main_pdf"]).name, "resume.pdf")
        second = Path(resumes["alternatives"][0]["path"])
        self.assertEqual(second.name, "resume-2.pdf")
        self.assertEqual(second.read_bytes(), b.read_bytes())

    def test_end_of_input_stops_setup(self):
        with self.assertRaises(self.ts.SetupStopped):
            self.wizard(["1"])


class Rerun(Base):
    def test_blank_answers_keep_current_values_and_resumes(self):
        kept = self.pdf("kept.pdf", "runtime/resumes")
        self.write_json(
            "runtime/config.json",
            {
                "runtime": {"language": "en", "apply_engine": "codex", "quota_stop_percent": 70, "jobs_per_run": 4},
                "resumes": {"main_pdf": str(kept), "main_tags": ["ML"], "alternatives": []},
            },
        )
        self.wizard(["", "", "", "", "", ""])
        config = self.config()
        self.assertEqual(config["runtime"]["apply_engine"], "codex")
        self.assertEqual(config["runtime"]["quota_stop_percent"], 70)
        self.assertEqual(config["runtime"]["jobs_per_run"], 4)
        self.assertEqual(config["resumes"]["main_pdf"], str(kept))
        self.assertEqual(self.probes[0][0], "codex")

    def test_replacing_resumes_drops_the_old_role_mapping(self):
        old = self.pdf("old.pdf")
        self.write_json(
            "runtime/config.json",
            {"resumes": {"main_pdf": str(old), "alternatives": [], "role_mapping": {"swe": str(old)}}},
        )
        self.wizard(["1", "1", "", "n", str(self.pdf("new.pdf")), "SWE", "n", ""])
        resumes = self.config()["resumes"]
        self.assertEqual(Path(resumes["main_pdf"]).name, "new.pdf")
        self.assertNotIn("role_mapping", resumes)


class Launch(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.ts = self.module("terminal_setup")

    def calls(self, engine):
        seen = []

        def runner(argv, **kwargs):
            seen.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0)

        with patch.object(self.ts, "resolve_executable", lambda name: [f"/bin/{name}"]):
            code = self.ts.launch_engine(engine, self.root, runner=runner)
        self.assertEqual(code, 0)
        return seen

    def test_claude_opens_interactively_with_setup_command(self):
        [(argv, kwargs)] = self.calls("claude")
        self.assertEqual(argv, ["/bin/claude", "/setup"])
        self.assertEqual(kwargs["cwd"], self.root)
        self.assertNotIn("capture_output", kwargs)
        self.assertNotIn("--print", argv)

    def test_codex_opens_interactively_with_the_setup_skill(self):
        [(argv, _)] = self.calls("codex")
        self.assertEqual(argv[0], "/bin/codex")
        self.assertNotIn("exec", argv)
        self.assertIn("$autoapply-local setup", argv[-1])


class Wiring(ReleaseCase):
    def test_cli_setup_terminal_passes_flags(self):
        from autoapply_local import cli

        seen = {}

        def setup(**kwargs):
            seen.update(kwargs)
            return {"status": "ready"}

        code = cli.main(
            ["--user-dir", str(self.root), "setup", "--terminal", "--no-launch"],
            services={"setup": setup},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        self.assertEqual(code, 0)
        self.assertEqual(seen, {"root": self.root, "terminal": True, "launch": False})

    def test_no_launch_requires_terminal(self):
        err = io.StringIO()
        from autoapply_local import cli

        code = cli.main(["--user-dir", str(self.root), "setup", "--no-launch"], services={}, stdout=io.StringIO(), stderr=err)
        self.assertEqual(code, 2)

    def test_runtime_setup_terminal_creates_runtime_then_runs_the_wizard(self):
        runtime = self.module("runtime")
        ts = self.module("terminal_setup")
        seen = {}

        def fake_run(root, **kwargs):
            seen["root"], seen["kwargs"] = root, kwargs
            seen["config_exists"] = (Path(root) / "config.json").is_file()
            return {"status": "ready"}

        with patch.object(ts, "run", fake_run):
            result = runtime.setup(root=self.root, terminal=True, launch=False)
        self.assertEqual(result, {"status": "ready"})
        self.assertEqual(Path(seen["root"]), self.root)
        self.assertTrue(seen["config_exists"])
        self.assertIsNone(seen["kwargs"]["launch"])
