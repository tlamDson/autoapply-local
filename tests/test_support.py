"""Synthetic fixtures shared by the public release's offline contract tests.

Giả định của người thực thi: use a flat autoapply_local package, JSON records,
Path user roots and injectable engine/browser/transport objects. These tests
specify public behavior; they never generate or patch implementation files.
The plan is deliberately left untouched because this turn permits test files only.
"""

import copy
import importlib
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock


RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))
NOW = "2030-01-10T12:00:00Z"
SCOPE = {
    "country": "US",
    "job_type": "internship",
    "employer": "Example Labs",
    "question": "Authorized at job start?",
    "effective_at": "2030-06-01",
}


def fact(value=None, status="confirmed", scope=None, source_id="intake:1"):
    return {
        "value": value,
        "status": status,
        "provenance": {"source_id": source_id, "kind": "user_confirmation"},
        "confirmed_at": NOW if status == "confirmed" else None,
        "scope": copy.deepcopy(scope or {}),
    }


def candidate(kind="student"):
    """Three independent synthetic identities, with no inferred authorization."""
    data = {
        "schema_version": 1,
        "revision": 1,
        "contact": {"legal_name": fact("Alex Example"), "application_email": fact("alex@example.invalid")},
        "education": [
            {
                "id": "education:1",
                "school": fact("Example University"),
                "degree": fact("BS"),
                "major": fact("Computer Science"),
                "status": fact("current"),
                "graduation": fact({"value": "2031-05", "precision": "month", "kind": "expected"}),
            }
        ],
        "authorization": [],
        "answers": [],
        "targets": {
            "job_types": ["internship"],
            "roles": ["backend"],
            "countries": ["US"],
            "season": "summer",
            "year": 2030,
        },
        "experience": [
            {
                "id": "experience:1",
                "employer": fact("Example Systems"),
                "title": fact("Engineering Intern"),
                "start": fact("2029-06"),
                "end": fact("2029-08"),
                "metrics": [fact("Reduced synthetic benchmark latency by 12%", source_id="experience:1:metric:1")],
            }
        ],
        "skills": [{"name": "Python", "evidence": "project", "source_id": "project:1"}],
        "preferences": {"include_big_tech": True, "eligibility": "broad_unknown", "no_sponsor_conflict": "review"},
    }
    if kind == "student":
        data["authorization"] = [
            {
                "id": "auth:1",
                "status": fact("student"),
                "mechanism": fact("CPT"),
                "mechanism_status": fact("planned"),
                "authorized_now": fact(None, "unknown"),
                "authorized_at_start": fact(None, "unknown"),
            }
        ]
    elif kind == "authorized":
        data["contact"]["legal_name"] = fact("Morgan Example")
        data["authorization"] = [
            {
                "id": "auth:1",
                "authorized_now": fact(True),
                "authorized_at_start": fact(True),
                "sponsorship_now": fact(False),
                "sponsorship_future": fact(False),
            }
        ]
    elif kind == "unknown":
        data["contact"]["legal_name"] = fact("Taylor Example")
        data["authorization"] = [
            {
                "id": "auth:1",
                "status": fact(None, "declined"),
                "authorized_now": fact(None, "unknown"),
                "authorized_at_start": fact(None, "unknown"),
            }
        ]
    else:
        raise ValueError(kind)
    return data


def job(job_id="synthetic-1", ats="greenhouse", **changes):
    data = {
        "id": job_id,
        "ats": ats,
        "company": "Example Labs",
        "role": "Backend Software Engineering Intern",
        "country": "US",
        "location": "Remote, US",
        "level": "internship",
        "season": "summer",
        "year": 2030,
        "url": f"https://jobs.example.invalid/{job_id}",
        "description": "Build Python services.",
        "eligibility": "unknown",
        "status": "pending",
    }
    data.update(changes)
    return data


def snapshot(job_id="synthetic-1", **changes):
    data = {
        "job_id": job_id,
        "tab_id": "tab-1",
        "page_id": "page-1",
        "url": f"https://jobs.example.invalid/{job_id}",
        "fields": [
            {"id": "name", "label": "Legal name", "required": True, "value": "Alex Example", "status": "confirmed"}
        ],
        "errors": [],
        "resume_sha256": None,
    }
    data.update(changes)
    return data


def cleanup_temporary_directory(temporary):
    """Retry temporary Windows sharing violations, with six cleanup calls at most."""
    delays = (0.1, 0.2, 0.4, 0.8, 1.6)
    for attempt in range(len(delays) + 1):
        try:
            temporary.cleanup()
            return
        except PermissionError as error:
            if getattr(error, "winerror", None) != 32 or attempt == len(delays):
                raise
            time.sleep(delays[attempt])


class ReleaseCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="autoapply-synthetic-")
        self.addCleanup(cleanup_temporary_directory, self.temporary)
        self.root = Path(self.temporary.name)

    def module(self, name):
        # Do not turn missing product modules into skips or configuration failures.
        return importlib.import_module(f"autoapply_local.{name}")

    def write_json(self, relative, value):
        import json

        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2), encoding="utf-8")
        return path

    def fake_engine(self):
        engine = Mock(spec=["run"])
        engine.run.return_value = {"status": "completed"}
        return engine
