"""Workday credentials in user/secrets.json (plan aa-06).

The store keeps one email and password per Workday tenant in the runtime root,
never prints or logs a password, and the release audit fails when any user/ or
secrets file is tracked by git. All people, tenants and passwords are invented.
"""

import json
import os
import stat
import unittest
from unittest.mock import patch

from test_setup_root import RUNTIME, SetupRootCase

EMAIL = "alex.rivera@example.invalid"
PASSWORD = "synthetic-pass-4821!"
TENANT = "examplecorp.wd5.myworkdayjobs.com"
URL = "https://ExampleCorp.wd5.myworkdayjobs.com/en-US/Careers/job/Remote/Intern_R-1001"


class CredentialStoreTests(SetupRootCase):
    def store(self):
        return self.module("wd_cred")

    def test_save_then_load_round_trips_under_the_runtime_root(self):
        store = self.store()
        root = self.root / RUNTIME
        store.save(root, URL, EMAIL, PASSWORD)
        self.assertTrue((root / "secrets.json").is_file())
        credential = store.load(root, TENANT)
        self.assertEqual((credential.tenant, credential.email, credential.password), (TENANT, EMAIL, PASSWORD))
        self.assertEqual(store.load(root, URL).password, PASSWORD)
        self.assertIsNone(store.load(root, "other.wd1.myworkdayjobs.com"))

    def test_tenant_name_is_the_lowercase_host_of_a_url_or_bare_host(self):
        store = self.store()
        self.assertEqual(store.tenant_name(URL), TENANT)
        self.assertEqual(store.tenant_name("  ExampleCorp.WD5.myworkdayjobs.com "), TENANT)
        for bad in ("", "   ", None, "https:///nohost", "has space.example"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                store.tenant_name(bad)

    def test_save_replaces_one_tenant_and_keeps_the_others(self):
        store = self.store()
        root = self.root / RUNTIME
        store.save(root, TENANT, EMAIL, PASSWORD)
        store.save(root, "second.wd1.myworkdayjobs.com", EMAIL, "synthetic-second-9")
        store.save(root, TENANT, EMAIL, "synthetic-rotated-7")
        self.assertEqual(store.load(root, TENANT).password, "synthetic-rotated-7")
        self.assertEqual(store.load(root, "second.wd1.myworkdayjobs.com").password, "synthetic-second-9")
        self.assertEqual(store.tenants(root), sorted([TENANT, "second.wd1.myworkdayjobs.com"]))

    def test_tenants_lists_names_without_any_secret(self):
        store = self.store()
        root = self.root / RUNTIME
        store.save(root, TENANT, EMAIL, PASSWORD)
        listing = json.dumps(store.tenants(root))
        self.assertNotIn(PASSWORD, listing)
        self.assertNotIn(EMAIL, listing)
        self.assertEqual(store.tenants(self.root / "missing runtime"), [])

    def test_credential_never_exposes_the_password_through_text_forms(self):
        store = self.store()
        root = self.root / RUNTIME
        store.save(root, TENANT, EMAIL, PASSWORD)
        credential = store.load(root, TENANT)
        for rendered in (repr(credential), str(credential), f"{credential}", "%s %r" % (credential, credential)):
            self.assertNotIn(PASSWORD, rendered)
            self.assertIn("***", rendered)
        self.assertNotIn(PASSWORD, json.dumps(credential.public()))
        self.assertEqual(credential.public()["tenant"], TENANT)

    def test_secret_is_written_only_to_secrets_json(self):
        store = self.store()
        root = self.root / RUNTIME
        store.save(root, TENANT, EMAIL, PASSWORD)
        store.save(root, TENANT, EMAIL, "synthetic-rotated-7")
        holders = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and (PASSWORD.encode() in path.read_bytes() or b"synthetic-rotated-7" in path.read_bytes())
        ]
        self.assertEqual(holders, ["secrets.json"], "no backup, temp or lock file may hold a password")

    @unittest.skipIf(os.name == "nt", "POSIX permission bits do not exist on Windows")
    def test_secrets_file_is_owner_only_on_posix(self):
        store = self.store()
        root = self.root / RUNTIME
        store.save(root, TENANT, EMAIL, PASSWORD)
        self.assertEqual(stat.S_IMODE((root / "secrets.json").stat().st_mode), 0o600)

    def test_invalid_input_is_rejected_without_echoing_the_secret(self):
        store = self.store()
        root = self.root / RUNTIME
        cases = [
            (TENANT, "", PASSWORD),
            (TENANT, "not-an-email", PASSWORD),
            (TENANT, EMAIL, ""),
            (TENANT, EMAIL, "  "),
            (TENANT, EMAIL, None),
            (TENANT, EMAIL, "line\nbreak-password-1"),
            ("", EMAIL, PASSWORD),
        ]
        for tenant, email, password in cases:
            with self.subTest(tenant=tenant, email=email, password=bool(password)):
                with self.assertRaises(ValueError) as caught:
                    store.save(root, tenant, email, password)
                for secret in (PASSWORD, "line\nbreak-password-1"):
                    self.assertNotIn(secret, str(caught.exception))
        self.assertFalse((root / "secrets.json").exists())

    def test_corrupt_store_fails_without_leaking_file_content(self):
        store = self.store()
        root = self.root / RUNTIME
        root.mkdir()
        (root / "secrets.json").write_text('{"workday": {"x": "' + PASSWORD + '"', encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            store.load(root, TENANT)
        self.assertNotIn(PASSWORD, str(caught.exception))
        (root / "secrets.json").write_text(json.dumps({"workday": {TENANT: {"email": EMAIL}}}), encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            store.load(root, TENANT)
        self.assertNotIn(EMAIL, str(caught.exception))

    def test_redact_masks_every_stored_password_in_text_and_nested_values(self):
        store = self.store()
        root = self.root / RUNTIME
        store.save(root, TENANT, EMAIL, PASSWORD)
        store.save(root, "second.wd1.myworkdayjobs.com", EMAIL, "synthetic-second-9")
        text = f"signin failed for {PASSWORD}; retry with synthetic-second-9 ok"
        cleaned = store.redact(root, text)
        self.assertNotIn(PASSWORD, cleaned)
        self.assertNotIn("synthetic-second-9", cleaned)
        self.assertIn("signin failed", cleaned)
        nested = store.redact(root, {"out": [text, {"deep": PASSWORD}], "n": 3})
        self.assertNotIn(PASSWORD, json.dumps(nested))
        self.assertEqual(nested["n"], 3)
        self.assertEqual(store.redact(self.root / "missing runtime", text), text)

    def test_no_stdout_stderr_or_log_output_while_saving_and_loading(self):
        store = self.store()
        root = self.root / RUNTIME
        import io
        from contextlib import redirect_stderr, redirect_stdout

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), self.assertNoLogs(level="DEBUG"):
            store.save(root, TENANT, EMAIL, PASSWORD)
            store.load(root, TENANT)
            store.tenants(root)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_store_is_offline(self):
        store = self.store()
        root = self.root / RUNTIME
        with patch("subprocess.Popen", side_effect=AssertionError("offline")), patch(
            "socket.socket", side_effect=AssertionError("offline")
        ):
            store.save(root, TENANT, EMAIL, PASSWORD)
            self.assertEqual(store.load(root, TENANT).email, EMAIL)


class TrackedSecretsAuditTests(SetupRootCase):
    """check_release fails when git tracks a user/ or secrets file, even if allowlisted."""

    def tracked_secret_report(self, relative):
        self.checkouts = getattr(self, "checkouts", 0) + 1
        source, allowed = self.source(name=f"source checkout {self.checkouts}", public_copy=True)
        self.init_git(source, allowed)
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"workday": {TENANT: {"email": EMAIL, "password": PASSWORD}}}), encoding="utf-8")
        result = self.git(source, "add", "--force", "--", relative)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (source / "export-allowlist.json").write_text(json.dumps([*allowed, relative]), encoding="utf-8")
        code, output = self.check(self.checker(source))
        self.assertEqual(code, 1, output)
        self.assertNotIn(PASSWORD, output, "the audit report must not echo a secret")
        return [item for item in json.loads(output)["violations"] if item["path"] == relative]

    def test_tracked_user_secrets_json_fails_the_audit(self):
        violations = self.tracked_secret_report(RUNTIME + "/secrets.json")
        self.assertTrue(violations and all("private" in item["reason"] for item in violations), violations)

    def test_tracked_secrets_files_anywhere_fail_the_audit(self):
        for relative in (
            "secrets.json",
            "secrets.json.bak",
            "Secrets.JSON",
            "config/secrets.toml",
            "config/secrets/workday.json",
            "nested/Secrets/token.txt",
        ):
            with self.subTest(path=relative):
                violations = self.tracked_secret_report(relative)
                self.assertTrue(violations, relative)

    def test_untracked_ignored_user_secrets_do_not_fail_the_audit(self):
        source, allowed = self.source(public_copy=True)
        self.init_git(source, allowed)
        store = self.module("wd_cred")
        store.save(source / RUNTIME, TENANT, EMAIL, PASSWORD)
        for name in ("user/secrets.json", "secrets.json"):
            result = self.git(source, "check-ignore", "--", name)
            self.assertEqual(result.returncode, 0, name + " must be ignored: " + result.stdout + result.stderr)
        code, output = self.check(self.checker(source))
        self.assertEqual(code, 0, output)


if __name__ == "__main__":
    unittest.main()
