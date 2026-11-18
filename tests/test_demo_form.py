"""Offline demo form (plan aa-09, flow 3.3 step 5).

Setup fills a local Greenhouse-style demo page in the tool's own Chrome with the
Greenhouse script driver, to prove Chrome, the adapter and the driver work
before any real job. The page has no Submit control, loads nothing from the
network and stays open. The browser is a fake CDP module; the candidate is invented.
"""

import io
import json
import re
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path
from unittest.mock import Mock

from test_support import ReleaseCase, fact

DEBUG_URL = "http://127.0.0.1:9333"


def spec():
    return json.loads(files("autoapply_local").joinpath("data/demo_form.json").read_text(encoding="utf-8"))


def candidate():
    return {
        "schema_version": 1,
        "revision": 1,
        "contact": {
            "first_name": fact("Alex"),
            "last_name": fact("Rivera"),
            "application_email": fact("alex.rivera@example.com"),
            "phone": fact("+1 555 0100"),
            "linkedin": fact("https://www.linkedin.com/in/alex-rivera-example"),
            "portfolio": fact(None, status="unknown"),
        },
    }


def recorded_scan():
    """The Greenhouse adapter's scan of the demo page (adapters.test.cjs asserts the same shape)."""
    fields = []
    for item in spec()["fields"]:
        label = item["label"]
        fields.append({
            "id": item["attrs"]["id"],
            "label": label.rstrip("*").strip(),
            "type": item["attrs"].get("type", item["tag"]),
            "required": label.endswith("*"),
            "value": "",
            "options": [],
        })
    return {"fields": fields}


class FakeCDP:
    CDPError = RuntimeError

    def __init__(self):
        self.opened, self.expressions, self.fill_result, self.events = [], [], None, []
        self.not_ready = None

    def wait_ready(self, debug_url, timeout=20):
        self.events.append(("wait_ready", debug_url))
        if self.not_ready:
            raise self.not_ready

    def open_page(self, debug_url, url):
        self.events.append(("open_page", debug_url))
        self.opened.append((debug_url, url))
        self.page = {"id": "T1", "url": url, "ws": "ws://127.0.0.1:9333/devtools/page/T1"}
        return dict(self.page)

    def pages(self, debug_url):
        return [dict(self.page)] if self.opened else []

    def evaluate(self, ws, expression, timeout=60):
        self.expressions.append(expression)
        if "AutoApplyGreenhouse.audit" in expression:
            return recorded_scan()
        filled = [key for key in ("first_name", "last_name", "email", "phone", "question_1") if f'"{key}"' in expression]
        return self.fill_result or {"filled": filled, "blocked": [], "uploads": []}


NODE_PAGE = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const { Input, TextArea, makeDocument, addField } = require(input.fake_dom);
const { document, form, submit } = makeDocument();
const controls = {};
for (const item of input.fields) {
  const control = item.tag === 'textarea' ? new TextArea(item.attrs) : new Input(item.attrs);
  controls[item.attrs.id] = addField(form, control, item.label);
}
const context = vm.createContext({ document, location: { href: input.href } });
(async () => {
  const result = await vm.runInContext(input.expression, context, { timeout: 5000 });
  const values = Object.fromEntries(Object.entries(controls).map(([id, c]) => [id, c.value]));
  const writes = Object.values(controls).reduce((sum, c) => sum + c.nativeWrites, 0);
  process.stdout.write(JSON.stringify({ result, values, writes, clicks: submit.clicks }));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


class NodePageCDP(FakeCDP):
    """Runs each real wrapped script in Node against a synthetic copy of the demo page.

    ``hrefs`` gives the document URL the page reports for each script in turn,
    so a test can move the tab to another document after the audit while the
    target list (``pages``) still reports the demo URL.
    """

    def __init__(self, hrefs):
        super().__init__()
        self.hrefs, self.pages_seen = list(hrefs), []
        self.node = shutil.which("node")

    def evaluate(self, ws, expression, timeout=60):
        self.expressions.append(expression)
        href = self.hrefs.pop(0) if self.hrefs else self.page["url"]
        payload = {
            "fake_dom": str(Path(__file__).with_name("fake_dom.cjs")),
            "fields": spec()["fields"],
            "href": self.page["url"] if href is None else href,
            "expression": expression,
        }
        done = subprocess.run([self.node, "-e", NODE_PAGE], input=json.dumps(payload), capture_output=True,
                              text=True, encoding="utf-8", timeout=60, shell=False)
        if done.returncode:
            raise AssertionError(done.stderr[-2000:])
        page = json.loads(done.stdout)
        self.pages_seen.append(page)
        return page["result"]


class DemoPageTests(ReleaseCase):
    def test_spec_is_a_greenhouse_style_form_with_standard_ids(self):
        ids = [item["attrs"]["id"] for item in spec()["fields"]]
        for expected in ("first_name", "last_name", "email", "phone", "resume"):
            self.assertIn(expected, ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(any(item["attrs"].get("type") == "submit" for item in spec()["fields"]))

    def test_rendered_page_is_offline_and_has_no_submit_control(self):
        page = self.module("demo_form").render()
        for item in spec()["fields"]:
            with self.subTest(field=item["attrs"]["id"]):
                self.assertIn(f'<label for="{item["attrs"]["id"]}">', page)
                self.assertIn(f'id="{item["attrs"]["id"]}"', page)
        self.assertNotRegex(page, re.compile(r"<button|type=\"submit\"|<script|action=|https?://", re.I))
        self.assertIn('onsubmit="return false"', page)

    def test_render_escapes_labels(self):
        demo = self.module("demo_form")
        page = demo.render({"title": "T", "fields": [{"tag": "input", "attrs": {"id": "q", "type": "text"},
                                                       "label": "<b>Name</b> & co"}]})
        self.assertIn("&lt;b&gt;Name&lt;/b&gt; &amp; co", page)

    def test_write_puts_the_page_inside_the_runtime_root(self):
        path = self.module("demo_form").write(self.root)
        self.assertEqual(path, self.root / "demo" / "demo-form.html")
        self.assertEqual(path.read_text(encoding="utf-8"), self.module("demo_form").render())


class DemoRunTests(ReleaseCase):
    def test_run_opens_the_local_page_and_fills_it_from_confirmed_facts(self):
        demo, cdp = self.module("demo_form"), FakeCDP()
        result = demo.run(self.root, DEBUG_URL, candidate(), cdp=cdp)
        page = self.root / "demo" / "demo-form.html"
        self.assertEqual(cdp.opened, [(DEBUG_URL, page.as_uri())])
        self.assertEqual(len(cdp.expressions), 2)
        audit, fill = cdp.expressions
        self.assertIn("AutoApplyGreenhouse.audit", audit)
        for value in ("Alex", "Rivera", "alex.rivera@example.com", "+1 555 0100", "alex-rivera-example"):
            self.assertIn(value, fill)
        for expression in cdp.expressions:
            self.assertNotRegex(expression, r"\.click\s*\(|requestSubmit|\.submit\s*\(")
        self.assertEqual(result["status"], "filled")
        self.assertIs(result["ok"], True)
        self.assertEqual(result["filled"], ["first_name", "last_name", "email", "phone", "question_1"])
        self.assertEqual([item["id"] for item in result["unknown"]], ["question_2", "question_3"])
        self.assertEqual(result["resume_field"], "resume")
        self.assertEqual((result["tab_id"], result["url"]), ("T1", page.as_uri()))

    def test_run_waits_for_the_debug_endpoint_before_opening_the_page(self):
        demo, cdp = self.module("demo_form"), FakeCDP()
        demo.run(self.root, DEBUG_URL, candidate(), cdp=cdp)
        self.assertEqual(cdp.events[:2], [("wait_ready", DEBUG_URL), ("open_page", DEBUG_URL)])

    def test_run_stops_with_nothing_opened_when_chrome_never_answers(self):
        demo, cdp = self.module("demo_form"), FakeCDP()
        cdp.not_ready = RuntimeError("Chrome's debugging endpoint did not answer")
        with self.assertRaises(RuntimeError):
            demo.run(self.root, DEBUG_URL, candidate(), cdp=cdp)
        self.assertEqual((cdp.opened, cdp.expressions), ([], []))

    def test_runtime_demo_waits_for_the_chrome_it_just_launched(self):
        self.write_json("config.json", {"runtime": {}, "resources": {"lanes": 1}})
        self.write_json("profile.json", candidate())
        launcher = Mock(return_value=[{"lane": 1, "debug_url": DEBUG_URL, "profile_dir": "x"}])
        cdp = FakeCDP()
        self.module("runtime").demo(root=self.root, launcher=launcher, cdp=cdp)
        self.assertEqual(cdp.events[0], ("wait_ready", DEBUG_URL))

    def test_fill_never_runs_after_the_tab_left_the_demo_page(self):
        demo, cdp = self.module("demo_form"), FakeCDP()
        real_evaluate = cdp.evaluate

        def navigate_after_audit(ws, expression, timeout=60):
            result = real_evaluate(ws, expression, timeout)
            cdp.page["url"] = "https://job-boards.greenhouse.io/examplecorp/jobs/1"
            return result

        cdp.evaluate = navigate_after_audit
        with self.assertRaises(ValueError):
            demo.run(self.root, DEBUG_URL, candidate(), cdp=cdp)
        self.assertEqual(len(cdp.expressions), 1, "no fill once the tab shows another document")

    def test_every_script_checks_the_document_it_runs_in(self):
        demo, cdp = self.module("demo_form"), FakeCDP()
        demo.run(self.root, DEBUG_URL, candidate(), cdp=cdp)
        url = (self.root / "demo" / "demo-form.html").as_uri()
        for expression in cdp.expressions:
            self.assertIn("location.href !== " + json.dumps(url), expression)

    def test_real_scripts_fill_the_synthetic_demo_page_without_clicking(self):
        demo, cdp = self.module("demo_form"), NodePageCDP([None, None])
        self.assertIsNotNone(cdp.node, "Node is a required offline adapter test dependency")
        result = demo.run(self.root, DEBUG_URL, candidate(), cdp=cdp)
        audit, fill = cdp.pages_seen
        self.assertEqual(audit["writes"], 0)
        self.assertEqual(fill["values"]["first_name"], "Alex")
        self.assertEqual(fill["values"]["email"], "alex.rivera@example.com")
        self.assertEqual(fill["values"]["question_2"], "")
        self.assertEqual(fill["clicks"], 0)
        self.assertIn("first_name", result["filled"])

    def test_in_page_guard_blocks_the_fill_after_navigation_the_tab_list_missed(self):
        demo, cdp = self.module("demo_form"), NodePageCDP([None, "https://job-boards.greenhouse.io/examplecorp/jobs/1"])
        self.assertIsNotNone(cdp.node, "Node is a required offline adapter test dependency")
        with self.assertRaises(ValueError):
            demo.run(self.root, DEBUG_URL, candidate(), cdp=cdp)
        self.assertEqual(len(cdp.pages_seen), 2, "the fill script reached the page; only its own guard can stop it")
        fill = cdp.pages_seen[1]
        self.assertEqual(fill["result"], {"wrong_document": True})
        self.assertEqual(fill["writes"], 0)
        self.assertEqual(set(fill["values"].values()), {""})
        self.assertEqual(fill["clicks"], 0)

    def test_document_mismatch_inside_the_page_stops_the_demo(self):
        demo, cdp = self.module("demo_form"), FakeCDP()
        cdp.evaluate = lambda ws, expression, timeout=60: (cdp.expressions.append(expression),
                                                           {"wrong_document": True})[1]
        with self.assertRaises(ValueError):
            demo.run(self.root, DEBUG_URL, candidate(), cdp=cdp)
        self.assertEqual(len(cdp.expressions), 1)

    def test_nothing_filled_is_not_ok(self):
        demo, cdp = self.module("demo_form"), FakeCDP()
        cdp.fill_result = {"filled": [], "blocked": [], "uploads": []}
        result = demo.run(self.root, DEBUG_URL, {"schema_version": 1, "revision": 1, "contact": {}}, cdp=cdp)
        self.assertIs(result["ok"], False)

    def test_run_refuses_a_non_loopback_browser(self):
        demo, cdp = self.module("demo_form"), FakeCDP()
        for url in ("http://example.com:9222", "http://127.0.0.1", "ws://127.0.0.1:9222"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                demo.run(self.root, url, candidate(), cdp=cdp)
        self.assertEqual(cdp.opened, [])

    def test_runtime_demo_uses_the_tools_own_chrome_and_saved_profile(self):
        self.write_json("config.json", {"runtime": {}, "resources": {"lanes": 1}})
        self.write_json("profile.json", candidate())
        launcher = Mock(return_value=[{"lane": 1, "debug_url": DEBUG_URL, "profile_dir": "x"}])
        cdp = FakeCDP()
        result = self.module("runtime").demo(root=self.root, launcher=launcher, cdp=cdp)
        self.assertEqual(launcher.call_args.args, (self.root,))
        self.assertEqual(launcher.call_args.kwargs["lanes"], 1)
        self.assertEqual(result["filled"][0], "first_name")
        self.assertEqual(cdp.opened[0][0], DEBUG_URL)

    def test_runtime_demo_needs_setup_first(self):
        with self.assertRaises(ValueError):
            self.module("runtime").demo(root=self.root, launcher=Mock(), cdp=FakeCDP())


class DemoCliTests(ReleaseCase):
    def test_demo_command_dispatches_the_service(self):
        services = {"demo": Mock(return_value={"status": "filled"})}
        out, err = io.StringIO(), io.StringIO()
        code = self.module("cli").main(["--user-dir", str(self.root), "demo"], services=services, stdout=out, stderr=err)
        self.assertEqual(code, 0, err.getvalue())
        services["demo"].assert_called_once_with(root=self.root)
