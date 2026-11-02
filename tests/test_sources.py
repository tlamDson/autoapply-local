"""Small offline contracts for plans/autoapply-local-sources.md.

Giả định của người thực thi: boards dùng preview_import(root,path) và
apply_import(root,preview,confirm=False) như intake; runtime cung cấp boards_list
và boards_import; đầu vào là JSON list, bỏ whitespace và chuẩn hóa ATS/host.
Chỉ được sửa test nên ghi giả định tại đây, không sửa plan hoặc lưu log ngoài test.
Public seed metadata is fixed by the plan; all job responses are synthetic mocks.
"""

import copy
import fnmatch
import io
import json
import threading
import tomllib
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from unittest.mock import Mock, patch

from test_support import RELEASE, ReleaseCase


SEEDS = [
    {"ats": "greenhouse", "board": "anthropic", "company": "Anthropic"},
    {
        "ats": "workday",
        "host": "nvidia.wd5.myworkdayjobs.com",
        "tenant": "nvidia",
        "site": "NVIDIAExternalCareerSite",
        "company": "NVIDIA",
        "search": "software",
    },
    {"ats": "rippling", "board": "spreeai", "company": "SpreeAI"},
]
UUID = "11111111-2222-4333-8444-555555555555"
OTHER_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
ABSENT = object()


class SourceTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        process = patch("subprocess.Popen", side_effect=AssertionError("No live subprocess in source tests"))
        process.start()
        self.addCleanup(process.stop)
        # Patch objects, rather than the network function's return value, so an
        # accidental real request fails immediately instead of reaching the web.
        network = patch("autoapply_local.fetch.urlopen", side_effect=AssertionError("No live HTTP in source tests"))
        network.start()
        self.addCleanup(network.stop)

    def config(self, boards=ABSENT):
        value = {"runtime": {"apply_engine": None}, "resources": {"lanes": 1}, "custom": {"keep": True}}
        if boards is not ABSENT:
            value["boards"] = copy.deepcopy(boards)
        self.write_json("config.json", value)
        return value

    def read_config(self):
        return json.loads((self.root / "config.json").read_text(encoding="utf-8"))

    def tree(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def invoke(self, *argv, services=None):
        out, err = io.StringIO(), io.StringIO()
        code = self.module("cli").main(["--user-dir", str(self.root), *argv], services=services, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_packaged_seeds_examples_and_package_inventory_use_approved_public_metadata(self):
        resource = files("autoapply_local").joinpath("data/boards.json")
        self.assertTrue(resource.is_file(), "Setup defaults must be a packaged resource")
        self.assertEqual(json.loads(resource.read_text(encoding="utf-8")), SEEDS)
        self.assertEqual(json.loads((RELEASE / "examples/boards.json").read_text(encoding="utf-8")), SEEDS)
        allowed = json.loads((RELEASE / "export-allowlist.json").read_text(encoding="utf-8"))
        self.assertIn("autoapply_local/data/boards.json", allowed)
        self.assertIn("tests/test_sources.py", allowed)
        project = tomllib.loads((RELEASE / "pyproject.toml").read_text(encoding="utf-8"))
        patterns = project["tool"]["setuptools"]["package-data"]["autoapply_local"]
        self.assertTrue(any(fnmatch.fnmatch("data/boards.json", pattern) for pattern in patterns))
        manifest = (RELEASE / "MANIFEST.in").read_text(encoding="utf-8")
        resource_path = "autoapply_local/data/boards.json"
        included = False
        for line in manifest.splitlines():
            words = line.split()
            if words[:1] == ["include"]:
                included |= any(fnmatch.fnmatch(resource_path, pattern) for pattern in words[1:])
            elif words[:1] == ["recursive-include"] and len(words) >= 3:
                directory = words[1].rstrip("/") + "/"
                included |= resource_path.startswith(directory) and any(
                    fnmatch.fnmatch(resource_path[len(directory) :], pattern) for pattern in words[2:]
                )
        self.assertTrue(included, "sdist must carry the seeds needed to build an installed wheel")

    def test_fresh_setup_installs_seeds_without_claiming_authorization(self):
        self.module("runtime").setup(root=self.root)
        self.assertEqual(self.read_config().get("boards"), SEEDS)
        profile = json.loads((self.root / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["authorization"], [])
        self.assertEqual(profile["contact"], {})
        document = self.module("intake").read_intake(self.root / "user/profile-intake.md")
        self.assertIn("boards", document["groups"]["resources"])

    def test_repeat_setup_preserves_custom_empty_null_and_edited_intake(self):
        intake = self.module("intake")
        intake.setup(self.root)
        path = self.root / "user/profile-intake.md"
        path.write_text("User-owned edits", encoding="utf-8")
        for boards in ([SEEDS[2]], [], None):
            with self.subTest(boards=boards):
                original = self.config(boards)
                intake.setup(self.root)
                self.assertEqual(self.read_config(), original)
                self.assertEqual(path.read_text(encoding="utf-8"), "User-owned edits")

    def test_existing_config_without_boards_gets_defaults_and_retains_other_settings(self):
        original = self.config()
        self.module("intake").setup(self.root)
        self.assertEqual(self.read_config(), {**original, "boards": SEEDS})

    def test_boards_import_preview_is_read_only_then_confirm_merges_stable_ats_identities(self):
        boards = self.module("boards")
        unrelated = {"ats": "greenhouse", "board": "examplelabs", "company": "Example Labs"}
        original = self.config([*SEEDS, unrelated])
        updates = copy.deepcopy(SEEDS)
        for item in updates:
            item["company"] += " Updated"
        updates[1]["search"] = "intern"
        new = {"ats": "rippling", "board": "examplelab", "company": "Example Lab"}
        raw = {**updates[0], "ats": " GREENHOUSE ", "board": " anthropic "}
        path = self.write_json("incoming.json", [raw, *updates, new, new])
        before = self.tree()
        preview = boards.preview_import(self.root, path)
        self.assertTrue(preview["changes"])
        rendered = json.dumps(preview["changes"])
        for company in ("Anthropic Updated", "NVIDIA Updated", "SpreeAI Updated", "Example Lab"):
            self.assertIn(company, rendered, "Preview must show changes and additions")
        boards.apply_import(self.root, preview, confirm=False)
        self.assertEqual(self.tree(), before)
        boards.apply_import(self.root, preview, confirm=True)
        expected = [*updates, unrelated, new]
        self.assertCountEqual(self.read_config()["boards"], expected)
        self.assertEqual(
            {k: v for k, v in self.read_config().items() if k != "boards"},
            {k: v for k, v in original.items() if k != "boards"},
        )
        self.assertEqual(json.loads((self.root / "config.json.bak").read_text(encoding="utf-8")), original)

    def test_board_validation_rejects_malformed_or_unsafe_endpoint_combinations_before_mutation(self):
        boards = self.module("boards")
        self.config(SEEDS)
        invalid = [
            {},
            {"boards": SEEDS},
            [None],
            ["anthropic"],
            [{**SEEDS[0], "ats": "unknown"}],
            [{**SEEDS[0], "board": "../private"}],
            [{**SEEDS[0], "board": ""}],
            [{**SEEDS[0], "company": []}],
            [{**SEEDS[0], "endpoint": "http://boards-api.greenhouse.io/v1/boards/anthropic/jobs"}],
            [{**SEEDS[0], "endpoint": "https://127.0.0.1/jobs"}],
            [{**SEEDS[0], "endpoint": "https://careers.example.invalid/jobs"}],
            [{**SEEDS[0], "endpoint": "https://api.rippling.com/platform/api/ats/v1/board/anthropic/jobs"}],
            [{**SEEDS[0], "endpoint": "https://boards-api.greenhouse.io.evil.invalid/jobs"}],
            # Preserve the identical credential-bearing URL at runtime without
            # representing a literal contact address in the public test source.
            [
                {
                    **SEEDS[0],
                    "endpoint": "https://user:secret" + "@" + "boards-api.greenhouse.io/v1/boards/anthropic/jobs",
                }
            ],
            [{**SEEDS[1], "host": "localhost"}],
            [{**SEEDS[1], "host": "192.168.1.1"}],
            [{**SEEDS[1], "host": "nvidia.wd5.myworkdayjobs.com.evil.invalid"}],
            [{**SEEDS[1], "tenant": "../nvidia"}],
            [{**SEEDS[1], "site": "../private"}],
            [{**SEEDS[1], "search": []}],
            [{**SEEDS[2], "board": "spreeai/jobs"}],
        ]
        for data in invalid:
            with self.subTest(data=data):
                path = self.write_json("invalid.json", data)
                before = self.tree()
                with self.assertRaises(ValueError):
                    boards.preview_import(self.root, path)
                self.assertEqual(self.tree(), before)

    def test_supported_https_overrides_remain_usable(self):
        boards = self.module("boards")
        self.config([])
        overrides = copy.deepcopy(SEEDS)
        for item, endpoint in zip(
            overrides,
            (
                "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs?content=true",
                "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs",
                "https://api.rippling.com/platform/api/ats/v1/board/spreeai/jobs",
            ),
        ):
            item["endpoint"] = endpoint
        overrides[1]["host"] = overrides[1]["host"].upper()
        preview = boards.preview_import(self.root, self.write_json("overrides.json", overrides))
        boards.apply_import(self.root, preview, confirm=True)
        overrides[1]["host"] = overrides[1]["host"].lower()
        self.assertCountEqual(self.read_config()["boards"], overrides)

    def test_changed_source_or_config_invalidates_preview_without_overwriting_new_content(self):
        boards = self.module("boards")
        for changed in ("source", "config"):
            with self.subTest(changed=changed):
                original = self.config([SEEDS[0]])
                path = self.write_json("incoming.json", [SEEDS[2]])
                preview = boards.preview_import(self.root, path)
                if changed == "source":
                    self.write_json("incoming.json", [SEEDS[1]])
                else:
                    self.write_json("config.json", {**original, "custom": {"keep": "new user edit"}})
                before = self.tree()
                with self.assertRaises(ValueError):
                    boards.apply_import(self.root, preview, confirm=True)
                self.assertEqual((self.root / "config.json").read_bytes(), before["config.json"])

    def test_concurrent_confirmations_accept_one_preview_and_reject_lost_update(self):
        boards = self.module("boards")
        self.config([SEEDS[0]])
        previews = [
            boards.preview_import(self.root, self.write_json(f"incoming-{i}.json", [item]))
            for i, item in enumerate(SEEDS[1:])
        ]
        barrier = threading.Barrier(2)

        def commit(index):
            barrier.wait(timeout=5)
            try:
                boards.apply_import(self.root, previews[index], confirm=True)
                return index, True
            except ValueError:
                return index, False

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(commit, range(2)))
        winners = [index for index, accepted in outcomes if accepted]
        self.assertEqual(len(winners), 1, "Lock must cover reading, stale check and config commit")
        self.assertCountEqual(self.read_config()["boards"], [SEEDS[0], SEEDS[1 + winners[0]]])

    def test_failed_atomic_replace_preserves_existing_config(self):
        boards, storage = self.module("boards"), self.module("storage")
        self.config([SEEDS[0]])
        preview = boards.preview_import(self.root, self.write_json("incoming.json", [SEEDS[2]]))
        before = (self.root / "config.json").read_bytes()
        replace = storage.os.replace

        def fail_config(source, target):
            if str(target) == str(self.root / "config.json"):
                raise OSError("Synthetic interrupted config replacement")
            return replace(source, target)

        with patch.object(storage.os, "replace", side_effect=fail_config):
            with self.assertRaises(OSError):
                boards.apply_import(self.root, preview, confirm=True)
        self.assertEqual((self.root / "config.json").read_bytes(), before)

    def test_boards_cli_dispatch_passes_root_path_and_explicit_confirmation(self):
        handlers = {
            "boards_list": Mock(return_value={"boards": SEEDS}),
            "boards_import": Mock(return_value={"changes": []}),
        }
        path = self.root / "incoming.json"
        for argv in (("boards", "list"), ("boards", "import", str(path)), ("boards", "import", str(path), "--confirm")):
            with self.subTest(argv=argv):
                code, _, err = self.invoke(*argv, services=handlers)
                self.assertEqual(code, 0, err)
        handlers["boards_list"].assert_called_once_with(root=self.root.resolve())
        self.assertEqual(handlers["boards_import"].call_count, 2)
        self.assertEqual(
            [call.kwargs for call in handlers["boards_import"].call_args_list],
            [{"root": self.root.resolve(), "path": path, "confirm": flag} for flag in (False, True)],
        )

    def test_default_boards_cli_works_with_config_only_and_invalid_import_exits_two(self):
        self.config([SEEDS[0]])  # No profile, resume, engine selection or onboarding.
        code, out, err = self.invoke("boards", "list")
        self.assertEqual(code, 0, err)
        self.assertIn("anthropic", out)
        path = self.write_json("incoming.json", [SEEDS[2]])
        before = (self.root / "config.json").read_bytes()
        code, out, err = self.invoke("boards", "import", str(path))
        self.assertEqual(code, 0, err)
        self.assertIn("spreeai", out)
        self.assertEqual((self.root / "config.json").read_bytes(), before)
        code, _, err = self.invoke("boards", "import", str(path), "--confirm")
        self.assertEqual(code, 0, err)
        self.assertCountEqual(self.read_config()["boards"], [SEEDS[0], SEEDS[2]])
        invalid = self.write_json("invalid.json", [{**SEEDS[0], "board": "../private"}])
        before = (self.root / "config.json").read_bytes()
        code, _, err = self.invoke("boards", "import", str(invalid), "--confirm")
        self.assertEqual(code, 2)
        self.assertTrue(err.strip())
        self.assertEqual((self.root / "config.json").read_bytes(), before)

    def test_intake_boards_preview_confirm_and_absent_null_empty_semantics(self):
        intake, runtime = self.module("intake"), self.module("runtime")
        intake.setup(self.root)
        original = intake.read_intake(self.root / "user/profile-intake.md")
        for value in (ABSENT, None, [], [SEEDS[2]]):
            with self.subTest(value=value):
                self.config([SEEDS[0]])
                document = copy.deepcopy(original)
                resources = document["groups"]["resources"]
                resources.pop("boards", None)
                if value is not ABSENT:
                    resources["boards"] = value
                path = self.root / "incoming-intake.md"
                path.write_text("```json\n" + json.dumps(document) + "\n```\n", encoding="utf-8")
                before = (self.root / "config.json").read_bytes()
                preview = runtime.profile_update(root=self.root, path=path)
                if value == [SEEDS[2]]:
                    self.assertTrue(any(change["field"] == "resources.boards" for change in preview["changes"]))
                self.assertEqual((self.root / "config.json").read_bytes(), before)
                runtime.profile_update(root=self.root, path=path, confirm=True)
                expected = [SEEDS[0]] if value is ABSENT or value is None else value
                self.assertEqual(self.read_config().get("boards"), expected)

    def test_invalid_intake_boards_fail_before_profile_or_config_mutation(self):
        intake = self.module("intake")
        intake.setup(self.root)
        path = self.root / "user/profile-intake.md"
        document = intake.read_intake(path)
        document["groups"]["resources"]["boards"] = [{**SEEDS[1], "host": "localhost"}]
        path.write_text("```json\n" + json.dumps(document) + "\n```\n", encoding="utf-8")
        before = {name: (self.root / name).read_bytes() for name in ("profile.json", "config.json")}
        with self.assertRaises(ValueError):
            preview = intake.preview_import(self.root, path)
            intake.apply_import(self.root, preview, confirm=True)
        for name, content in before.items():
            self.assertEqual((self.root / name).read_bytes(), content)

    def test_fetch_without_sources_raises_actionable_error_and_cli_exits_two(self):
        self.write_json("profile.json", {"schema_version": 1, "revision": 1, "targets": {}})
        for value in (ABSENT, None, []):
            with self.subTest(boards=value):
                self.config(value)
                with self.assertRaises(ValueError) as caught:
                    self.module("runtime").fetch(root=self.root)
                for guidance in ("boards", "list", "import", "setup"):
                    self.assertIn(guidance, str(caught.exception).lower())
                code, _, err = self.invoke("fetch")
                self.assertEqual(code, 2)
                self.assertIn("boards", err.lower())

    def test_configured_zero_results_and_url_only_fetch_need_no_complete_onboarding(self):
        fetch = self.module("fetch")
        self.write_json("profile.json", {"schema_version": 1, "revision": 1, "targets": {}})
        self.config([SEEDS[0]])
        client = Mock(spec=["fetch", "import_url"])
        client.fetch.return_value = []
        url = f"https://ats.rippling.com/en-US/spreeai/jobs/{UUID}"
        client.import_url.return_value = {"status": "closed", "url": url}
        with patch.object(fetch, "Fetcher", return_value=client):
            report = self.module("runtime").fetch(root=self.root)
            self.assertEqual(report["jobs"], [])
            self.assertEqual(report["unknown"], [])
            client.fetch.assert_called_once_with("greenhouse", SEEDS[0])
            client.reset_mock()
            self.config([])
            self.assertEqual(self.module("runtime").fetch(root=self.root, url=url), {"status": "closed", "url": url})
            client.import_url.assert_called_once_with(url)
            client.fetch.assert_not_called()

    def rippling(self, identity=UUID):
        transport = Mock(spec=["get_json", "post_json"])
        transport.get_json.return_value = [
            {
                "id": identity,
                "name": "Software Engineering Intern",
                "description": "Build Python services.",
                "url": f"https://ats.rippling.com/spreeai/jobs/{identity}",
                "workLocation": {"label": "New York, United States"},
            }
        ]
        return self.module("fetch").Fetcher(transport=transport), transport

    def test_real_shaped_rippling_work_location_drives_country_and_excluded_location_filters(self):
        fetcher, transport = self.rippling()
        items = fetcher.fetch("rippling", SEEDS[2])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["location"], "New York, United States")
        self.assertEqual(items[0]["country"], "US")
        self.assertEqual(items[0]["eligibility"], "unknown")
        profile = {"targets": {"countries": ["US"]}, "logistics": {"excluded_locations": ["New York"]}}
        verdict = self.module("fetch").evaluate_job(items[0], profile)
        self.assertEqual(verdict["decision"], "exclude")
        self.assertIn("excluded_location", verdict["reasons"])
        transport.get_json.assert_called_once()

    def test_rippling_manual_url_accepts_optional_locale_and_never_invents_changed_identity(self):
        for prefix in ("", "en-US/"):
            for identity in (UUID, OTHER_UUID):
                with self.subTest(prefix=prefix, identity=identity):
                    fetcher, transport = self.rippling(identity)
                    url = f"https://ats.rippling.com/{prefix}spreeai/jobs/{UUID}"
                    imported = fetcher.import_url(url)
                    self.assertEqual(imported["url"], url)
                    self.assertEqual(imported["status"], "open" if identity == UUID else "closed")
                    if identity == UUID:
                        self.assertEqual(imported["job"]["id"], f"rippling:spreeai:{UUID}")
                    else:
                        self.assertNotIn("job", imported)
                    transport.get_json.assert_called_once()
                    transport.post_json.assert_not_called()

    def test_rippling_import_rejects_disagreement_between_payload_id_and_job_url(self):
        url = f"https://ats.rippling.com/spreeai/jobs/{UUID}"
        for identity, url_identity in ((OTHER_UUID, UUID), (UUID, OTHER_UUID)):
            with self.subTest(identity=identity, url_identity=url_identity):
                fetcher, transport = self.rippling(identity)
                transport.get_json.return_value[0]["url"] = f"https://ats.rippling.com/spreeai/jobs/{url_identity}"
                imported = fetcher.import_url(url)
                self.assertEqual(imported["status"], "closed")
                self.assertNotIn("job", imported)
