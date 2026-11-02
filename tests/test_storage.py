"""Temporary storage only: locking, crash recovery, dedup and authoritative XLSX.

Giả định của người thực thi: storage.Store(root) owns the isolated ledger and
tracker.xlsx; claim returns bool, jobs/get return JSON records. JSON helpers are
atomic_write_json(path,value), read_json(path), recover_json(path), lock(path).
Giả định của người thực thi: historical stages normalize to lowercase in the
tracker; unknown stages fail explicitly before importing instead of becoming pending.
"""

import concurrent.futures
import csv
import json
import multiprocessing
import time
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from openpyxl import Workbook

from test_support import ReleaseCase, job


def claim_in_process(root, start, output):
    from autoapply_local.storage import Store

    start.wait(10)
    output.put(Store(Path(root)).claim("synthetic-1", lane=multiprocessing.current_process().name))


class StorageTests(ReleaseCase):
    def store(self):
        return self.module("storage").Store(self.root)

    def test_duplicate_job_identity_is_enqueued_once(self):
        store = self.store()
        store.enqueue(job())
        store.enqueue(job(url=job()["url"] + "?source=example"))
        self.assertEqual([record["id"] for record in store.jobs()], ["synthetic-1"])

    def test_same_identity_claimed_once_across_concurrent_lanes(self):
        store = self.store()
        store.enqueue(job())
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            claimed = list(executor.map(lambda lane: store.claim("synthetic-1", lane=str(lane)), range(3)))
        self.assertEqual(sum(claimed), 1)

    def test_claim_is_exclusive_across_processes(self):
        store = self.store()
        store.enqueue(job())
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        output = context.Queue()
        processes = [context.Process(target=claim_in_process, args=(str(self.root), start, output)) for _ in range(3)]
        try:
            for process in processes:
                process.start()
            start.set()
            results = [output.get(timeout=15) for _ in processes]
            for process in processes:
                process.join(15)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sum(results), 1)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(5)
            output.close()

    def test_claim_and_submit_intent_survive_store_recreation(self):
        store = self.store()
        store.enqueue(job())
        self.assertTrue(store.claim("synthetic-1", lane="1"))
        store.record_submit_intent("synthetic-1", {"review_id": "review-1", "tab_id": "tab-1"})
        reopened = self.store()
        self.assertEqual(reopened.get("synthetic-1")["state"], "submit_intent")
        self.assertFalse(reopened.claim("synthetic-1", lane="2"))
        self.assertEqual(reopened.tracker_rows(), [])

    def test_missing_confirmation_never_marks_submitted(self):
        store = self.store()
        store.enqueue(job())
        store.claim("synthetic-1", lane="1")
        store.record_submit_intent("synthetic-1", {"review_id": "review-1"})
        for evidence in ({}, {"status": "clicked"}, {"status": "unknown"}):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                store.mark_submitted("synthetic-1", evidence)
        self.assertEqual(store.tracker_rows(), [])

    def test_confirmation_for_another_job_is_rejected(self):
        store = self.store()
        store.enqueue(job())
        store.claim("synthetic-1", lane="1")
        store.record_submit_intent("synthetic-1", {"review_id": "review-1"})
        with self.assertRaises(ValueError):
            store.mark_submitted(
                "synthetic-1",
                {
                    "status": "confirmed",
                    "job_id": "other",
                    "url": "https://jobs.example.invalid/other/confirmation",
                    "text": "Received",
                },
            )
        self.assertEqual(store.tracker_rows(), [])

    def test_confirmed_submission_written_once_to_authoritative_xlsx(self):
        store = self.store()
        store.enqueue(job())
        store.claim("synthetic-1", lane="1")
        store.record_submit_intent("synthetic-1", {"review_id": "review-1"})
        evidence = {
            "status": "confirmed",
            "job_id": "synthetic-1",
            "url": job()["url"] + "/confirmation",
            "text": "Application received",
        }
        store.mark_submitted("synthetic-1", evidence)
        store.mark_submitted("synthetic-1", evidence)
        self.assertEqual(len(store.tracker_rows()), 1)
        self.assertEqual(store.tracker_rows()[0]["id"], "synthetic-1")
        self.assertTrue(zipfile.is_zipfile(self.root / "tracker.xlsx"))

    def test_tracker_strings_cannot_be_excel_formulas(self):
        store = self.store()
        for index, prefix in enumerate(("=", "+", "-", "@", "\t=", "\r=", "\n=")):
            item = job(f"synthetic-{index}", company=prefix + "Synthetic Formula")
            store.enqueue(item)
            store.claim(item["id"], lane="1")
            store.record_submit_intent(item["id"], {"review_id": f"review-{index}"})
            store.mark_submitted(
                item["id"],
                {
                    "status": "confirmed",
                    "job_id": item["id"],
                    "url": item["url"] + "/confirmation",
                    "text": "Application received",
                },
            )
        namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with zipfile.ZipFile(self.root / "tracker.xlsx") as workbook:
            for name in workbook.namelist():
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    sheet = ElementTree.fromstring(workbook.read(name))
                    self.assertEqual(sheet.findall(".//s:f", namespace), [])

    def test_atomic_replacement_failure_preserves_original(self):
        storage = self.module("storage")
        path = self.write_json("state.json", {"revision": 1})
        with patch("os.replace", side_effect=OSError("synthetic interrupted replacement")):
            with self.assertRaises(OSError):
                storage.atomic_write_json(path, {"revision": 2})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"revision": 1})

    def test_recovery_uses_last_valid_backup(self):
        storage = self.module("storage")
        path = self.root / "state.json"
        storage.atomic_write_json(path, {"revision": 1})
        storage.atomic_write_json(path, {"revision": 2})
        path.write_text("{interrupted", encoding="utf-8")
        recovered = storage.recover_json(path)
        self.assertEqual(recovered, {"revision": 1})
        self.assertEqual(storage.read_json(path), recovered)

    def test_lock_released_after_exception(self):
        storage = self.module("storage")
        lock_path = self.root / "synthetic.lock"
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            with storage.lock(lock_path, timeout=1):
                raise RuntimeError("synthetic")
        with storage.lock(lock_path, timeout=1):
            self.assertTrue(True)

    def test_parallel_ledger_writes_do_not_drop_unrelated_jobs(self):
        store = self.store()
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(lambda index: store.enqueue(job(f"synthetic-{index}")), range(20)))
        self.assertEqual(
            {record["id"] for record in self.store().jobs()}, {f"synthetic-{index}" for index in range(20)}
        )

    def test_missing_ledger_recovers_all_tracker_stages_and_blocks_duplicate_url_claim(self):
        storage = self.module("storage")
        for stage in ("submitted", "rejected", "assessment", "interview", "offer"):
            with self.subTest(stage=stage):
                root = self.root / stage
                store = storage.Store(root)
                item = job()
                store.enqueue(item)
                store.claim(item["id"])
                store.record_submit_intent(item["id"], {"review_id": "synthetic-review"})
                store.mark_submitted(
                    item["id"],
                    {
                        "status": "confirmed",
                        "job_id": item["id"],
                        "url": item["url"] + "/confirmation",
                        "text": "Synthetic application received",
                    },
                )
                if stage != "submitted":
                    store.update_tracker_status(item["id"], stage)
                tracker_before = store.tracker.read_bytes()
                store.path.unlink()
                store.path.with_name(store.path.name + ".bak").unlink(missing_ok=True)

                restarted = storage.Store(root)
                recovered = {record["id"]: record for record in restarted.jobs()}
                self.assertIn(item["id"], recovered, "The tracker must reconstruct lost submitted identity")
                self.assertNotEqual(recovered[item["id"]]["state"], "pending")
                duplicate = restarted.enqueue(job("rediscovered", url=item["url"] + "?source=example"))
                self.assertEqual(duplicate["id"], item["id"])
                self.assertFalse(restarted.claim(duplicate["id"]))
                self.assertEqual([row["status"] for row in restarted.tracker_rows()], [stage])
                self.assertEqual(restarted.tracker.read_bytes(), tracker_before)

    def history_file(self, root, suffix, stages):
        root.mkdir(parents=True, exist_ok=True)
        path = root / ("history" + suffix)
        columns = ("id", "company", "role", "url", "status")
        rows = [job(f"history-{index}", status=stage) for index, stage in enumerate(stages)]
        if suffix == ".csv":
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
        else:
            workbook = Workbook()
            workbook.active.append(columns)
            for row in rows:
                workbook.active.append([row[key] for key in columns])
            workbook.save(path)
            workbook.close()
        return path, rows

    def test_csv_and_xlsx_history_preserve_later_stages_and_never_become_applicable(self):
        storage = self.module("storage")
        for suffix in (".csv", ".xlsx"):
            with self.subTest(format=suffix):
                root = self.root / suffix.lstrip(".")
                source, rows = self.history_file(root, suffix, ("Rejected", "Interview", "Offer"))
                store = storage.Store(root / "runtime")
                preview = store.preview_history(source)
                result = store.import_history(preview, confirm=False)
                self.assertIs(result["applied"], False)
                self.assertEqual(store.jobs(), [])
                self.assertEqual(store.tracker_rows(), [])

                result = store.import_history(preview, confirm=True)
                self.assertIs(result["applied"], True)
                self.assertEqual(result["count"], 3)
                store.import_history(preview, confirm=True)
                persisted = {row["id"]: row for row in store.tracker_rows()}
                self.assertEqual(set(persisted), {row["id"] for row in rows})
                for row in rows:
                    with self.subTest(stage=row["status"]):
                        self.assertEqual(persisted[row["id"]]["status"], row["status"].lower())
                        self.assertEqual(persisted[row["id"]]["url"], row["url"])
                        self.assertNotEqual(store.get(row["id"])["state"], "pending")
                        self.assertFalse(store.claim(row["id"]))
                        duplicate = store.enqueue(job("rediscovered-" + row["id"], url=row["url"]))
                        self.assertEqual(duplicate["id"], row["id"])
                        self.assertFalse(store.claim(duplicate["id"]))

    def test_unknown_history_stage_fails_explicitly_without_partial_import(self):
        storage = self.module("storage")
        for suffix in (".csv", ".xlsx"):
            with self.subTest(format=suffix):
                root = self.root / suffix.lstrip(".")
                source, _ = self.history_file(root, suffix, ("Applied", "Unrecognized historical stage"))
                store = storage.Store(root / "runtime")
                with self.assertRaises(ValueError):
                    preview = store.preview_history(source)
                    store.import_history(preview, confirm=True)
                self.assertEqual(store.jobs(), [])
                self.assertEqual(store.tracker_rows(), [])
