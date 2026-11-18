"""Gmail-tab rows to applied history: classification, company/role parsing and tracker dedup.

Rows are synthetic Gmail list rows (invented companies and senders); no mailbox,
browser or network is used.
"""

from test_support import ReleaseCase


def mail(thread, name, email, subject, snippet="", date="Tue, Nov 10, 2026, 9:14 AM"):
    return {"thread": thread, "from": name, "email": email, "subject": subject, "snippet": snippet,
            "date": date, "unread": False}


NORTHWIND = mail("18c0a1", "Northwind Labs Recruiting", "no-reply@northwind.example",
                 "Thank you for applying to Northwind Labs",
                 "We received your application for the Software Engineer Intern position.")
CONTOSO = mail("18c0a3", "Contoso Careers", "careers@contoso.example", "Your application",
               "Thanks for your interest. We have received your application.")
FABRIKAM = mail("18c0a4", "Fabrikam Talent Team", "jobs@fabrikam.example",
                "Application received: Data Analyst Intern", "We got your application.",
                date="Mon, Jun 1, 2026, 7:00 PM")
VERIFY = mail("18c0a2", "Contoso Careers", "careers@contoso.example", "Verify your candidate account",
              "Your security code is below.")
REJECT = mail("18c0a5", "Litware Recruiting", "talent@litware.example", "Your application to Litware",
              "Thank you for applying. Unfortunately we decided to move forward with other candidates.")
STARTED = mail("18c0a6", "Tailspin Careers", "careers@tailspin.example", "Your application step was started",
               "Continue your application.")
ATS_ONLY = mail("18c0a7", "Notifications", "no-reply@us.greenhouse-mail.io", "Thank you for your application",
                "We received it.")


class Classify(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.mh = self.module("mail_history")

    def test_verify_confirm_reject_other(self):
        self.assertEqual(self.mh.classify(VERIFY), "verify")
        self.assertEqual(self.mh.classify(mail("1", "A", "a@a.example", "Your one-time passcode")), "verify")
        self.assertEqual(self.mh.classify(NORTHWIND), "confirm")
        self.assertEqual(self.mh.classify(CONTOSO), "confirm")
        self.assertEqual(self.mh.classify(FABRIKAM), "confirm")
        self.assertEqual(self.mh.classify(REJECT), "reject")
        self.assertEqual(self.mh.classify(STARTED), "other")
        self.assertEqual(self.mh.classify(mail("2", "News", "n@n.example", "Weekly digest", "Top stories")), "other")

    def test_confirmation_only_in_snippet_still_counts(self):
        row = mail("3", "Adventure Works", "hr@adventure-works.example", "Adventure Works",
                   "Thank you for applying. Our team will review it.")
        self.assertEqual(self.mh.classify(row), "confirm")

    def test_parse_company_and_role_from_subject_snippet_and_sender(self):
        entry = self.mh.parse(NORTHWIND)
        self.assertEqual((entry["company"], entry["role"]), ("Northwind Labs", "Software Engineer Intern"))
        self.assertEqual(entry["date"], "2026-11-10")
        self.assertEqual(self.mh.parse(CONTOSO)["company"], "Contoso")
        self.assertEqual(self.mh.parse(CONTOSO)["role"], "")
        self.assertTrue(self.mh.UNKNOWN_ROLE)
        fabrikam = self.mh.parse(FABRIKAM)
        self.assertEqual((fabrikam["company"], fabrikam["role"], fabrikam["date"]),
                         ("Fabrikam", "Data Analyst Intern", "2026-06-01"))

    def test_role_and_company_from_one_subject(self):
        row = mail("4", "Recruiting", "no-reply@myworkday.example",
                   "Thank you for applying for Backend Engineer Intern at Wide World Importers")
        entry = self.mh.parse(row)
        self.assertEqual((entry["company"], entry["role"]), ("Wide World Importers", "Backend Engineer Intern"))

    def test_generic_ats_sender_without_company_is_unknown(self):
        self.assertEqual(self.mh.parse(ATS_ONLY)["company"], "")

    def test_key_uses_thread_or_falls_back_to_sender_subject_date(self):
        self.assertEqual(self.mh.parse(NORTHWIND)["key"], "thread:18c0a1")
        without = {**NORTHWIND, "thread": ""}
        key = self.mh.parse(without)["key"]
        self.assertTrue(key.startswith("row:"))
        self.assertEqual(key, self.mh.parse(dict(without))["key"])
        self.assertNotEqual(key, self.mh.parse({**without, "subject": "Other"})["key"])

    def test_unparsed_date_is_none(self):
        self.assertIsNone(self.mh.parse({**NORTHWIND, "date": "Nov 10"})["date"])


class Import(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.mh = self.module("mail_history")
        self.store = self.module("storage").Store(self.root / "runtime")

    def rows(self):
        return [(row["company"], row["role"], row["status"]) for row in self.store.tracker_rows()]

    def test_confirmations_become_submitted_tracker_rows(self):
        report = self.mh.import_confirmations(self.store, [NORTHWIND, CONTOSO, FABRIKAM, VERIFY, REJECT, STARTED])
        self.assertEqual(report["added"], 3)
        self.assertEqual(report["duplicates"], 0)
        self.assertEqual(report["ignored"], 3)
        self.assertEqual(sorted(self.rows()), sorted([
            ("Northwind Labs", "Software Engineer Intern", "submitted"),
            ("Contoso", self.mh.UNKNOWN_ROLE, "submitted"),
            ("Fabrikam", "Data Analyst Intern", "submitted"),
        ]))
        tracked = {row["company"]: row for row in self.store.tracker_rows()}
        self.assertEqual(tracked["Northwind Labs"]["updated_at"], "2026-11-10")
        self.assertIn("Thank you for applying to Northwind Labs", tracked["Northwind Labs"]["confirmation"])
        self.assertTrue(tracked["Northwind Labs"]["url"].startswith("https://mail.google.com/"))

    def test_verify_mail_is_never_stored(self):
        self.mh.import_confirmations(self.store, [VERIFY])
        self.assertEqual(self.store.tracker_rows(), [])
        self.assertEqual(self.store.mail_events(), [])

    def test_rescan_does_not_duplicate(self):
        self.mh.import_confirmations(self.store, [NORTHWIND, CONTOSO])
        again = self.mh.import_confirmations(self.store, [NORTHWIND, CONTOSO, FABRIKAM])
        self.assertEqual((again["added"], again["duplicates"]), (1, 2))
        self.assertEqual(len(self.store.tracker_rows()), 3)

    def test_same_application_twice_in_one_scan_is_one_row(self):
        copy = {**NORTHWIND, "thread": "18c0b9", "subject": "Application received: Software Engineer Intern",
                "from": "Northwind Labs"}
        report = self.mh.import_confirmations(self.store, [NORTHWIND, copy])
        self.assertEqual((report["added"], report["duplicates"]), (1, 1))

    def test_application_already_in_tracker_is_a_duplicate(self):
        self.mh.import_confirmations(self.store, [NORTHWIND])
        other_thread = {**NORTHWIND, "thread": "18c0c0"}
        report = self.mh.import_confirmations(self.store, [other_thread])
        self.assertEqual((report["added"], report["duplicates"]), (0, 1))

    def test_company_without_role_dedups_by_day(self):
        later = {**CONTOSO, "thread": "18c0d0", "date": "Fri, Nov 13, 2026, 9:00 AM"}
        report = self.mh.import_confirmations(self.store, [CONTOSO, {**CONTOSO, "thread": "18c0d1"}, later])
        self.assertEqual((report["added"], report["duplicates"]), (2, 1))

    def test_unknown_company_is_ignored(self):
        report = self.mh.import_confirmations(self.store, [ATS_ONLY])
        self.assertEqual((report["added"], report["ignored"]), (0, 1))
        self.assertEqual(self.store.tracker_rows(), [])
