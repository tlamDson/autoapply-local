"""Bounded runtime-fix regressions; no live processes, connectors or user data.

Giả định của người thực thi: giữ API hiện có; lane/debug_url lưu trên job,
capacity refusal trả reason cùng partial results; radio giữ option value và
Boolean False chỉ được chấp nhận khi khớp đáp án đúng scope. Chỉ sửa test nên
ghi giả định ở đây, không sửa plan. Subprocess/native boundaries luôn là mock.

Giả định của người thực thi: intake có answers ở cấp document và first_name /
last_name ở contact; record index chỉ dùng cho binding đã quan sát, không suy từ
opaque ID; native fill nhận pdf và trả upload_receipt {name,size,sha256}; static
Review trả text cùng evidence {quote,start,end} cho từng field/attachment.
"""

import copy
import hashlib
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import Mock, patch

from test_support import RELEASE, ReleaseCase, candidate, fact, job, snapshot


WD = "https://example.wd5.myworkdayjobs.com/en-US/External/job/Remote/Engineer_JR123"
RP = "https://ats.rippling.com/example/jobs/11111111-2222-3333-4444-555555555555"
SCHEMA = {"type": "object", "required": ["status"], "properties": {"status": {"type": "string"}}}


def task(kind="apply_analysis", tools=()):
    return {"kind": kind, "allowed_tools": list(tools), "schema": SCHEMA, "prompt": "Synthetic task"}


def flag_values(argv, flag):
    return [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == flag]


def overrides(argv):
    # Parse actual CLI TOML overrides, including quoted server names.
    result = {}
    for item in flag_values(argv, "-c"):

        def merge(target, value):
            for key, child in value.items():
                if isinstance(child, dict):
                    merge(target.setdefault(key, {}), child)
                else:
                    target[key] = child

        merge(result, tomllib.loads(item))
    return result


class OnboardingCompatibilityTests(ReleaseCase):
    def test_generated_and_public_intake_have_bilingual_questions_for_all_fourteen_groups(self):
        intake = self.module("intake")
        intake.setup(self.root)
        generated = (self.root / "user/profile-intake.md").read_text(encoding="utf-8")
        questions = generated.split("```json", 1)[0]
        sections = re.split(r"(?m)^##\s+", questions)[1:]
        self.assertEqual(len(sections), 14, "Each group needs human-readable questions outside JSON")
        for section in sections:
            with self.subTest(group=section.splitlines()[0]):
                self.assertIn("?", section, "Technical key lists are not a questionnaire")
                self.assertRegex(section, r"[ăâđêôơưĂÂĐÊÔƠƯà-ỹ]", "Each group needs Vietnamese wording")
        for concept in (
            r"(?i)sponsor\w*.*now",
            r"(?i)sponsor\w*.*future",
            r"(?i)job.type",
            r"(?i)graduat",
            r"(?i)visa",
            r"(?i)authoriz",
            r"(?i)given|first name",
            r"(?i)family|last name",
            r"(?i)planned",
            r"(?i)pending",
            r"(?i)granted",
            r"(?i)CPT",
            r"(?i)OPT",
            r"(?i)STEM.?OPT",
            r"(?i)target.*role",
            r"(?i)sponsor.*filter|filter.*sponsor",
            r"(?i)tailor",
        ):
            with self.subTest(concept=concept):
                self.assertRegex(questions, concept)
        public = RELEASE / "templates/profile-intake.md"
        self.assertTrue(public.is_file(), "The public full questionnaire must exist")
        self.assertEqual(public.read_text(encoding="utf-8"), generated)
        self.assertEqual(intake.template_text(), generated)
        document = intake.read_intake(self.root / "user/profile-intake.md")
        self.assertEqual(len(document["groups"]), 14)
        for key in ("first_name", "last_name"):
            self.assertIn(key, document["groups"]["contact"])
            self.assertIsNone(document["groups"]["contact"][key])
        for key in ("authorized_now", "authorized_at_start", "sponsorship_now", "sponsorship_future"):
            self.assertIsNone(document["groups"]["authorization"][key])
        self.assertEqual(document.get("answers"), [], "No preconfirmed immigration answers")

    def test_normal_intake_preview_then_confirmation_persists_exact_scoped_answers_and_names(self):
        intake, runtime = self.module("intake"), self.module("runtime")
        intake.setup(self.root)
        path = self.root / "user/profile-intake.md"
        document = intake.read_intake(path)
        question = "Will you require sponsorship for this internship in the future?"
        scope = {
            "country": "US",
            "job_type": "internship",
            "employer": "Example Labs",
            "question": question,
            "effective_at": "2030-06-01",
        }
        document["groups"]["contact"].update(legal_name="Example Morgan", first_name="Morgan", last_name="Example")
        document["answers"] = [{"question": question, **fact(False, scope=scope)}]
        path.write_text("```json\n" + json.dumps(document) + "\n```\n", encoding="utf-8")
        before = (self.root / "profile.json").read_bytes()
        runtime.profile_update(root=self.root, path=path)
        self.assertEqual((self.root / "profile.json").read_bytes(), before)
        runtime.profile_update(root=self.root, path=path, confirm=True)
        profile = json.loads((self.root / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["contact"]["first_name"]["value"], "Morgan")
        self.assertEqual(profile["contact"]["last_name"]["value"], "Example")
        resolve = self.module("profile").resolve_answer
        answer = resolve(profile, question, scope)
        self.assertEqual(answer["status"], "confirmed")
        self.assertIs(answer["value"], False)
        for key, other in (
            ("job_type", "fulltime"),
            ("employer", "Other Labs"),
            ("effective_at", "2031-06-01"),
            ("country", "CA"),
        ):
            with self.subTest(changed_scope=key):
                self.assertNotEqual(resolve(profile, question, {**scope, key: other})["status"], "confirmed")
        different = "Are you currently authorized to work?"
        self.assertNotEqual(resolve(profile, different, {**scope, "question": different})["status"], "confirmed")


class IsolationTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        home = self.root / ".codex"
        home.mkdir()
        self.config = home / "config.toml"
        self.config.write_text(
            '[mcp_servers.personal_notes]\ncommand="unused"\n'
            '[mcp_servers."other.server"]\nurl="https://example.invalid/mcp"\n',
            encoding="utf-8",
        )
        self.original_config = self.config.read_bytes()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, CODEX_HOME=str(home), HOME=str(self.root), USERPROFILE=str(self.root)).start()
        patch.object(Path, "home", return_value=self.root).start()
        patch("subprocess.Popen", side_effect=AssertionError("No live engine")).start()

    def argv(self, name, payload, response=None):
        result = response or {"status": "completed"}
        if name == "claude":
            result = {"type": "result", "subtype": "success", "is_error": False, "structured_output": result}
        self.runner = Mock(return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(result), stderr=""))

        def process(argv, **kwargs):
            if "mcp" in argv and "list" in argv and "--json" in argv:
                inventory = [
                    {"name": server, "enabled": True, "transport": {"type": "stdio"}}
                    for server in ("personal_notes", "other.server")
                ]
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(inventory), stderr="")
            if name == "codex" and "exec" in argv:
                schema_path = Path(argv[argv.index("--output-schema") + 1])
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                if schema == {
                    "type": "object",
                    "properties": {"json_response": {"type": "string"}},
                    "required": ["json_response"],
                    "additionalProperties": False,
                }:
                    return subprocess.CompletedProcess(
                        argv, 0, stdout=json.dumps({"json_response": json.dumps(result)}), stderr=""
                    )
            return self.runner.return_value

        self.runner.side_effect = process
        engine = self.module("engines").Engine(
            name, runner=self.runner, cwd=self.root, debug_url="http://127.0.0.1:9333"
        )
        with (
            patch("autoapply_local.engines.resolve_executable", return_value=[name]),
            patch("autoapply_local.engines.probe_version", return_value=(2, 1, 312)),
        ):
            engine.run(payload)
        # The injected native inventory probe is additional metadata work;
        # exactly one model dispatch must still occur for the selected engine.
        dispatches = [
            call for call in self.runner.call_args_list if "exec" in call.args[0] or "--print" in call.args[0]
        ]
        self.assertEqual(len(dispatches), 1)
        argv = dispatches[0].args[0]
        self.assertEqual(argv[0], name)
        self.assertIs(dispatches[0].kwargs["shell"], False)
        for denied in ("--allowedTools", "--dangerously-skip-permissions", "--yolo", "--model", "-m"):
            self.assertNotIn(denied, argv)
        self.assertEqual(self.config.read_bytes(), self.original_config)
        return argv

    def test_analysis_and_tailor_disable_builtin_and_inherited_mcp_tools(self):
        for name in ("claude", "codex"):
            for kind in ("apply_analysis", "tailor"):
                with self.subTest(engine=name, kind=kind):
                    argv = self.argv(name, task(kind))
                    if name == "claude":
                        self.assertEqual(flag_values(argv, "--tools"), [""])
                        self.assertIn("--strict-mcp-config", argv)
                        configs = flag_values(argv, "--mcp-config")
                        self.assertEqual(len(configs), 1)
                        self.assertEqual(json.loads(configs[0]), {"mcpServers": {}})
                        self.assertIn("mcp__*", ",".join(flag_values(argv, "--disallowedTools")))
                    else:
                        config = overrides(argv)
                        for server in ("personal_notes", "other.server"):
                            self.assertIs(config.get("mcp_servers", {}).get(server, {}).get("enabled"), False)
                        self.assertIs(config.get("features", {}).get("shell_tool"), False)
                        self.assertIs(config.get("features", {}).get("apps"), False)

    def test_browser_uses_only_selected_server_and_operation_tools(self):
        allowed = "mcp__autoapply-local-browser__take_snapshot"
        for name in ("claude", "codex"):
            with self.subTest(engine=name):
                argv = self.argv(name, task("browser_evaluate", [allowed]))
                if name == "claude":
                    self.assertEqual(flag_values(argv, "--tools"), [""])
                    self.assertIn("--strict-mcp-config", argv)
                    config = json.loads(flag_values(argv, "--mcp-config")[0])
                    self.assertEqual(set(config["mcpServers"]), {"autoapply-local-browser"})
                    denied = ",".join(flag_values(argv, "--disallowedTools"))
                    for tool in ("click", "close_page", "new_page", "navigate_page", "evaluate_script"):
                        self.assertIn("mcp__autoapply-local-browser__" + tool, denied)
                    self.assertNotIn(allowed, denied)
                    server = config["mcpServers"]["autoapply-local-browser"]
                else:
                    config = overrides(argv)
                    for inherited in ("personal_notes", "other.server"):
                        self.assertIs(config.get("mcp_servers", {}).get(inherited, {}).get("enabled"), False)
                    server = config["mcp_servers"]["autoapply-local-browser"]
                    self.assertEqual(server["enabled_tools"], ["take_snapshot"])
                    self.assertIs(config.get("features", {}).get("shell_tool"), False)
                    self.assertIs(config.get("features", {}).get("apps"), False)
                self.assertIn("--browserUrl=http://127.0.0.1:9333", server["args"])


class IdentityTests(ReleaseCase):
    def bridge(self, original, current):
        client = Mock(spec=["new_page", "list_pages", "evaluate_script"])
        client.new_page.return_value = {"id": "held", "url": current}
        client.list_pages.return_value = [{"id": "held", "url": current}]
        client.evaluate_script.return_value = {"fields": [{"id": "name", "value": "Morgan Example"}]}
        return self.module("browser").BrowserBridge(client), client

    def test_new_page_and_restored_tab_accept_verified_apply_routes(self):
        for original, suffix in ((WD, "/apply"), (WD, "/applyManually"), (WD, "/apply/applyManually"), (RP, "/apply")):
            with self.subTest(url=original + suffix):
                current = original + suffix
                with self.subTest(phase="new_page"):
                    client = self.module("browser").EngineMCPClient(Mock(), "http://127.0.0.1:9333")
                    with (
                        patch.object(client, "list_pages", side_effect=[[], [{"id": "held", "url": current}]]),
                        patch.object(client, "_run", return_value={"status": "opened"}),
                    ):
                        self.assertEqual(client.new_page(original)["id"], "held")
                with self.subTest(phase="open"):
                    bridge, _ = self.bridge(original, current)
                    self.assertEqual(bridge.open(original), "held")
                with self.subTest(phase="restore"):
                    bridge, _ = self.bridge(original, current)
                    bridge.restore(job("route", url=original, tab_id="held"))
                    self.assertEqual(bridge.snapshot("held", "route")["url"], current)

    def test_different_job_tenant_host_query_identity_and_arbitrary_routes_are_rejected(self):
        embedded = "https://boards.greenhouse.io/embed/job_app?for=example&token=123"
        hosted = "https://careers.example.invalid/openings?gh_jid=123"
        pairs = [
            (WD, WD.replace("JR123", "JR124") + "/apply"),
            (WD, WD.replace("/External/", "/Other/") + "/apply"),
            (WD, WD.replace("example.wd5", "other.wd5") + "/apply"),
            (WD, WD + "/unrelated"),
            (WD, WD + "/apply/../../login"),
            (WD, "https://example.wd5.myworkdayjobs.com/login"),
            (WD, "not a URL"),
            (RP, RP.replace("555555555555", "666666666666") + "/apply"),
            (embedded, embedded.replace("token=123", "token=456")),
            (embedded, embedded.replace("for=example", "for=other")),
            (hosted, hosted.replace("gh_jid=123", "gh_jid=456")),
        ]
        for original, current in pairs:
            with self.subTest(url=current):
                bridge, _ = self.bridge(original, current)
                with self.assertRaises(ValueError):
                    bridge.open(original)
                bridge.restore(job("route", url=original, tab_id="held"))
                with self.assertRaises(ValueError):
                    bridge.snapshot("held", "route")


class WorkflowCompatibilityTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.profile = candidate("authorized")
        self.write_json("profile.json", self.profile)
        self.pdf = self.root / "resume.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\nSynthetic resume\n%%EOF")
        self.digest = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        self.store = self.module("storage").Store(self.root)

    def live(self, item, tab="tab-1", url=None):
        value = snapshot(item["id"], tab_id=tab, url=url or item["url"], resume_sha256=self.digest)
        value["fields"][0]["value"] = "Morgan Example"
        return value

    def held(self, item, live):
        self.store.enqueue(item)
        self.store.update(
            item["id"],
            state="filled",
            tab_id=live["tab_id"],
            pdf=str(self.pdf),
            resume_sha256=self.digest,
            lane="1",
            debug_url="http://127.0.0.1:9331",
        )
        bridge = Mock(spec=["snapshot", "submit", "close"])
        bridge.snapshot.side_effect = lambda *a, **k: copy.deepcopy(live)
        return self.module("workflow").Workflow(self.root, browser=bridge, engines={})

    def test_workflow_uses_same_apply_route_policy_as_native_bridge(self):
        for i, (url, suffix) in enumerate(((WD, "/apply"), (WD, "/apply/applyManually"), (RP, "/apply"))):
            with self.subTest(url=url + suffix):
                # Separate synthetic requisitions prevent Store dedup from
                # merging two independent route cases; assertions stay intact.
                url = url.replace("JR123", f"JR{123 + i}")
                item = job(f"route-{i}", url=url)
                runner = self.held(item, self.live(item, tab=f"tab-{i}", url=url + suffix))
                runner.review([item["id"]])
                self.assertEqual(self.store.get(item["id"])["state"], "reviewed")
                runner.browser.submit.assert_not_called()

    def test_contact_review_accepts_confirmed_values_and_rejects_wrong_or_unknown_components(self):
        self.profile["contact"].update(
            phone=fact("+1 202 555 0100"),
            city=fact("Example City"),
            first_name=fact("Morgan"),
            last_name=fact("Example"),
        )
        for index, (label, key) in enumerate(
            (("Phone", "phone"), ("City", "city"), ("First name", "first_name"), ("Last name", "last_name"))
        ):
            for state in ("correct", "wrong", "unknown"):
                with self.subTest(label=label, state=state):
                    profile = copy.deepcopy(self.profile)
                    value = profile["contact"][key]["value"]
                    if state == "unknown":
                        profile["contact"][key] = fact(None, "unknown")
                    self.write_json("profile.json", profile)
                    item = job(f"contact-{index}-{state}")
                    live = self.live(item)
                    live["fields"].append(
                        {
                            "id": key,
                            "label": label,
                            "type": "text",
                            "required": True,
                            "observed": True,
                            "status": "confirmed",
                            "value": value if state != "wrong" else "Wrong value",
                        }
                    )
                    runner = self.held(item, live)
                    if state == "correct":
                        runner.review([item["id"]])
                        self.assertEqual(self.store.get(item["id"])["state"], "reviewed")
                    else:
                        with self.assertRaises(ValueError):
                            runner.review([item["id"]])
                    runner.browser.submit.assert_not_called()

    def test_record_review_respects_observed_education_and_experience_binding(self):
        self.profile["education"].append({"id": "education:2", "school": fact("Second University")})
        self.profile["experience"].append({"id": "experience:2", "title": fact("Research Assistant")})
        self.write_json("profile.json", self.profile)
        for kind, label, value in (
            ("education", "School", "Example University"),
            ("experience", "Job title", "Engineering Intern"),
        ):
            for binding in (0, 1, "opaque"):
                with self.subTest(kind=kind, binding=binding):
                    item = job(f"record-{kind}-{binding}")
                    live = self.live(item)
                    record = (
                        {"kind": kind, "index": binding} if binding != "opaque" else {"kind": kind, "id": "opaque-72"}
                    )
                    live["fields"].append(
                        {
                            "id": "record-field",
                            "label": label,
                            "required": True,
                            "value": value,
                            "observed": True,
                            "record": record,
                        }
                    )
                    runner = self.held(item, live)
                    if binding == 0:
                        runner.review([item["id"]])
                        self.assertEqual(self.store.get(item["id"])["state"], "reviewed")
                    else:
                        with self.assertRaises(ValueError):
                            runner.review([item["id"]])
                    runner.browser.submit.assert_not_called()

    def test_injected_fill_uses_confirmed_contact_and_record_facts_without_guessing_names(self):
        self.profile["contact"].update(
            phone=fact("+1 202 555 0100"),
            city=fact("Example City"),
            first_name=fact("Morgan"),
            last_name=fact(None, "unknown"),
        )
        fields = [
            {"id": "phone", "label": "Phone"},
            {"id": "city", "label": "City"},
            {"id": "given", "label": "First name"},
            {"id": "family", "label": "Last name"},
            {"id": "school", "label": "School", "record": {"kind": "education", "index": 0}},
            {"id": "title", "label": "Job title", "record": {"kind": "experience", "index": 0}},
            {"id": "auth", "label": "Are you currently authorized to work?"},
        ]
        supplied = {}

        def evaluate(tab, function, *, operation="audit"):
            if ".fill(document, " not in function:
                return {"fields": fields}
            arguments = function.split(".fill(document, ", 1)[1].rsplit(");", 1)[0]
            supplied.update(json.loads(arguments))
            return {"filled": list(supplied), "blocked": [], "uploads": []}

        item = job("injected-fill")
        client = Mock(spec=["new_page", "list_pages", "evaluate_script", "upload_file"])
        client.new_page.return_value = {"id": "fill-tab", "url": item["url"]}
        client.list_pages.return_value = [{"id": "fill-tab", "url": item["url"]}]
        client.evaluate_script.side_effect = evaluate
        bridge = self.module("browser").BrowserBridge(client)
        tab = bridge.open(item["url"])
        bridge.fill(tab, profile=self.profile, job=item)
        self.assertEqual(
            {key: answer["value"] for key, answer in supplied.items()},
            {
                "phone": "+1 202 555 0100",
                "city": "Example City",
                "given": "Morgan",
                "school": "Example University",
                "title": "Engineering Intern",
            },
        )
        client.upload_file.assert_not_called()

    def test_confirmed_field_status_and_similar_auth_wording_never_replace_exact_scope(self):
        question = "Do you require sponsorship now?"
        scope = {
            "country": "US",
            "job_type": "internship",
            "employer": "Example Labs",
            "question": question,
            "effective_at": "2030-06-01",
        }
        self.profile["answers"] = [{"question": question, **fact("No", scope=scope)}]
        self.write_json("profile.json", self.profile)
        for index, label in enumerate(("Do you require future sponsorship?", "Are you authorized to work now?")):
            item = job(f"auth-meaning-{index}", start_date="2030-06-01")
            live = self.live(item)
            live["fields"].append(
                {
                    "id": "auth",
                    "label": label,
                    "type": "radio",
                    "required": True,
                    "value": "No",
                    "observed": True,
                    "status": "confirmed",
                }
            )
            runner = self.held(item, live)
            with self.assertRaises(ValueError):
                runner.review([item["id"]])
            runner.browser.submit.assert_not_called()

    def test_manual_continuation_preserves_same_tab_and_never_uploads_while_blocked(self):
        self.configure(cap=1)
        for reason in ("login", "captcha", "unknown_data"):
            with self.subTest(reason=reason):
                item = job("continue-" + reason)
                self.held(item, self.live(item, tab="held-" + reason))
                self.store.update(item["id"], state="needs_manual", problem=reason)
                record = self.store.get(item["id"])
                self.bridges = {}
                browser, engines = self.module("browser"), self.module("engines")
                resolved = False

                def boundary(**kwargs):
                    bridge = self.boundary(**kwargs)
                    bridge.snapshot.side_effect = lambda tab, job_id: {
                        **self.live(item, tab),
                        "required_unknown": [] if resolved else [reason],
                    }
                    bridge.fill.side_effect = lambda *a, **k: {
                        "status": "filled" if resolved else "needs_manual",
                        "filled": [],
                        "blocked": [] if resolved else [{"reason": reason}],
                    }
                    return bridge

                with (
                    patch.object(browser, "launch_chrome", side_effect=AssertionError("Reuse held browser")),
                    patch.object(browser, "available_memory_mb", return_value=0),
                    patch.object(browser, "BrowserBridge", side_effect=boundary),
                    patch.object(engines, "Engine", return_value=self.fake_engine()),
                ):
                    blocked_report = self.module("runtime").apply(root=self.root, selection=[item["id"]])
                    blocked = self.store.get(item["id"])
                    self.assertEqual(blocked["state"], "needs_manual")
                    self.assertTrue(blocked.get("problem"))
                    self.assertTrue(blocked_report["results"])
                    for bridge in self.bridges.values():
                        bridge.upload_file.assert_not_called()
                    resolved = True
                    report = self.module("runtime").apply(root=self.root, selection=[item["id"]])
                resumed = self.store.get(item["id"])
                self.assertEqual(resumed["state"], "filled")
                self.assertEqual(resumed["tab_id"], record["tab_id"])
                self.assertEqual(resumed["debug_url"], record["debug_url"])
                self.assertTrue(any(tab["tab_id"] == record["tab_id"] for tab in report["held_tabs"]))
                owner = self.bridges[record["debug_url"]]
                owner.restore.assert_any_call(record)
                owner.open.assert_not_called()
                owner.close.assert_not_called()
                owner.submit.assert_not_called()

    def test_manual_continuation_cannot_retry_submission_intent_or_uncertain_outcome(self):
        self.configure()
        for state in ("submit_intent", "needs_reconciliation"):
            with self.subTest(state=state):
                item = job("no-retry-" + state)
                self.held(item, self.live(item))
                self.store.update(item["id"], state=state)
                self.bridges = {}
                browser, engines = self.module("browser"), self.module("engines")
                with (
                    patch.object(
                        browser, "launch_chrome", return_value=[{"lane": 1, "debug_url": "http://127.0.0.1:9331"}]
                    ) as launch,
                    patch.object(browser, "BrowserBridge", side_effect=self.boundary),
                    patch.object(engines, "Engine", return_value=self.fake_engine()),
                ):
                    with self.assertRaises(ValueError):
                        self.module("runtime").apply(root=self.root, selection=[item["id"]])
                launch.assert_not_called()
                self.assertEqual(self.store.get(item["id"])["state"], state)
                for bridge in self.bridges.values():
                    bridge.fill.assert_not_called()
                    bridge.upload_file.assert_not_called()
                    bridge.open.assert_not_called()
                    bridge.submit.assert_not_called()

    def test_native_multipage_fill_gets_pdf_and_uploads_before_leaving_resume_page(self):
        browser = self.module("browser")
        item = job("multipage", ats="workday")
        self.store.enqueue(item)
        phase, events = {"page": "Resume"}, []
        receipt = {"name": self.pdf.name, "size": self.pdf.stat().st_size, "sha256": self.digest}

        def native_task(payload):
            operation = json.loads(payload["prompt"][payload["prompt"].index("{") :])
            if payload["kind"] == "browser_native_fill":
                self.assertEqual(operation.get("pdf"), str(self.pdf), "Native multipage fill needs the selected PDF")
                self.assertIn("mcp__autoapply-local-browser__upload_file", payload["allowed_tools"])
                instructions = operation["instructions"].casefold()
                self.assertRegex(instructions, r"upload.*before.*(?:next|advanc|leav)")
                for concept in ("observ", "sha256", "size", "resume"):
                    self.assertIn(concept, instructions)
                self.assertEqual(phase["page"], "Resume")
                # Synthetic native executor: the file input exists only here.
                events.extend([("upload", "Resume"), ("observe_receipt", "Resume"), ("Next", "Resume")])
                phase["page"] = "Review"
                return {"status": "filled", "filled": ["name"], "blocked": [], "upload_receipt": receipt}
            if payload["kind"] == "browser_upload":
                self.fail("Workflow attempted a second upload after Next removed the file input")
            if payload["kind"] == "browser_evaluate":
                self.assertEqual(phase["page"], "Review")
                return {"result": self.static_review(receipt)}
            self.fail("Unexpected native task: " + payload["kind"])

        engine = Mock(spec=["run"])
        engine.run.side_effect = native_task
        client = browser.EngineMCPClient(engine, "http://127.0.0.1:9331")
        with (
            patch.object(client, "new_page", return_value={"id": "multipage-tab", "url": item["url"]}),
            patch.object(client, "list_pages", return_value=[{"id": "multipage-tab", "url": item["url"]}]),
        ):
            bridge = browser.BrowserBridge(client)
            runner = self.module("workflow").Workflow(self.root, bridge, {"codex": self.fake_engine()})
            runner.apply([item["id"]], pdf=self.pdf)
            self.assertEqual(events, [("upload", "Resume"), ("observe_receipt", "Resume"), ("Next", "Resume")])
            self.assertEqual(self.store.get(item["id"])["state"], "filled")
            self.assertEqual(self.store.get(item["id"]).get("upload_receipt"), receipt)
            runner.review([item["id"]])
            self.assertEqual(self.store.get(item["id"])["state"], "reviewed")
        self.assertNotIn("submit", [call.args[0]["kind"] for call in engine.run.call_args_list])

    def static_review(self, receipt, name="Morgan Example"):
        text = f"Legal name: {name}\nResume: {receipt['name']}"
        name_quote = f"Legal name: {name}"
        file_quote = f"Resume: {receipt['name']}"
        return {
            "text": text,
            "fields": [
                {
                    "id": "summary-name",
                    "label": "Legal name",
                    "value": name,
                    "required": True,
                    "observed": True,
                    "type": "summary",
                    "evidence": {"quote": name_quote, "start": 0, "end": len(name_quote)},
                }
            ],
            "attachments": [
                {
                    **receipt,
                    "observed": True,
                    "evidence": {"quote": file_quote, "start": len(name_quote) + 1, "end": len(text)},
                }
            ],
            "errors": [],
            "required_unknown": [],
        }

    def test_static_review_needs_fresh_matching_summary_pdf_and_observed_upload_receipt(self):
        browser = self.module("browser")
        for variant in ("correct", "wrong_summary", "wrong_pdf", "missing_receipt", "missing_attachment", "bad_span"):
            with self.subTest(variant=variant):
                item = job("static-" + variant, ats="workday")
                tab = "static-tab-" + variant
                receipt = {"name": self.pdf.name, "size": self.pdf.stat().st_size, "sha256": self.digest}
                live = self.static_review(receipt, "Wrong Person" if variant == "wrong_summary" else "Morgan Example")
                if variant == "wrong_pdf":
                    live["attachments"][0]["sha256"] = "0" * 64
                if variant == "missing_attachment":
                    live["attachments"] = []
                if variant == "bad_span":
                    live["fields"][0]["evidence"]["start"] = 7
                # An echoed expected hash must never make any negative case pass.
                live["resume_sha256"] = self.digest
                self.held(item, self.live(item, tab=tab))
                if variant != "missing_receipt":
                    self.store.update(item["id"], upload_receipt=receipt)
                client = Mock(spec=["list_pages", "evaluate_script", "upload_file", "new_page", "close_page"])
                client.list_pages.return_value = [{"id": tab, "url": item["url"]}]
                client.evaluate_script.side_effect = lambda *a, **k: copy.deepcopy(live)
                bridge = browser.BrowserBridge(client)
                bridge.restore(self.store.get(item["id"]))
                runner = self.module("workflow").Workflow(self.root, bridge, {})
                if variant == "correct":
                    runner.review([item["id"]])
                    self.assertEqual(self.store.get(item["id"])["state"], "reviewed")
                    client.evaluate_script.assert_called()
                    self.assertNotIn(self.digest, repr(client.evaluate_script.call_args_list))
                else:
                    with self.assertRaises(ValueError):
                        runner.review([item["id"]])
                    self.assertNotEqual(self.store.get(item["id"])["state"], "reviewed")
                client.new_page.assert_not_called()
                client.upload_file.assert_not_called()
                client.close_page.assert_not_called()

    def test_radio_no_boolean_no_unknown_and_unchecked_consent_have_distinct_meanings(self):
        for i, (kind, value, expected) in enumerate(
            (
                ("radio", "No", True),
                ("radio", False, True),
                ("radio", None, False),
                ("checkbox", False, False),
            )
        ):
            with self.subTest(type=kind, value=value):
                question = "Do you require future sponsorship?" if kind == "radio" else "I consent to retention"
                item = job(f"answer-{i}", start_date="2030-06-01")
                scope = {
                    "country": "US",
                    "job_type": "internship",
                    "employer": item["company"],
                    "question": question,
                    "effective_at": item["start_date"],
                }
                self.profile["answers"] = [{"question": question, **fact(value, scope=scope)}]
                self.write_json("profile.json", self.profile)
                live = self.live(item, tab=f"answer-tab-{i}")
                live["fields"].append(
                    {
                        "id": "answer",
                        "type": kind,
                        "label": question,
                        "required": True,
                        "observed": True,
                        "value": value,
                        "options": [{"value": "Yes", "label": "Yes"}, {"value": "No", "label": "No"}],
                    }
                )
                runner = self.held(item, live)
                if expected:
                    runner.review([item["id"]])
                    self.assertEqual(self.store.get(item["id"])["state"], "reviewed")
                else:
                    with self.assertRaises(ValueError):
                        runner.review([item["id"]])
                runner.browser.submit.assert_not_called()

    def configure(self, lanes=1, cap=5):
        self.write_json(
            "config.json",
            {
                "runtime": {"apply_engine": "codex"},
                "resumes": {"main_pdf": str(self.pdf)},
                "resources": {"lanes": lanes, "multiple_lanes_opt_in": lanes > 1, "held_tab_cap": cap},
            },
        )
        return [
            {
                "lane": i,
                "debug_url": f"http://127.0.0.1:{9330 + i}",
                "profile_dir": str(self.root / f"browser/lane-{i}"),
            }
            for i in range(1, lanes + 1)
        ]

    def boundary(self, **kwargs):
        endpoint = kwargs["debug_url"]
        bridge = Mock(spec=["open", "fill", "upload_file", "snapshot", "restore", "submit", "close"])
        bridge.open.side_effect = lambda url: endpoint + "/" + url.rsplit("/", 1)[-1]
        bridge.snapshot.side_effect = lambda tab, job_id: self.live(self.store.get(job_id), tab)
        bridge.fill.return_value = {"status": "filled"}
        bridge.submit.side_effect = lambda tab, job_id: {
            "status": "confirmed",
            "job_id": job_id,
            "url": self.store.get(job_id)["url"] + "/confirmation",
            "text": "Application received",
        }
        self.bridges[endpoint] = bridge
        return bridge

    def test_two_and_three_lanes_persist_assignment_and_restore_review_submit_to_owner(self):
        for count in (2, 3):
            with self.subTest(lanes=count):
                lanes = self.configure(count, cap=count)
                ids = [f"lane-{count}-{i}" for i in range(count)]
                for job_id in ids:
                    self.store.enqueue(job(job_id))
                self.bridges = {}
                browser, engines, runtime = self.module("browser"), self.module("engines"), self.module("runtime")
                with (
                    patch.object(browser, "launch_chrome", return_value=lanes),
                    patch.object(browser, "available_memory_mb", return_value=16384),
                    patch.object(browser, "BrowserBridge", side_effect=self.boundary),
                    patch.object(engines, "Engine", side_effect=lambda *a, **k: self.fake_engine()),
                ):
                    runtime.apply(root=self.root, selection=ids)
                    records = [self.store.get(job_id) for job_id in ids]
                    self.assertEqual({str(record["lane"]) for record in records}, {str(i) for i in range(1, count + 1)})
                    for record in records:
                        self.assertEqual(record["debug_url"], lanes[int(record["lane"]) - 1]["debug_url"])
                        self.assertEqual(record["engine"], "codex")
                    self.bridges = {}
                    with patch.object(
                        browser, "launch_chrome", side_effect=AssertionError("Held review must not launch")
                    ):
                        runtime.review(root=self.root, selection=ids)
                        for record in records:
                            owner = self.bridges[record["debug_url"]]
                            owner.restore.assert_any_call(record)
                            owner.snapshot.assert_any_call(record["tab_id"], job_id=record["id"])
                            for endpoint, bridge in self.bridges.items():
                                if endpoint != record["debug_url"]:
                                    self.assertNotIn(
                                        record["tab_id"], [call.args[0] for call in bridge.snapshot.call_args_list]
                                    )
                        runtime.submit(root=self.root, selection=ids)
                        for record in records:
                            self.bridges[record["debug_url"]].submit.assert_called_with(
                                record["tab_id"], job_id=record["id"]
                            )

    def test_unsupported_login_redirect_retains_tab_and_actionable_manual_report(self):
        lanes = self.configure()
        item = job("login-redirect", ats="workday", url=WD)
        self.store.enqueue(item)
        client = Mock(spec=["new_page", "list_pages", "evaluate_script"])
        login = "https://example.wd5.myworkdayjobs.com/en-US/External/login"
        client.new_page.return_value = {"id": "login-tab", "url": login}
        client.list_pages.return_value = [{"id": "login-tab", "url": login}]
        browser, engines = self.module("browser"), self.module("engines")
        bridge = browser.BrowserBridge(client)
        with (
            patch.object(browser, "launch_chrome", return_value=lanes),
            patch.object(browser, "available_memory_mb", return_value=16384),
            patch.object(browser, "BrowserBridge", return_value=bridge),
            patch.object(engines, "Engine", return_value=self.fake_engine()),
        ):
            report = self.module("runtime").apply(root=self.root, selection=[item["id"]])
        record = self.store.get(item["id"])
        self.assertEqual(record["state"], "needs_manual")
        self.assertEqual(record["tab_id"], "login-tab")
        self.assertEqual(record["url"], WD)
        self.assertTrue(report["results"])
        self.assertTrue(record.get("problem"))
        self.assertEqual(report["held_tabs"][0]["tab_id"], "login-tab")
        client.evaluate_script.assert_not_called()

    def test_capacity_is_checked_before_each_job_and_partial_report_is_retained(self):
        for reason in ("cap", "memory"):
            with self.subTest(reason=reason):
                root_before = self.root
                self.root = root_before / reason
                self.root.mkdir()
                self.write_json("profile.json", self.profile)
                self.store = self.module("storage").Store(self.root)
                lanes = self.configure(cap=1 if reason == "cap" else 5)
                for job_id in ("first", "second", "third"):
                    self.store.enqueue(job(job_id))
                self.bridges = {}
                browser, engines = self.module("browser"), self.module("engines")
                opened = []

                def boundary(**kwargs):
                    bridge = self.boundary(**kwargs)

                    def open_tab(url):
                        opened.append(url)
                        return "capacity-tab"

                    bridge.open.side_effect = open_tab
                    return bridge

                with (
                    patch.object(browser, "launch_chrome", return_value=lanes),
                    patch.object(
                        browser,
                        "available_memory_mb",
                        side_effect=lambda: 16384 if not opened or reason == "cap" else 0,
                    ) as memory,
                    patch.object(browser, "BrowserBridge", side_effect=boundary),
                    patch.object(engines, "Engine", side_effect=lambda *a, **k: self.fake_engine()),
                ):
                    report = self.module("runtime").apply(root=self.root, selection=3)
                self.assertEqual(len(opened), 1)
                self.assertEqual(self.store.get("first")["state"], "filled")
                self.assertEqual(self.store.get("second")["state"], "pending")
                self.assertEqual(self.store.get("third")["state"], "pending")
                self.assertTrue(report["results"])
                self.assertTrue(
                    report.get("reason"), "Capacity exhaustion needs an actionable reason with partial results"
                )
                self.assertEqual(len(report["held_tabs"]), 1)
                if reason == "memory":
                    self.assertGreaterEqual(memory.call_count, 2)
                self.root = root_before

    def test_apply_of_already_held_selection_at_cap_never_launches_profiles(self):
        self.configure(cap=1)
        item = job("held-only")
        self.held(item, self.live(item))
        self.write_json("browser/active-lanes.json", [{"lane": 1, "debug_url": "http://127.0.0.1:9331"}])
        self.bridges = {}
        browser, engines = self.module("browser"), self.module("engines")
        with (
            patch.object(
                browser, "launch_chrome", return_value=[{"lane": 1, "debug_url": "http://127.0.0.1:9331"}]
            ) as launch,
            patch.object(browser, "available_memory_mb", return_value=0),
            patch.object(browser, "BrowserBridge", side_effect=self.boundary),
            patch.object(engines, "Engine", side_effect=lambda *a, **k: self.fake_engine()),
        ):
            report = self.module("runtime").apply(root=self.root, selection=[item["id"]])
        launch.assert_not_called()
        self.assertEqual(len(report["held_tabs"]), 1)
        for bridge in self.bridges.values():
            bridge.open.assert_not_called()
            bridge.close.assert_not_called()


class NativeMailCompatibilityTests(ReleaseCase):
    def test_verified_account_uses_explicit_discovered_read_search_tools_on_each_engine(self):
        account = "alex@example.invalid"
        for name in ("claude", "codex"):
            with self.subTest(engine=name):
                engines = self.module("engines")
                capabilities = {
                    "accounts": [account],
                    "permissions": ["read", "search"],
                    "search_tool": "mcp__gmail__search_email_ids",
                    "read_tool": "mcp__gmail__read_email",
                }
                message = {"id": "message-1", "account": account, "subject": "Unrelated", "body": "Synthetic"}
                tasks = []

                def process(argv, **kwargs):
                    if "mcp" in argv and "list" in argv:
                        return subprocess.CompletedProcess(argv, 0, "[]", "")
                    payload = json.loads(kwargs["input"].split("\n", 1)[1])
                    tasks.append(payload)
                    response = {
                        "mail_discovery": {"gmail": capabilities},
                        "mail_search": {"messages": [{"id": "message-1", "account": account}]},
                        "mail_read": message,
                    }[payload["kind"]]
                    if name == "claude":
                        response = {
                            "type": "result",
                            "subtype": "success",
                            "is_error": False,
                            "structured_output": response,
                        }
                    return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

                runner = Mock(side_effect=process)
                engine = engines.Engine(name, runner=runner, cwd=self.root)
                native = self.module("runtime").NativeMailRuntime(engine)
                checker = self.module("gmail").MailChecker(self.root, native)
                with (
                    patch.object(engines, "resolve_executable", return_value=["synthetic-" + name]),
                    patch.object(engines, "probe_version", return_value=(2, 1, 312)),
                    patch.object(engines, "_app_plugin_names", return_value=({}, set())),
                    patch("subprocess.Popen", side_effect=AssertionError("No live engine")),
                ):
                    report = checker.check(
                        {"gmail_opt_in": True, "gmail_account": account, "gmail_permissions": ["read", "search"]}
                    )
                    if name == "codex":
                        self.assertEqual(report["status"], "manual_required")
                        self.assertEqual(report["events"], [])
                        self.assertEqual([item["kind"] for item in tasks], ["mail_discovery"])
                        for kind, tool in (
                            ("mail_search", capabilities["search_tool"]),
                            ("mail_read", capabilities["read_tool"]),
                        ):
                            runner.reset_mock()
                            with self.assertRaises(engines.EngineError) as caught:
                                engine.run(
                                    {
                                        "kind": kind,
                                        "allowed_tools": [tool],
                                        "schema": task()["schema"],
                                        "prompt": json.dumps({"account": account}),
                                    }
                                )
                            self.assertEqual(caught.exception.category, "configuration")
                            self.assertIn("manual_required", str(caught.exception))
                            runner.assert_not_called()
                    else:
                        self.assertEqual(report["status"], "ok")
                        self.assertEqual(
                            [item["kind"] for item in tasks], ["mail_discovery", "mail_search", "mail_read"]
                        )
                        self.assertEqual(tasks[1]["allowed_tools"], [capabilities["search_tool"]])
                        self.assertEqual(tasks[2]["allowed_tools"], [capabilities["read_tool"]])
                        self.assertEqual(json.loads(tasks[1]["prompt"])["account"], account)
                        self.assertEqual(json.loads(tasks[2]["prompt"])["account"], account)
                self.assertNotEqual(
                    set(tasks[0]["allowed_tools"]),
                    {"list_mcp_resources", "list_mcp_resource_templates"},
                    "Resource enumeration alone cannot discover callable Gmail tools/accounts",
                )

    def test_discovered_write_or_non_gmail_tool_cannot_be_dispatched_as_read_or_search(self):
        for engine_name in ("claude", "codex"):
            for action in ("read", "search"):
                for tool in ("mcp__gmail__send_email", "mcp__gmail__draft_email", "mcp__drive__read_file"):
                    with self.subTest(engine=engine_name, action=action, tool=tool):
                        engine = Mock(name=engine_name, spec=["name", "run"])
                        engine.name = engine_name
                        engine.run.return_value = {"messages": []}
                        native = self.module("runtime").NativeMailRuntime(engine)
                        native.capabilities = {
                            "accounts": ["alex@example.invalid"],
                            "permissions": ["read", "search"],
                            "read_tool": tool,
                            "search_tool": tool,
                        }
                        with self.assertRaises(PermissionError):
                            if action == "read":
                                native.read_mail("alex@example.invalid", "message-1")
                            else:
                                native.search_mail("alex@example.invalid", "application", 7)
                        engine.run.assert_not_called()

    def test_opt_in_precedes_discovery_and_missing_account_never_reads_mail(self):
        runtime = self.module("runtime")
        for name in ("claude", "codex"):
            for enabled, accounts in ((False, ["alex@example.invalid"]), (True, []), (True, ["other@example.invalid"])):
                with self.subTest(engine=name, enabled=enabled, accounts=accounts):
                    self.write_json(
                        "config.json",
                        {
                            "runtime": {"apply_engine": name},
                            "resources": {
                                "gmail_opt_in": enabled,
                                "gmail_account": "alex@example.invalid",
                                "gmail_permissions": ["read", "search"],
                            },
                        },
                    )
                    engine = Mock(spec=["name", "run"])
                    engine.name = name
                    engine.run.return_value = {
                        "gmail": {
                            "accounts": accounts,
                            "permissions": ["read", "search"],
                            "search_tool": "",
                            "read_tool": "",
                        }
                    }
                    with patch("autoapply_local.engines.Engine", return_value=engine):
                        report = runtime.mail_check(root=self.root)
                    self.assertEqual(report["status"], "manual_required" if enabled else "disabled")
                    self.assertEqual(
                        [call.args[0]["kind"] for call in engine.run.call_args_list],
                        ["mail_discovery"] if enabled else [],
                    )

    def test_resource_listing_alone_does_not_establish_gmail_readiness(self):
        for name in ("claude", "codex"):
            with self.subTest(engine=name):
                engine = Mock(spec=["name", "run"])
                engine.name = name
                engine.run.return_value = {
                    "gmail": {
                        "accounts": ["alex@example.invalid"],
                        "permissions": ["read", "search"],
                        "read_tool": "list_mcp_resources",
                        "search_tool": "list_mcp_resource_templates",
                    }
                }
                checker = self.module("gmail").MailChecker(self.root, self.module("runtime").NativeMailRuntime(engine))
                report = checker.check(
                    {
                        "gmail_opt_in": True,
                        "gmail_account": "alex@example.invalid",
                        "gmail_permissions": ["read", "search"],
                    }
                )
                self.assertEqual(report["status"], "manual_required")
                self.assertEqual([call.args[0]["kind"] for call in engine.run.call_args_list], ["mail_discovery"])


class PrereleaseMetadataTests(ReleaseCase):
    def test_package_and_project_are_the_requested_alpha_version(self):
        project = tomllib.loads((RELEASE / "pyproject.toml").read_text(encoding="utf-8"))
        import autoapply_local

        self.assertEqual(project["project"]["version"], "0.1.0a1")
        self.assertEqual(autoapply_local.__version__, "0.1.0a1")

    def test_new_regression_module_is_explicitly_export_allowlisted(self):
        allowed = json.loads((RELEASE / "export-allowlist.json").read_text(encoding="utf-8"))
        self.assertIn("tests/test_runtime_compatibility.py", allowed)
