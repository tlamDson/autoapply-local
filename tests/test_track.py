"""`track`: held tabs on a submitted page and Gmail confirmations become tracker rows.

Runs first in `apply`. It only lists and closes tabs of the tool's own Chrome
and reads Gmail rows; it never evaluates a script in a job tab, so it can never
press a Submit control. Every case is offline: a fake DevTools module and
synthetic Gmail rows.
"""

import io
import json
from unittest.mock import patch

from test_support import ReleaseCase, job

LANE = "http://127.0.0.1:9451"


class FakeCDP:
    """Lists and closes tabs; has no evaluate, so any script in a tab fails the test."""

    def __init__(self, tabs=(), down=False):
        self.tabs, self.down, self.closed = list(tabs), down, []

    def pages(self, debug_url):
        if self.down:
            raise ConnectionRefusedError("no Chrome")
        return [dict(tab, ws="ws://127.0.0.1:9451/devtools/page/" + tab["id"]) for tab in self.tabs]

    def close_page(self, debug_url, tab_id):
        self.closed.append(tab_id)
        self.tabs = [tab for tab in self.tabs if tab["id"] != tab_id]


def mail(company, role=None, thread="t1", date="Nov 18, 2026"):
    subject = f"Thank you for applying for {role} at {company}" if role else f"Thank you for applying to {company}"
    return {"from": f"{company} Recruiting", "email": "no-reply@example.invalid", "subject": subject,
            "snippet": "We received your application.", "date": date, "thread": thread}


class TrackTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.store = self.module("storage").Store(self.root)
        self.track = self.module("track")
        self.write_json("browser/active-lanes.json", [{"lane": 1, "debug_url": LANE}])

    def held(self, job_id, url, company="Example Labs", role="Backend Software Engineering Intern", state="filled",
             held_at="2026-11-17T09:00:00+00:00"):
        self.store.enqueue(job(job_id, url=url, company=company, role=role))
        fields = {"held_at": held_at} if held_at else {}
        self.store.update(job_id, state=state, tab_id="tab-" + job_id, debug_url=LANE, lane="1", **fields)

    def reconcile(self, cdp, rows=()):
        self.gmail_calls = []

        def gmail(debug_url, days):
            self.gmail_calls.append(debug_url)
            self.gmail_days = days
            return list(rows)

        return self.track.run(self.root, cdp=cdp, gmail=gmail, clock=lambda: "2026-11-18T10:00:00+00:00")

    def tracker_ids(self):
        return [str(row["id"]) for row in self.store.tracker_rows()]

    def test_confirmation_pages_are_recognised_per_ats(self):
        cases = {
            "https://job-boards.greenhouse.io/acme/jobs/123/confirmation": True,
            "https://boards.greenhouse.io/acme/jobs/123/confirmation?gh_jid=123": True,
            "https://job-boards.greenhouse.io/acme/jobs/123": False,
            "https://acme.wd5.myworkdayjobs.com/en-US/Ext/jobTasks/completed/application": True,
            "https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/Remote/Intern_R1/apply?job_application_id=9": True,
            "https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/Remote/Intern_R1/apply": False,
            "https://ats.rippling.com/acme/jobs/abc/apply?step=confirmation": True,
            "https://ats.rippling.com/acme/jobs/abc/apply?step=questions": False,
            "https://jobs.lever.co/acme/0b1c/thanks": True,
            "https://jobs.lever.co/acme/0b1c/apply": False,
            "https://jobs.ashbyhq.com/acme/0b1c/application": False,
            "https://example.invalid/confirmation": False,
            "not a url": False,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertIs(self.track.confirmation_page(url), expected)

    def test_tab_on_a_confirmation_page_records_the_job_and_closes_only_that_tab(self):
        self.held("gh-1", "https://job-boards.greenhouse.io/acme/jobs/1")
        self.held("gh-2", "https://job-boards.greenhouse.io/acme/jobs/2")
        cdp = FakeCDP([
            {"id": "tab-gh-1", "url": "https://job-boards.greenhouse.io/acme/jobs/1/confirmation"},
            {"id": "tab-gh-2", "url": "https://job-boards.greenhouse.io/acme/jobs/2"},
        ])
        report = self.reconcile(cdp)
        self.assertEqual([item["job_id"] for item in report["submitted"]], ["gh-1"])
        self.assertEqual(report["submitted"][0]["via"], "page")
        self.assertEqual(self.store.get("gh-1")["state"], "submitted")
        self.assertEqual(self.tracker_ids(), ["gh-1"])
        self.assertEqual(cdp.closed, ["tab-gh-1"])
        self.assertEqual([item["job_id"] for item in report["waiting"]], ["gh-2"])
        self.assertEqual(self.store.get("gh-2")["state"], "filled")

    def test_closed_tab_without_confirmation_is_reported_gone_and_kept(self):
        self.held("gone-1", "https://job-boards.greenhouse.io/acme/jobs/7", state="reviewed")
        report = self.reconcile(FakeCDP([]))
        self.assertEqual(report["gone"], [{
            "job_id": "gone-1", "company": "Example Labs", "role": "Backend Software Engineering Intern",
            "url": "https://job-boards.greenhouse.io/acme/jobs/7",
        }])
        self.assertEqual(self.store.get("gone-1")["state"], "reviewed")
        self.assertEqual(self.tracker_ids(), [])

    def test_reopened_tab_with_a_new_id_is_matched_by_job_url(self):
        self.held("lv-1", "https://jobs.lever.co/acme/0b1c/apply")
        cdp = FakeCDP([{"id": "new-target", "url": "https://jobs.lever.co/acme/0b1c/thanks"}])
        report = self.reconcile(cdp)
        self.assertEqual([item["job_id"] for item in report["submitted"]], ["lv-1"])
        self.assertEqual(cdp.closed, ["new-target"])

    def test_gmail_confirmation_records_the_matching_held_job_once(self):
        self.held("ab-1", "https://jobs.ashbyhq.com/acme/0b1c/application", company="Acme Robotics",
                  role="Software Engineer Intern")
        cdp = FakeCDP([{"id": "tab-ab-1", "url": "https://jobs.ashbyhq.com/acme/0b1c/application"}])
        rows = [mail("Acme Robotics", "Software Engineer Intern", thread="th-ab")]
        report = self.reconcile(cdp, rows)
        self.assertEqual(report["submitted"], [{
            "job_id": "ab-1", "company": "Acme Robotics", "role": "Software Engineer Intern", "via": "email",
            "evidence": "Gmail: Thank you for applying for Software Engineer Intern at Acme Robotics",
        }])
        self.assertEqual(self.gmail_calls, [LANE])
        record = self.store.get("ab-1")
        self.assertEqual(record["state"], "submitted")
        self.assertEqual(record["confirmation"]["channel"], "gmail")
        self.assertEqual(self.tracker_ids(), ["ab-1"])
        self.assertEqual(cdp.closed, [], "a tab still on the form is never closed")
        self.assertEqual(report["history"]["added"], 0)
        again = self.reconcile(cdp, rows)
        self.assertEqual(again["submitted"], [])
        self.assertEqual(self.tracker_ids(), ["ab-1"])

    def test_email_only_match_is_listed_to_check_with_its_undo(self):
        self.held("ab-1", "https://jobs.ashbyhq.com/acme/0b1c/application", company="Acme Robotics",
                  role="Software Engineer Intern")
        cdp = FakeCDP([{"id": "tab-ab-1", "url": "https://jobs.ashbyhq.com/acme/0b1c/application"}])
        report = self.reconcile(cdp, [mail("Acme Robotics", "Software Engineer Intern", thread="th-ab")])
        self.assertEqual([item["job_id"] for item in report["submitted"]], ["ab-1"])
        self.assertEqual(report["check"], [{
            "job_id": "ab-1", "company": "Acme Robotics", "role": "Software Engineer Intern",
            "email_date": "2026-11-18", "undo": "track --undo ab-1",
        }])
        self.assertEqual(self.tracker_ids(), ["ab-1"], "an email-only match is still recorded")

    def test_match_backed_by_the_tabs_confirmation_page_is_not_listed_to_check(self):
        self.held("wd-1", "https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/Remote/Intern_R1/apply",
                  company="Acme Robotics", role="Software Engineer Intern")
        done = "https://acme.wd5.myworkdayjobs.com/en-US/Ext/jobTasks/completed/application"
        cdp = FakeCDP([{"id": "tab-wd-1", "url": done}])
        report = self.reconcile(cdp, [mail("Acme Robotics", "Software Engineer Intern", thread="th-wd")])
        self.assertEqual([item["job_id"] for item in report["submitted"]], ["wd-1"])
        self.assertEqual(report["check"], [])

    def test_job_confirmed_by_its_page_alone_is_not_listed_to_check(self):
        self.held("gh-1", "https://job-boards.greenhouse.io/acme/jobs/1")
        report = self.reconcile(FakeCDP([{"id": "tab-gh-1",
                                          "url": "https://job-boards.greenhouse.io/acme/jobs/1/confirmation"}]))
        self.assertEqual(report["check"], [])

    def test_undo_returns_an_email_only_match_to_its_held_state_and_never_rematches_that_email(self):
        self.held("ab-1", "https://jobs.ashbyhq.com/acme/0b1c/application", company="Acme Robotics",
                  role="Software Engineer Intern", state="reviewed")
        cdp = FakeCDP([{"id": "tab-ab-1", "url": "https://jobs.ashbyhq.com/acme/0b1c/application"}])
        rows = [mail("Acme Robotics", "Software Engineer Intern", thread="th-ab")]
        self.reconcile(cdp, rows)
        result = self.track.undo(self.root, "ab-1")
        self.assertEqual(result, {"job_id": "ab-1", "state": "reviewed"})
        record = self.store.get("ab-1")
        self.assertEqual(record["state"], "reviewed")
        self.assertNotIn("confirmation", record)
        self.assertEqual(record["tab_id"], "tab-ab-1")
        self.assertEqual(self.tracker_ids(), [])
        again = self.reconcile(cdp, rows)
        self.assertEqual(again["submitted"], [])
        self.assertEqual([item["job_id"] for item in again["waiting"]], ["ab-1"])
        self.assertEqual(self.tracker_ids(), [])

    def test_undo_refuses_a_job_not_recorded_from_email(self):
        self.held("gh-1", "https://job-boards.greenhouse.io/acme/jobs/1")
        self.held("gh-2", "https://job-boards.greenhouse.io/acme/jobs/2")
        self.reconcile(FakeCDP([{"id": "tab-gh-1",
                                 "url": "https://job-boards.greenhouse.io/acme/jobs/1/confirmation"}]))
        for job_id in ("gh-1", "gh-2"):
            with self.subTest(job_id=job_id), self.assertRaises(ValueError):
                self.track.undo(self.root, job_id)
        self.assertEqual(self.store.get("gh-1")["state"], "submitted")
        self.assertEqual(self.tracker_ids(), ["gh-1"])

    def test_undo_refuses_an_application_that_moved_to_a_later_stage(self):
        self.held("ab-1", "https://jobs.ashbyhq.com/acme/0b1c/application", company="Acme Robotics",
                  role="Software Engineer Intern")
        self.reconcile(FakeCDP([{"id": "tab-ab-1", "url": "https://jobs.ashbyhq.com/acme/0b1c/application"}]),
                       [mail("Acme Robotics", "Software Engineer Intern", thread="th-ab")])
        self.store.update_tracker_status("ab-1", "interview", "2026-11-19")
        with self.assertRaises(ValueError):
            self.track.undo(self.root, "ab-1")
        self.assertEqual(self.tracker_ids(), ["ab-1"])

    def test_email_for_another_role_does_not_match_but_unrelated_confirmations_join_history(self):
        self.held("wk-1", "https://apply.workable.com/acme/j/ABC/apply/", company="Acme Robotics",
                  role="Data Engineer Intern")
        cdp = FakeCDP([{"id": "tab-wk-1", "url": "https://apply.workable.com/acme/j/ABC/apply/"}])
        rows = [mail("Acme Robotics", "Hardware Intern", thread="th-hw"), mail("Northwind Analytics", thread="th-nw")]
        report = self.reconcile(cdp, rows)
        self.assertEqual(report["submitted"], [])
        self.assertEqual(self.store.get("wk-1")["state"], "filled")
        self.assertEqual(report["history"]["added"], 1, "an email naming a held job's company is held back")
        companies = sorted(row["company"] for row in self.store.tracker_rows())
        self.assertEqual(companies, ["Northwind Analytics"])
        self.assertEqual(report["held_back_emails"], 1)
        self.assertEqual([item["job_id"] for item in report["waiting"]], ["wk-1"])

    def test_company_only_email_matches_a_single_held_job_of_that_company(self):
        self.held("c-1", "https://jobs.lever.co/northwind/1/apply", company="Northwind Analytics")
        cdp = FakeCDP([{"id": "tab-c-1", "url": "https://jobs.lever.co/northwind/1/apply"}])
        report = self.reconcile(cdp, [mail("Northwind Analytics", thread="th-c")])
        self.assertEqual([item["job_id"] for item in report["submitted"]], ["c-1"])

    def test_company_only_email_is_ambiguous_for_two_held_jobs_of_that_company(self):
        self.held("c-1", "https://jobs.lever.co/northwind/1/apply", company="Northwind Analytics", role="ML Intern")
        self.held("c-2", "https://jobs.lever.co/northwind/2/apply", company="Northwind Analytics", role="Data Intern")
        cdp = FakeCDP([{"id": "tab-c-1", "url": "https://jobs.lever.co/northwind/1/apply"},
                       {"id": "tab-c-2", "url": "https://jobs.lever.co/northwind/2/apply"}])
        report = self.reconcile(cdp, [mail("Northwind Analytics", thread="th-c")])
        self.assertEqual(report["submitted"], [])
        self.assertEqual(self.store.get("c-1")["state"], "filled")
        self.assertEqual(self.store.get("c-2")["state"], "filled")
        self.assertEqual(report["history"]["added"], 0, "an ambiguous email is not added as a separate application")
        self.assertEqual(report["held_back_emails"], 1)
        self.assertEqual(self.tracker_ids(), [])

    def two_jobs_and_two_emails(self, order):
        # Many ATSs send two confirmations; the company-only one may belong to the job already matched.
        self.held("n-1", "https://jobs.lever.co/northwind/1/apply", company="Northwind Analytics", role="ML Intern")
        self.held("n-2", "https://jobs.lever.co/northwind/2/apply", company="Northwind Analytics", role="Data Intern")
        cdp = FakeCDP([{"id": "tab-n-1", "url": "https://jobs.lever.co/northwind/1/apply"},
                       {"id": "tab-n-2", "url": "https://jobs.lever.co/northwind/2/apply"}])
        rows = [mail("Northwind Analytics", "ML Intern", thread="th-role"),
                mail("Northwind Analytics", thread="th-plain")][::order]
        report = self.reconcile(cdp, rows)
        self.assertEqual([item["job_id"] for item in report["submitted"]], ["n-1"])
        self.assertEqual(self.store.get("n-2")["state"], "filled")
        self.assertEqual(report["held_back_emails"], 1)
        self.assertEqual(report["history"]["added"], 0)
        again = self.reconcile(cdp, rows)
        self.assertEqual(again["submitted"], [])
        self.assertEqual(self.store.get("n-2")["state"], "filled")
        self.assertEqual(self.tracker_ids(), ["n-1"])

    def test_company_only_email_after_a_role_match_never_marks_the_other_held_job(self):
        self.two_jobs_and_two_emails(1)

    def test_company_only_email_before_a_role_match_never_marks_the_other_held_job(self):
        self.two_jobs_and_two_emails(-1)

    def test_email_that_may_belong_to_a_job_just_submitted_from_its_page_is_held_back(self):
        self.store.enqueue(job("n-0", url="https://jobs.lever.co/northwind/0/apply", company="Northwind Analytics",
                               role="ML Intern"))
        self.store.update("n-0", state="claimed", held_at="2026-11-17T08:00:00+00:00")
        self.store.record_submit_intent("n-0", {"review_id": "r"})
        self.store.mark_submitted("n-0", {"status": "confirmed", "job_id": "n-0", "text": "page",
                                          "url": "https://jobs.lever.co/northwind/0/thanks",
                                          "confirmed_at": "2026-11-17T09:30:00+00:00"})
        self.held("n-2", "https://jobs.lever.co/northwind/2/apply", company="Northwind Analytics", role="Data Intern")
        cdp = FakeCDP([{"id": "tab-n-2", "url": "https://jobs.lever.co/northwind/2/apply"}])
        report = self.reconcile(cdp, [mail("Northwind Analytics", thread="th-plain")])
        self.assertEqual(report["submitted"], [])
        self.assertEqual(self.store.get("n-2")["state"], "filled")
        self.assertEqual(report["held_back_emails"], 1)
        self.assertEqual(self.tracker_ids(), ["n-0"])

    def test_submission_long_before_the_email_does_not_block_the_held_job(self):
        self.store.enqueue(job("n-0", url="https://jobs.lever.co/northwind/0/apply", company="Northwind Analytics",
                               role="ML Intern"))
        self.store.update("n-0", state="claimed")
        self.store.record_submit_intent("n-0", {"review_id": "r"})
        self.store.mark_submitted("n-0", {"status": "confirmed", "job_id": "n-0", "text": "page",
                                          "url": "https://jobs.lever.co/northwind/0/thanks",
                                          "confirmed_at": "2026-09-01T09:30:00+00:00"})
        self.held("n-2", "https://jobs.lever.co/northwind/2/apply", company="Northwind Analytics", role="Data Intern")
        cdp = FakeCDP([{"id": "tab-n-2", "url": "https://jobs.lever.co/northwind/2/apply"}])
        report = self.reconcile(cdp, [mail("Northwind Analytics", thread="th-plain")])
        self.assertEqual([item["job_id"] for item in report["submitted"]], ["n-2"])

    def test_tab_id_now_on_another_jobs_confirmation_page_is_not_this_job(self):
        self.held("gh-1", "https://job-boards.greenhouse.io/acme/jobs/1")
        cdp = FakeCDP([{"id": "tab-gh-1", "url": "https://job-boards.greenhouse.io/acme/jobs/2/confirmation"}])
        report = self.reconcile(cdp)
        self.assertEqual(report["submitted"], [])
        self.assertEqual([item["job_id"] for item in report["gone"]], ["gh-1"])
        self.assertEqual(self.store.get("gh-1")["state"], "filled")
        self.assertEqual(self.tracker_ids(), [])
        self.assertEqual(cdp.closed, [])

    def test_workday_page_naming_no_job_waits_for_the_email_and_keeps_the_tab(self):
        # The user may have moved the held tab to another posting of the same site and applied there.
        self.held("wd-1", "https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/Remote/Intern_R1/apply",
                  company="Acme Robotics", role="Software Engineer Intern")
        done = "https://acme.wd5.myworkdayjobs.com/en-US/Ext/jobTasks/completed/application"
        cdp = FakeCDP([{"id": "tab-wd-1", "url": done}])
        report = self.reconcile(cdp)
        self.assertEqual(report["submitted"], [])
        self.assertEqual(self.store.get("wd-1")["state"], "filled")
        self.assertEqual(self.tracker_ids(), [])
        self.assertEqual(cdp.closed, [])
        self.assertEqual(report["waiting"], [{
            "job_id": "wd-1", "company": "Acme Robotics", "role": "Software Engineer Intern", "tab_id": "tab-wd-1",
            "state": "filled", "page_url": done, "note": self.track.NO_JOB_NOTE,
        }])
        report = self.reconcile(cdp, [mail("Acme Robotics", "Software Engineer Intern", thread="th-wd")])
        self.assertEqual([(item["job_id"], item["via"]) for item in report["submitted"]], [("wd-1", "email")])
        self.assertEqual(cdp.closed, ["tab-wd-1"])

    def test_workday_confirmation_counts_only_for_the_posting_it_names(self):
        self.held("wd-1", "https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/Remote/Intern_R1/apply")
        self.held("wd-2", "https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/Remote/Intern_R2")
        own = "https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/Remote/Intern_R1/apply/applyManually?job_application_id=9"
        other = "https://acme.wd5.myworkdayjobs.com/Ext/job/Remote/Intern_R3/apply/applyManually?job_application_id=8"
        cdp = FakeCDP([{"id": "tab-wd-1", "url": own}, {"id": "tab-wd-2", "url": other}])
        report = self.reconcile(cdp)
        self.assertEqual([item["job_id"] for item in report["submitted"]], ["wd-1"])
        self.assertEqual(self.tracker_ids(), ["wd-1"])
        self.assertEqual([item["job_id"] for item in report["gone"]], ["wd-2"])
        self.assertEqual(self.store.get("wd-2")["state"], "filled")
        self.assertEqual(cdp.closed, ["tab-wd-1"])

    def test_reopened_workday_tab_without_locale_is_matched_by_its_posting(self):
        self.held("wd-1", "https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/Remote/Intern_R1")
        done = "https://acme.wd5.myworkdayjobs.com/Ext/job/Remote/Intern_R1/apply/autofillWithResume?job_application_id=7"
        report = self.reconcile(FakeCDP([{"id": "new-target", "url": done}]))
        self.assertEqual([item["job_id"] for item in report["submitted"]], ["wd-1"])
        neutral = "https://acme.wd5.myworkdayjobs.com/en-US/Ext/jobTasks/completed/application"
        self.held("wd-2", "https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/Remote/Intern_R2")
        report = self.reconcile(FakeCDP([{"id": "new-target-2", "url": neutral}]))
        self.assertEqual(report["submitted"], [])
        self.assertEqual([item["job_id"] for item in report["gone"]], ["wd-2"])

    def test_email_older_than_the_held_form_does_not_match(self):
        self.held("ab-1", "https://jobs.ashbyhq.com/acme/1/application", company="Acme Robotics",
                  role="Software Engineer Intern", held_at="2026-11-17T09:00:00+00:00")
        cdp = FakeCDP([{"id": "tab-ab-1", "url": "https://jobs.ashbyhq.com/acme/1/application"}])
        report = self.reconcile(cdp, [mail("Acme Robotics", "Software Engineer Intern", date="Nov 10, 2026")])
        self.assertEqual(report["submitted"], [])
        self.assertEqual(report["history"]["added"], 0)
        self.assertEqual(self.store.get("ab-1")["state"], "filled")

    def northwind_form(self, job_id="n-2"):
        self.held(job_id, f"https://jobs.lever.co/northwind/{job_id}/apply", company="Northwind Analytics",
                  role="Data Intern")
        return {"id": "tab-" + job_id, "url": f"https://jobs.lever.co/northwind/{job_id}/apply"}

    def assert_email_held_back(self, report, submitted=()):
        self.assertEqual([(item["job_id"], item["via"]) for item in report["submitted"]], list(submitted))
        self.assertEqual(self.store.get("n-2")["state"], "filled", "the email may belong to the other job")
        self.assertEqual(report["held_back_emails"], 1)
        self.assertEqual(report["history"]["added"], 0)

    def test_email_is_held_back_when_a_legacy_job_confirmed_by_its_page_in_this_run_could_have_sent_it(self):
        self.held("n-1", "https://jobs.lever.co/northwind/1/apply", company="Northwind Analytics", role="ML Intern",
                  held_at=None)
        cdp = FakeCDP([{"id": "tab-n-1", "url": "https://jobs.lever.co/northwind/1/thanks"}, self.northwind_form()])
        rows = [mail("Northwind Analytics", thread="th-plain")]
        self.assert_email_held_back(self.reconcile(cdp, rows), [("n-1", "page")])
        self.assertEqual(self.tracker_ids(), ["n-1"])
        self.assert_email_held_back(self.reconcile(cdp, rows))
        self.assertEqual(self.tracker_ids(), ["n-1"])

    def test_email_sent_days_before_a_legacy_job_was_seen_on_its_page_stays_held_back_on_later_runs(self):
        # The confirmation page may have been open for days before track saw it.
        self.held("n-1", "https://jobs.lever.co/northwind/1/apply", company="Northwind Analytics", role="ML Intern",
                  held_at=None)
        self.held("n-2", "https://jobs.lever.co/northwind/n-2/apply", company="Northwind Analytics",
                  role="Data Intern", held_at="2026-11-10T09:00:00+00:00")
        cdp = FakeCDP([{"id": "tab-n-1", "url": "https://jobs.lever.co/northwind/1/thanks"},
                       {"id": "tab-n-2", "url": "https://jobs.lever.co/northwind/n-2/apply"}])
        rows = [mail("Northwind Analytics", thread="th-plain", date="Nov 15, 2026")]
        self.assert_email_held_back(self.reconcile(cdp, rows), [("n-1", "page")])
        for _ in range(2):
            self.assert_email_held_back(self.reconcile(cdp, rows))
        self.assertEqual(self.tracker_ids(), ["n-1"])

    def test_email_is_held_back_when_a_legacy_held_job_still_on_its_form_could_have_sent_it(self):
        self.held("n-1", "https://jobs.lever.co/northwind/1/apply", company="Northwind Analytics", role="ML Intern",
                  held_at=None)
        cdp = FakeCDP([{"id": "tab-n-1", "url": "https://jobs.lever.co/northwind/1/apply"}, self.northwind_form()])
        self.assert_email_held_back(self.reconcile(cdp, [mail("Northwind Analytics", thread="th-plain")]))
        self.assertEqual(self.store.get("n-1")["state"], "filled")
        self.assertEqual(self.tracker_ids(), [])

    def test_email_naming_the_role_still_matches_next_to_a_legacy_job_with_another_role(self):
        self.held("n-1", "https://jobs.lever.co/northwind/1/apply", company="Northwind Analytics", role="ML Intern",
                  held_at=None)
        cdp = FakeCDP([{"id": "tab-n-1", "url": "https://jobs.lever.co/northwind/1/apply"}, self.northwind_form()])
        report = self.reconcile(cdp, [mail("Northwind Analytics", "Data Intern", thread="th-role")])
        self.assertEqual([(item["job_id"], item["via"]) for item in report["submitted"]], [("n-2", "email")])
        self.assertEqual(self.store.get("n-1")["state"], "filled")

    def test_email_is_held_back_when_a_filled_job_without_a_tab_could_have_sent_it(self):
        self.store.enqueue(job("n-1", url="https://jobs.lever.co/northwind/1/apply", company="Northwind Analytics",
                               role="ML Intern"))
        self.store.update("n-1", state="filled", held_at="2026-11-17T08:00:00+00:00")
        cdp = FakeCDP([self.northwind_form()])
        self.assert_email_held_back(self.reconcile(cdp, [mail("Northwind Analytics", thread="th-plain")]))
        self.assertEqual(self.tracker_ids(), [])

    def test_email_is_held_back_when_a_job_being_recorded_by_hand_could_have_sent_it(self):
        self.store.enqueue(job("n-1", url="https://jobs.lever.co/northwind/1/apply", company="Northwind Analytics",
                               role="ML Intern"))
        self.store.update("n-1", state="claimed")
        self.store.record_submit_intent("n-1", {"review_id": "by-hand"})
        cdp = FakeCDP([self.northwind_form()])
        self.assert_email_held_back(self.reconcile(cdp, [mail("Northwind Analytics", thread="th-plain")]))
        self.assertEqual(self.tracker_ids(), [])

    def test_email_is_held_back_when_a_recent_application_that_moved_to_a_later_stage_could_have_sent_it(self):
        self.store.enqueue(job("n-1", url="https://jobs.lever.co/northwind/1/apply", company="Northwind Analytics",
                               role="ML Intern"))
        self.store.update("n-1", state="claimed")
        self.store.record_submit_intent("n-1", {"review_id": "r"})
        self.store.mark_submitted("n-1", {"status": "confirmed", "job_id": "n-1", "text": "page",
                                          "url": "https://jobs.lever.co/northwind/1/thanks",
                                          "confirmed_at": "2026-11-17T09:30:00+00:00"})
        self.store.update_tracker_status("n-1", "interview", "2026-11-18T08:00:00+00:00")
        cdp = FakeCDP([self.northwind_form()])
        self.assert_email_held_back(self.reconcile(cdp, [mail("Northwind Analytics", thread="th-plain")]))
        self.assertEqual(self.tracker_ids(), ["n-1"])


    def recorded_legacy_job(self, **fields):
        # Recorded before `held_at` existed: only its own `created_at` (if any) dates it.
        self.store.enqueue(job("n-1", url="https://jobs.lever.co/northwind/1/apply", company="Northwind Analytics",
                               role="ML Intern"))
        self.store.update("n-1", state="claimed", **fields)
        self.store.record_submit_intent("n-1", {"review_id": "r"})
        self.store.mark_submitted("n-1", {"status": "confirmed", "job_id": "n-1", "text": "page",
                                          "url": "https://jobs.lever.co/northwind/1/thanks",
                                          "confirmed_at": "2026-11-16T09:30:00+00:00"})
        self.held("n-2", "https://jobs.lever.co/northwind/n-2/apply", company="Northwind Analytics",
                  role="Data Intern", held_at="2026-11-10T09:00:00+00:00")
        return FakeCDP([{"id": "tab-n-2", "url": "https://jobs.lever.co/northwind/n-2/apply"}])

    def test_recorded_legacy_job_does_not_hold_back_an_email_sent_before_it_was_created(self):
        cdp = self.recorded_legacy_job(created_at="2026-11-14T08:00:00+00:00")
        report = self.reconcile(cdp, [mail("Northwind Analytics", thread="th-plain", date="Nov 12, 2026")])
        self.assertEqual([(item["job_id"], item["via"]) for item in report["submitted"]], [("n-2", "email")])
        self.assertEqual(report["held_back_emails"], 0)

    def test_recorded_legacy_job_still_holds_back_an_email_sent_after_it_was_created(self):
        cdp = self.recorded_legacy_job(created_at="2026-11-14T08:00:00+00:00")
        self.assert_email_held_back(
            self.reconcile(cdp, [mail("Northwind Analytics", thread="th-plain", date="Nov 15, 2026")]))
        self.assertEqual(self.tracker_ids(), ["n-1"])

    def test_recorded_legacy_job_without_any_day_still_holds_back_an_older_email(self):
        cdp = self.recorded_legacy_job()
        self.assert_email_held_back(
            self.reconcile(cdp, [mail("Northwind Analytics", thread="th-plain", date="Nov 12, 2026")]))
        self.assertEqual(self.tracker_ids(), ["n-1"])
    def test_legacy_held_job_matches_an_exact_role_confirmation_from_the_last_two_weeks(self):
        # Held before `held_at` existed: no held day, so the email must name the exact role.
        self.held("ab-1", "https://jobs.ashbyhq.com/acme/1/application", company="Acme Robotics",
                  role="Software Engineer Intern", held_at=None)
        cdp = FakeCDP([{"id": "tab-ab-1", "url": "https://jobs.ashbyhq.com/acme/1/application"}])
        report = self.reconcile(cdp, [mail("Acme Robotics", "Software Engineer Intern", thread="th-l",
                                           date="Nov 5, 2026")])
        self.assertEqual([(item["job_id"], item["via"]) for item in report["submitted"]], [("ab-1", "email")])
        self.assertEqual(self.store.get("ab-1")["state"], "submitted")
        self.assertEqual(self.tracker_ids(), ["ab-1"])
        self.assertEqual(report["history"]["added"], 0)

    def test_legacy_held_job_needs_a_role_a_recent_date_and_no_earlier_application(self):
        self.held("ab-1", "https://jobs.ashbyhq.com/acme/1/application", company="Acme Robotics",
                  role="Software Engineer Intern", held_at=None)
        cdp = FakeCDP([{"id": "tab-ab-1", "url": "https://jobs.ashbyhq.com/acme/1/application"}])
        rows = [mail("Acme Robotics", thread="th-1"),
                mail("Acme Robotics", "Software Engineer Intern", thread="th-2", date="Nov 3, 2026"),
                mail("Acme Robotics", "Software Engineer Intern", thread="th-3", date="sometime")]
        report = self.reconcile(cdp, rows)
        self.assertEqual(report["submitted"], [])
        self.assertEqual(report["held_back_emails"], 3, "none of them may become a separate application")
        self.assertEqual(report["history"]["added"], 0)
        self.assertEqual(self.store.get("ab-1")["state"], "filled")
        # The tracker already holds this company and role: a new email may belong to that application.
        self.store.enqueue(job("earlier", url="https://jobs.ashbyhq.com/acme/0/application", company="Acme Robotics",
                               role="Software Engineer Intern"))
        self.store.update("earlier", state="claimed")
        self.store.record_submit_intent("earlier", {"review_id": "r"})
        self.store.mark_submitted("earlier", {"status": "confirmed", "job_id": "earlier", "text": "seen",
                                              "url": "https://jobs.ashbyhq.com/acme/0/application"})
        report = self.reconcile(cdp, [mail("Acme Robotics", "Software Engineer Intern", thread="th-4")])
        self.assertEqual(report["submitted"], [])
        self.assertEqual(self.store.get("ab-1")["state"], "filled")
        self.assertEqual(self.tracker_ids(), ["earlier"])

    def test_email_without_a_date_does_not_match(self):
        self.held("ab-2", "https://jobs.ashbyhq.com/acme/2/application", company="Bolt Freight", role="Data Intern")
        cdp = FakeCDP([{"id": "tab-ab-2", "url": "https://jobs.ashbyhq.com/acme/2/application"}])
        report = self.reconcile(cdp, [mail("Bolt Freight", "Data Intern", thread="th-b", date="sometime")])
        self.assertEqual(report["submitted"], [])
        self.assertEqual(self.store.get("ab-2")["state"], "filled")

    def test_partial_company_or_role_names_do_not_match(self):
        self.held("m-1", "https://jobs.lever.co/meta/1/apply", company="Meta", role="Software Engineer Intern")
        self.held("m-2", "https://jobs.lever.co/acme/2/apply", company="Acme Robotics", role="Software Engineer Intern")
        cdp = FakeCDP([{"id": "tab-m-1", "url": "https://jobs.lever.co/meta/1/apply"},
                       {"id": "tab-m-2", "url": "https://jobs.lever.co/acme/2/apply"}])
        rows = [mail("Metadata Systems", "Software Engineer Intern", thread="th-1"),
                mail("Acme Robotics", "Intern", thread="th-2")]
        report = self.reconcile(cdp, rows)
        self.assertEqual(report["submitted"], [])
        self.assertEqual(self.store.get("m-1")["state"], "filled")
        self.assertEqual(self.store.get("m-2")["state"], "filled")

    def test_company_suffixes_and_case_still_match(self):
        self.held("s-1", "https://jobs.lever.co/acme/1/apply", company="Acme Robotics, Inc.",
                  role="Software Engineer Intern")
        cdp = FakeCDP([{"id": "tab-s-1", "url": "https://jobs.lever.co/acme/1/apply"}])
        report = self.reconcile(cdp, [mail("ACME Robotics", "software engineer intern")])
        self.assertEqual([item["job_id"] for item in report["submitted"]], ["s-1"])

    def test_gmail_lookback_covers_the_oldest_held_form(self):
        self.reconcile(FakeCDP([]))
        self.assertEqual(self.gmail_days, self.track.TRACK_DAYS)
        self.held("old-1", "https://jobs.lever.co/acme/1/apply", held_at="2026-10-09T23:00:00+00:00")
        self.held("new-1", "https://jobs.lever.co/acme/2/apply", held_at="2026-11-17T09:00:00+00:00")
        self.reconcile(FakeCDP([]))
        self.assertEqual(self.gmail_days, 41, "clock 2026-11-18 minus held 2026-10-09, plus one day")
        self.held("ancient", "https://jobs.lever.co/acme/3/apply", held_at="2025-01-01T00:00:00+00:00")
        self.reconcile(FakeCDP([]))
        self.assertEqual(self.gmail_days, self.module("mail_history").HISTORY_DAYS)

    def test_default_gmail_reader_pages_the_whole_lookback(self):
        calls = []

        class Tab:
            def __init__(self, debug_url):
                calls.append(debug_url)

            def history(self, days, max_pages):
                calls.append((days, max_pages))
                return []

        with patch("autoapply_local.gmail_tab.GmailTab", Tab):
            self.assertEqual(self.track._gmail_rows(LANE, 41), [])
        self.assertEqual(calls, [LANE, (41, self.module("gmail_tab").MAX_PAGES)])

    def test_chrome_not_running_changes_nothing(self):
        self.held("gh-1", "https://job-boards.greenhouse.io/acme/jobs/1")
        report = self.reconcile(FakeCDP(down=True), [mail("Example Labs", thread="th-x")])
        self.assertEqual([item["job_id"] for item in report["unknown"]], ["gh-1"])
        self.assertEqual(report["submitted"], [])
        self.assertIn("not reachable", report["gmail"])
        self.assertEqual(self.gmail_calls, [])
        self.assertEqual(self.store.get("gh-1")["state"], "filled")
        self.assertEqual(self.tracker_ids(), [])

    def test_gmail_failure_still_reconciles_tabs(self):
        self.held("gh-1", "https://job-boards.greenhouse.io/acme/jobs/1")
        cdp = FakeCDP([{"id": "tab-gh-1", "url": "https://job-boards.greenhouse.io/acme/jobs/1/confirmation"}])

        def broken(debug_url, days):
            raise RuntimeError("Gmail is not signed in")

        report = self.track.run(self.root, cdp=cdp, gmail=broken, clock=lambda: "2026-11-18")
        self.assertEqual([item["job_id"] for item in report["submitted"]], ["gh-1"])
        self.assertIn("Gmail is not signed in", report["gmail"])

    def test_interrupted_recording_resumes_on_the_next_run(self):
        self.held("gh-1", "https://job-boards.greenhouse.io/acme/jobs/1")
        cdp = FakeCDP([{"id": "tab-gh-1", "url": "https://job-boards.greenhouse.io/acme/jobs/1/confirmation"}])
        original = self.store.mark_submitted
        with patch.object(type(self.store), "mark_submitted", side_effect=OSError("tracker locked")):
            first = self.reconcile(cdp)
        self.assertEqual(first["submitted"], [])
        self.assertIn("tracker locked", first["problems"][0]["problem"])
        self.assertNotEqual(self.store.get("gh-1")["state"], "submitted")
        self.assertEqual(cdp.closed, [])
        self.assertTrue(callable(original))
        second = self.reconcile(cdp)
        self.assertEqual([item["job_id"] for item in second["submitted"]], ["gh-1"])
        self.assertEqual(self.tracker_ids(), ["gh-1"])

    def test_submitted_jobs_and_jobs_without_tabs_are_not_tracked(self):
        self.store.enqueue(job("pending-1"))
        report = self.reconcile(FakeCDP([]))
        self.assertEqual(report["gone"] + report["waiting"] + report["unknown"], [])

    def test_track_never_runs_a_script_in_a_job_tab(self):
        import inspect

        source = inspect.getsource(self.track)
        self.assertNotIn("evaluate", source)
        self.assertNotIn("click", source.lower())


class ClosePageTests(ReleaseCase):
    def test_close_page_calls_the_loopback_close_endpoint_only(self):
        cdp = self.module("cdp")
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"Target is closing"

        def opener(request, timeout):
            requests.append(request.full_url)
            return Response()

        cdp.close_page(LANE, "ABC-123", opener=opener)
        self.assertEqual(requests, [LANE + "/json/close/ABC-123"])
        with self.assertRaises(ValueError):
            cdp.close_page("http://example.invalid:9222", "ABC", opener=opener)
        with self.assertRaises(ValueError):
            cdp.close_page(LANE, "../json/new?x", opener=opener)
        self.assertEqual(len(requests), 1)


class TrackCommandTests(ReleaseCase):
    def test_cli_dispatches_track(self):
        calls = []
        services = {"track": lambda **kwargs: calls.append(kwargs) or {"submitted": []}}
        out, err = io.StringIO(), io.StringIO()
        code = self.module("cli").main(["--user-dir", str(self.root), "track"], services=services, stdout=out,
                                       stderr=err)
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(calls, [{"root": self.root}])
        self.assertEqual(json.loads(out.getvalue()), {"submitted": []})

    def test_cli_dispatches_track_undo(self):
        calls = []
        services = {"track": lambda **kwargs: calls.append(kwargs) or {"job_id": "ab-1"}}
        out, err = io.StringIO(), io.StringIO()
        code = self.module("cli").main(["--user-dir", str(self.root), "track", "--undo", "ab-1"], services=services,
                                       stdout=out, stderr=err)
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(calls, [{"root": self.root, "undo": "ab-1"}])

    def test_runtime_track_undo_calls_track_undo(self):
        runtime = self.module("runtime")
        with patch("autoapply_local.track.undo", return_value={"job_id": "ab-1"}) as undo:
            self.assertEqual(runtime.track(root=self.root, undo="ab-1"), {"job_id": "ab-1"})
        undo.assert_called_once_with(self.root, "ab-1")

    def test_apply_end_table_lists_email_only_matches_with_their_undo(self):
        text = self.module("apply_run").table({"track": {
            "submitted": [{"job_id": "ab-1", "company": "Acme Robotics", "role": "Software Engineer Intern"}],
            "check": [{"job_id": "ab-1", "company": "Acme Robotics", "role": "Software Engineer Intern",
                       "email_date": "2026-11-18", "undo": "track --undo ab-1"}],
        }})
        self.assertIn("Recorded from email only, check it:", text)
        self.assertIn("ab-1 | Acme Robotics | Software Engineer Intern | 2026-11-18 | undo: track --undo ab-1", text)
        self.assertNotIn("check it", self.module("apply_run").table({"track": {"submitted": [], "check": []}}))

    def test_runtime_exposes_track(self):
        runtime = self.module("runtime")
        self.assertIn("track", runtime.services())
        with patch("autoapply_local.track.run", return_value={"submitted": []}) as run:
            self.assertEqual(runtime.track(root=self.root), {"submitted": []})
        run.assert_called_once_with(self.root)

    def test_apply_tracks_before_choosing_jobs(self):
        runtime = self.module("runtime")
        order = []
        with (
            patch("autoapply_local.track.run", side_effect=lambda root: order.append("track") or {"submitted": []}),
            patch("autoapply_local.runtime.configuration", side_effect=lambda root: order.append("config") or {}),
        ):
            with self.assertRaises(Exception):
                runtime.apply(root=self.root, selection=["missing"])
        self.assertEqual(order[:2], ["config", "track"])
