"""Crash-safe local state and the authoritative application workbook.

Giả định của người thực thi: one advisory store lock protects the JSON ledger
and workbook together; interrupted multi-file commits are reconciled from XLSX.
History import uses a content-bound preview before explicit confirmation.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zipfile import BadZipFile

from openpyxl import Workbook, load_workbook


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_GUARD = threading.Lock()
_COLUMNS = ("id", "company", "role", "url", "status", "confirmation", "updated_at")
_HISTORY_STAGES = {"submitted", "rejected", "assessment", "interview", "offer"}


@contextmanager
def lock(path, timeout=10):
    """Take a Windows/POSIX advisory lock, including exclusion between threads."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    with _THREAD_GUARD:
        local = _THREAD_LOCKS.setdefault(key, threading.RLock())
    deadline = time.monotonic() + timeout
    if not local.acquire(timeout=max(0, timeout)):
        raise TimeoutError(f"Lock busy: {path.name}")
    stream = None
    acquired = False
    try:
        stream = path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Lock busy: {path.name}") from None
                time.sleep(0.02)
        yield
    finally:
        if stream is not None:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
        local.release()


def _atomic_bytes(path: Path, content: bytes, backup=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if backup and path.exists():
            _atomic_bytes(path.with_name(path.name + ".bak"), path.read_bytes(), backup=False)
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write_json(path, value):
    path = Path(path)
    content = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with lock(path.with_name(path.name + ".lock")):
        _atomic_bytes(path, content)


def read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def recover_json(path):
    path = Path(path)
    with lock(path.with_name(path.name + ".lock")):
        try:
            return read_json(path)
        except (OSError, ValueError):
            recovered = read_json(path.with_name(path.name + ".bak"))
            content = (json.dumps(recovered, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            _atomic_bytes(path, content, backup=False)
            return recovered


def _canonical_url(url):
    from .identity import canonical_url

    return canonical_url(url)


def _safe_cell(value):
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "ledger.json"
        self.tracker = self.root / "tracker.xlsx"
        self.lock_path = self.root / ".store.lock"
        with lock(self.lock_path):
            if not self.path.exists():
                self._save({"schema_version": 1, "jobs": {}, "mail_events": []})
            if not self.tracker.exists():
                workbook = Workbook()
                workbook.active.title = "Applications"
                workbook.active.append(_COLUMNS)
                self._save_workbook(workbook)
            self._reconcile_tracker()

    def _load(self):
        try:
            return read_json(self.path)
        except (OSError, ValueError):
            return recover_json(self.path)

    def _save(self, ledger):
        atomic_write_json(self.path, ledger)

    def _save_workbook(self, workbook):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, suffix=".xlsx", delete=False) as stream:
                temporary = Path(stream.name)
            workbook.save(temporary)
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            if self.tracker.exists():
                _atomic_bytes(self.tracker.with_name("tracker.xlsx.bak"), self.tracker.read_bytes(), backup=False)
            os.replace(temporary, self.tracker)
        finally:
            workbook.close()
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _tracker_rows(self):
        try:
            workbook = load_workbook(self.tracker, read_only=True, data_only=False)
        except (OSError, ValueError, BadZipFile, KeyError):
            backup = self.tracker.with_name("tracker.xlsx.bak")
            workbook = load_workbook(backup, read_only=True, data_only=False)
            _atomic_bytes(self.tracker, backup.read_bytes(), backup=False)
        try:
            rows = iter(workbook.active.values)
            headers = next(rows)
            return [dict(zip(headers, row)) for row in rows if any(value is not None for value in row)]
        finally:
            workbook.close()

    def _reconcile_tracker(self):
        ledger = self._load()
        changed = False
        for row in self._tracker_rows():
            identity = str(row["id"])
            stage = str(row.get("status", "")).lower()
            if stage not in _HISTORY_STAGES:
                raise ValueError("Unknown authoritative tracker stage requires manual reconciliation")
            record = ledger["jobs"].get(identity)
            if record is None:
                record = {key: row[key] for key in ("company", "role", "url")}
                record.update(id=identity, status=stage)
                ledger["jobs"][identity] = record
                changed = True
            if record.get("state") != stage:
                record["state"] = stage
                record["confirmation"] = row.get("confirmation")
                changed = True
        if changed:
            self._save(ledger)

    def enqueue(self, job):
        if not isinstance(job, dict) or any(
            not isinstance(job.get(key), str) or not job[key] for key in ("id", "company", "role", "url")
        ):
            raise ValueError("A job needs string id, company, role and URL")
        with lock(self.lock_path):
            self._reconcile_tracker()
            ledger = self._load()
            identity = _canonical_url(job["url"])
            for record in ledger["jobs"].values():
                if record["id"] == job["id"] or _canonical_url(record["url"]) == identity:
                    return copy.deepcopy(record)
            record = copy.deepcopy(job)
            record["state"] = job.get("status", "pending")
            ledger["jobs"][job["id"]] = record
            self._save(ledger)
            return copy.deepcopy(record)

    def jobs(self):
        with lock(self.lock_path):
            return copy.deepcopy(list(self._load()["jobs"].values()))

    def get(self, job_id):
        with lock(self.lock_path):
            return copy.deepcopy(self._load()["jobs"][str(job_id)])

    def update(self, job_id, **fields):
        with lock(self.lock_path):
            ledger = self._load()
            record = ledger["jobs"][str(job_id)]
            record.update(copy.deepcopy(fields))
            self._save(ledger)
            return copy.deepcopy(record)

    def claim(self, job_id, lane="1"):
        with lock(self.lock_path):
            self._reconcile_tracker()
            ledger = self._load()
            record = ledger["jobs"][str(job_id)]
            if record["state"] != "pending":
                return False
            record.update(state="claimed", lane=str(lane))
            self._save(ledger)
            return True

    def record_submit_intent(self, job_id, evidence):
        with lock(self.lock_path):
            ledger = self._load()
            record = ledger["jobs"][str(job_id)]
            if record["state"] not in ("claimed", "reviewed"):
                raise ValueError("Submission intent requires a claimed or reviewed job")
            if not isinstance(evidence, dict) or not evidence.get("review_id"):
                raise ValueError("Submission intent needs review evidence")
            record.update(state="submit_intent", submit_intent=copy.deepcopy(evidence))
            self._save(ledger)

    def mark_submitted(self, job_id, evidence):
        with lock(self.lock_path):
            ledger = self._load()
            record = ledger["jobs"][str(job_id)]
            if (
                not isinstance(evidence, dict)
                or evidence.get("status") != "confirmed"
                or evidence.get("job_id") != job_id
            ):
                raise ValueError("Matched confirmation evidence is required")
            email = evidence.get("channel") == "gmail" and evidence.get("message_id") and evidence.get("account")
            if not evidence.get("text") or not evidence.get("url"):
                raise ValueError("Confirmation needs a URL and message")
            if urlsplit(evidence["url"]).netloc != urlsplit(record["url"]).netloc:
                raise ValueError("Confirmation URL belongs to another site")
            if record["state"] not in ("submit_intent", "submitted", "needs_reconciliation") and not email:
                raise ValueError("No durable submission intent exists")
            rows = self._tracker_rows()
            if not any(str(row["id"]) == str(job_id) for row in rows):
                workbook = load_workbook(self.tracker)
                row = {
                    "id": str(job_id),
                    "company": record["company"],
                    "role": record["role"],
                    "url": record["url"],
                    "status": "submitted",
                    "confirmation": evidence["text"],
                    "updated_at": evidence.get("confirmed_at"),
                }
                workbook.active.append([_safe_cell(row.get(column)) for column in _COLUMNS])
                self._save_workbook(workbook)
            record.update(state="submitted", confirmation=copy.deepcopy(evidence))
            self._save(ledger)

    def tracker_rows(self):
        with lock(self.lock_path):
            return self._tracker_rows()

    def has_tracker_job(self, job_id):
        return any(str(row["id"]) == str(job_id) for row in self.tracker_rows())

    def update_tracker_status(self, job_id, status, updated_at=None):
        if status not in ("rejected", "assessment", "interview", "offer"):
            raise ValueError("Unsupported application status")
        with lock(self.lock_path):
            workbook = load_workbook(self.tracker)
            columns = {cell.value: cell.column for cell in workbook.active[1]}
            found = False
            for row in workbook.active.iter_rows(min_row=2):
                if str(row[columns["id"] - 1].value) == str(job_id):
                    row[columns["status"] - 1].value = status
                    row[columns["updated_at"] - 1].value = updated_at
                    found = True
                    break
            if not found:
                workbook.close()
                raise ValueError("Mail cannot create a previously unmatched application")
            self._save_workbook(workbook)

    def mail_events(self):
        with lock(self.lock_path):
            return copy.deepcopy(self._load()["mail_events"])

    def record_mail_event(self, event):
        with lock(self.lock_path):
            ledger = self._load()
            identity = (event.get("account"), event.get("message_id"))
            if any((item.get("account"), item.get("message_id")) == identity for item in ledger["mail_events"]):
                return False
            ledger["mail_events"].append(copy.deepcopy(event))
            self._save(ledger)
            return True

    def preview_history(self, source):
        source = Path(source).resolve()
        if source.suffix.lower() == ".csv":
            with source.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
        elif source.suffix.lower() == ".xlsx":
            workbook = load_workbook(source, read_only=True, data_only=False)
            try:
                values = iter(workbook.active.values)
                headers = next(values)
                rows = [dict(zip(headers, row)) for row in values]
            finally:
                workbook.close()
        else:
            raise ValueError("History supports CSV or XLSX")
        for row in rows:
            if not all(row.get(key) for key in ("company", "role", "url")):
                raise ValueError("History needs company, role and URL columns")
            row["id"] = str(row.get("id") or hashlib.sha256(_canonical_url(row["url"]).encode()).hexdigest()[:24])
            stage = str(row.get("status") or "submitted").lower().strip()
            stage = {"applied": "submitted"}.get(stage, stage)
            if stage not in _HISTORY_STAGES:
                raise ValueError("Unknown history stage; explicitly resolve it before importing")
        return {"source": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "rows": rows}

    def import_history(self, preview, confirm=False):
        source = Path(preview["source"])
        current = self.preview_history(source)
        if current != preview:
            raise ValueError("History changed; preview again")
        if not confirm:
            return {"applied": False, "count": len(preview["rows"])}
        for row in preview["rows"]:
            record = self.enqueue({**row, "status": "pending"})
            identity = record["id"]
            stage = str(row.get("status") or "submitted").lower().strip()
            stage = {"applied": "submitted"}.get(stage, stage)
            if self.has_tracker_job(identity):
                continue
            self.update(identity, state="claimed")
            self.record_submit_intent(identity, {"review_id": "user-confirmed-history-import"})
            self.mark_submitted(
                identity,
                {
                    "status": "confirmed",
                    "job_id": identity,
                    "url": row["url"],
                    "text": "User confirmed history import",
                },
            )
            if stage != "submitted":
                self.update_tracker_status(identity, stage)
            self.update(identity, state=stage)
        return {"applied": True, "count": len(preview["rows"])}
