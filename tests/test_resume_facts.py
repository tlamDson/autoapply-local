"""Resume text to grouped, unconfirmed draft facts (synthetic candidate only)."""

import unittest

from test_support import ReleaseCase

RESUME = """Alex Rivera
alex.rivera@example.invalid | (555) 010-2030 | linkedin.com/in/alex-rivera-example | github.com/alexrivera-example

EDUCATION
Northfield State University                                   Sep 2023 – May 2027
Bachelor of Science in Computer Science                       Expected May 2027
GPA: 3.8/4.0

EXPERIENCE
Example Labs                                                  Jun 2025 – Aug 2025
Software Engineering Intern                                   Remote
• Built a caching layer for an internal API
• Wrote integration tests

Campus Tools Office                                           Jan 2024 – Present
Student Developer                                             Springfield, IL
- Maintained a ticket dashboard

PROJECTS
Trail Planner
• Route planner in Python

TECHNICAL SKILLS
Languages: Python, Java, TypeScript
Tools: Git, Docker, Python
"""

SECOND = """Alex Rivera
alex.rivera@example.invalid | https://alexrivera.example.invalid

Education
Northfield State University                                   Sep 2023 – May 2027
Bachelor of Science in Computer Science

Skills
SQL; Go
"""


class ResumeFactsTests(ReleaseCase):
    def parse(self, text=RESUME):
        return self.module("resume_facts").parse(text)

    def test_contact_group(self):
        self.assertEqual(self.parse()["contact"], {
            "legal_name": "Alex Rivera",
            "application_email": "alex.rivera@example.invalid",
            "phone": "(555) 010-2030",
            "linkedin": "https://linkedin.com/in/alex-rivera-example",
            "github": "https://github.com/alexrivera-example",
        })

    def test_education_group(self):
        education = self.parse()["education"]
        self.assertEqual(education["records"], [{
            "school": "Northfield State University",
            "degree": "Bachelor of Science",
            "major": "Computer Science",
            "start": "2023-09",
            "end": "2027-05",
            "graduation": {"value": "2027-05", "precision": "month", "kind": "expected"},
        }])
        self.assertEqual((education["gpa"], education["gpa_scale"]), (3.8, 4.0))

    def test_experience_group(self):
        self.assertEqual(self.parse()["experience"], [
            {"employer": "Example Labs", "title": "Software Engineering Intern", "location": "Remote",
             "start": "2025-06", "end": "2025-08",
             "description": "Built a caching layer for an internal API; Wrote integration tests"},
            {"employer": "Campus Tools Office", "title": "Student Developer", "location": "Springfield, IL",
             "start": "2024-01", "end": "present", "description": "Maintained a ticket dashboard"},
        ])

    def test_skills_group_is_deduplicated_and_ignores_projects(self):
        self.assertEqual(self.parse()["skills"], [{"name": name} for name in
                                                  ("Python", "Java", "TypeScript", "Git", "Docker")])

    def test_graduation_without_expected_word_is_not_inferred(self):
        record = self.parse(SECOND)["education"]["records"][0]
        self.assertNotIn("graduation", record)

    def test_merge_keeps_first_values_and_unions_records(self):
        facts = self.module("resume_facts")
        merged = facts.merge([facts.parse(RESUME), facts.parse(SECOND)])
        self.assertEqual(merged["contact"]["portfolio"], "https://alexrivera.example.invalid")
        self.assertEqual(merged["contact"]["phone"], "(555) 010-2030")
        self.assertEqual(len(merged["education"]["records"]), 1)
        self.assertEqual([s["name"] for s in merged["skills"]][-2:], ["SQL", "Go"])

    def test_empty_text_gives_empty_groups(self):
        self.assertEqual(self.parse(""), {"contact": {}, "education": {"records": []}, "experience": [], "skills": []})

    def test_from_config_reads_main_and_alternative_resumes(self):
        main = self.root / "resumes/main.txt"
        main.parent.mkdir(parents=True)
        main.write_text(RESUME, encoding="utf-8")
        other = self.root / "resumes/other.txt"
        other.write_text(SECOND, encoding="utf-8")
        config = {"resumes": {"main_pdf": str(main), "alternatives": [{"id": "other", "path": str(other), "tags": []}]}}
        facts = self.module("resume_facts").from_config(config)
        self.assertEqual(facts["contact"]["portfolio"], "https://alexrivera.example.invalid")
        self.assertEqual(facts["contact"]["legal_name"], "Alex Rivera")

    def test_from_config_skips_unreadable_resumes(self):
        config = {"resumes": {"main_pdf": str(self.root / "missing.pdf")}}
        self.assertEqual(self.module("resume_facts").from_config(config)["contact"], {})
        broken = self.root / "broken.pdf"
        broken.write_bytes(b"%PDF-1.4 synthetic")
        extractor = lambda path: (_ for _ in ()).throw(ValueError("no text"))
        self.assertEqual(self.module("resume_facts").from_config({"resumes": {"main_pdf": str(broken)}},
                                                                 extractor=extractor)["skills"], [])


if __name__ == "__main__":
    unittest.main()
