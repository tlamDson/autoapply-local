"""Two confirms racing on one answer bank must both survive (PR #17 review)."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from autoapply_local import answer_bank
from autoapply_local.answer_bank import AnswerBank


JOB = {"company": "Example Robotics", "country": "United States", "level": "intern", "start_date": "2027-06"}
CLUB = {"id": "q1", "label": "Which clubs are you a member of?", "type": "text", "options": []}
LANG = {"id": "q2", "label": "Which programming languages do you know?", "type": "text", "options": []}


class ConcurrentConfirmTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_interleaved_confirms_keep_both_answers(self):
        first_loaded = threading.Event()
        second_done = threading.Event()
        real_load = AnswerBank._load
        calls = []

        def slow_load(bank):
            data = real_load(bank)
            calls.append(threading.current_thread().name)
            if threading.current_thread().name == "first":
                first_loaded.set()
                # Without a lock the second confirm finishes here and its answer is overwritten.
                second_done.wait(timeout=1.5)
            return data

        errors = []

        def run(field, value):
            try:
                AnswerBank(self.root).confirm(field, JOB, value, source_id="synthetic")
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        with mock.patch.object(AnswerBank, "_load", slow_load):
            first = threading.Thread(target=run, args=(CLUB, "Chess Club"), name="first")
            first.start()
            self.assertTrue(first_loaded.wait(timeout=5))
            second = threading.Thread(target=run, args=(LANG, "Python"), name="second")
            second.start()
            second.join(timeout=0.5)
            if not second.is_alive():
                second_done.set()
            first.join(timeout=10)
            second.join(timeout=10)

        self.assertEqual(errors, [])
        bank = AnswerBank(self.root)
        self.assertEqual((bank.lookup(CLUB, JOB) or {}).get("value"), "Chess Club")
        self.assertEqual((bank.lookup(LANG, JOB) or {}).get("value"), "Python", "the second answer was lost")

    def test_confirm_holds_the_storage_lock_across_load_and_write(self):
        seen = []
        real_lock = answer_bank.lock if hasattr(answer_bank, "lock") else None
        self.assertIsNotNone(real_lock, "answer_bank must use storage.lock")
        held = threading.local()

        from contextlib import contextmanager

        @contextmanager
        def tracking_lock(path, *args, **kwargs):
            with real_lock(path, *args, **kwargs):
                held.on = True
                try:
                    yield
                finally:
                    held.on = False

        real_load = AnswerBank._load
        real_write = answer_bank.atomic_write_json

        def load(bank):
            seen.append(("load", getattr(held, "on", False)))
            return real_load(bank)

        def write(path, data):
            seen.append(("write", getattr(held, "on", False)))
            return real_write(path, data)

        with mock.patch.object(answer_bank, "lock", tracking_lock), \
                mock.patch.object(AnswerBank, "_load", load), \
                mock.patch.object(answer_bank, "atomic_write_json", write):
            AnswerBank(self.root).confirm(CLUB, JOB, "Chess Club", source_id="synthetic")

        self.assertEqual(seen, [("load", True), ("write", True)])


if __name__ == "__main__":
    unittest.main()
