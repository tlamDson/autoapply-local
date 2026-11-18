"""Experience records from a two-column resume PDF (synthetic candidate and employers only).

The layout puts a technology list where a location usually sits, and wraps
bullets over several PDF lines. A tech list is never a location, and a bullet
keeps its continuation lines.
"""

import shutil
import unittest

from test_support import ReleaseCase

# (x, y, text) in points on a US Letter page; text is encoded as WinAnsi (cp1252).
LINES = [
    (50, 740, "Alex Rivera"),
    (50, 726, "alex.rivera@example.invalid | (555) 010-2030"),
    (50, 700, "EXPERIENCE"),
    (50, 684, "Northwind Robotics"),
    (470, 684, "Jun 2025 – Aug 2025"),
    (50, 671, "Software Engineer Intern"),
    (300, 671, "Python, FastAPI, PostgreSQL, Docker, GitHub Actions, Kafka"),
    (60, 657, "• Cut tenant onboarding time in half by moving from single-tenant to multi-tenant,"),
    (68, 645, "which removed a manual deploy step for every new customer."),
    (60, 631, "• Added contract tests for the billing API."),
    (50, 611, "Bluefin Data Co"),
    (470, 611, "Jan 2024 – May 2025"),
    (50, 598, "Data Engineering Intern"),
    (300, 598, "Supabase, pgvector, Redis, OpenAI, LlamaParse, TypeScript, React"),
    (60, 584, "• Built a document search service that answers questions over uploaded files,"),
    (68, 572, "returning cited passages for each answer"),
    (68, 560, "within two seconds."),
    (50, 540, "Harbor Lane Studio"),
    (470, 540, "Jun 2023 – Aug 2023"),
    (50, 527, "Web Developer Intern"),
    (470, 527, "Austin, TX"),
    (60, 513, "• Shipped a booking page used by local clients."),
    (50, 490, "TECHNICAL SKILLS"),
    (50, 476, "Languages: Python, TypeScript"),
]

# What `pdftotext -enc UTF-8 -layout` prints for LINES (recorded, so CI without poppler still runs it).
LAYOUT_TEXT = """Alex Rivera
alex.rivera@example.invalid | (555) 010-2030

EXPERIENCE

Northwind Robotics                                                                  Jun 2025 – Aug 2025

Software Engineer Intern                            Python, FastAPI, PostgreSQL, Docker, GitHub Actions, Kafka

• Cut tenant onboarding time in half by moving from single-tenant to multi-tenant,
  which removed a manual deploy step for every new customer.

• Added contract tests for the billing API.

Bluefin Data Co                                                                     Jan 2024 – May 2025

Data Engineering Intern                             Supabase, pgvector, Redis, OpenAI, LlamaParse, TypeScript, React

• Built a document search service that answers questions over uploaded files,
  returning cited passages for each answer
  within two seconds.

Harbor Lane Studio                                                                  Jun 2023 – Aug 2023
Web Developer Intern                                                                Austin, TX

   • Shipped a booking page used by local clients.

TECHNICAL SKILLS
Languages: Python, TypeScript
"""

EXPECTED = [
    {"employer": "Northwind Robotics", "title": "Software Engineer Intern", "start": "2025-06", "end": "2025-08",
     "description": "Cut tenant onboarding time in half by moving from single-tenant to multi-tenant, "
                    "which removed a manual deploy step for every new customer.; "
                    "Added contract tests for the billing API."},
    {"employer": "Bluefin Data Co", "title": "Data Engineering Intern", "start": "2024-01", "end": "2025-05",
     "description": "Built a document search service that answers questions over uploaded files, "
                    "returning cited passages for each answer within two seconds."},
    {"employer": "Harbor Lane Studio", "title": "Web Developer Intern", "location": "Austin, TX",
     "start": "2023-06", "end": "2023-08", "description": "Shipped a booking page used by local clients."},
]


def _escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_resume_pdf(path):
    """Write a one-page text PDF with Helvetica (WinAnsi) at the given positions."""
    stream = "\n".join(f"BT /F1 10 Tf {x} {y} Td ({_escape(text)}) Tj ET" for x, y, text in LINES).encode("cp1252")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    body, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, content in enumerate(objects, 1):
        offsets.append(len(body))
        body += b"%d 0 obj\n" % number + content + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    body += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    body += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(body))


class ExperienceLayoutTests(ReleaseCase):
    def experience(self, text):
        return self.module("resume_facts").parse(text)["experience"]

    def test_tech_list_in_location_column_is_not_a_location(self):
        records = self.experience(LAYOUT_TEXT)
        self.assertEqual([r.get("location") for r in records], [None, None, "Austin, TX"])

    def test_bullet_continuation_lines_are_joined(self):
        records = self.experience(LAYOUT_TEXT)
        self.assertEqual([r["description"] for r in records], [r["description"] for r in EXPECTED])

    def test_recorded_layout_text_gives_expected_records(self):
        self.assertEqual(self.experience(LAYOUT_TEXT), EXPECTED)

    def test_tech_list_alone_under_the_heading_is_not_the_title(self):
        text = ("EXPERIENCE\nNorthwind Robotics                    Jun 2025 – Aug 2025\n"
                "Python, FastAPI, PostgreSQL, Docker\n"
                "Software Engineer Intern                  Remote\n"
                "• Built a queue worker\n")
        self.assertEqual(self.experience(text), [
            {"employer": "Northwind Robotics", "title": "Software Engineer Intern", "location": "Remote",
             "start": "2025-06", "end": "2025-08", "description": "Built a queue worker"}])

    def test_place_columns_stay_locations(self):
        for place in ("Remote", "Springfield, IL", "Hybrid - Austin, TX", "Toronto, ON, Canada", "Hanoi, Vietnam",
                      "Seattle, Washington"):
            with self.subTest(place=place):
                text = f"EXPERIENCE\nNorthwind Robotics     Jun 2025 – Aug 2025\nIntern     {place}\n• Did work\n"
                self.assertEqual(self.experience(text)[0].get("location"), place)

    def test_non_place_columns_leave_location_empty(self):
        for column in ("Python, Django", "React, Node.js, AWS", "C++, CUDA", "Part-time"):
            with self.subTest(column=column):
                text = f"EXPERIENCE\nNorthwind Robotics     Jun 2025 – Aug 2025\nIntern     {column}\n• Did work\n"
                record = self.experience(text)[0]
                self.assertNotIn("location", record)
                self.assertEqual(record["title"], "Intern")

    def test_next_record_heading_is_not_glued_to_a_bullet(self):
        text = ("EXPERIENCE\nNorthwind Robotics     Jun 2025 – Aug 2025\nIntern     Remote\n"
                "• First line of work\n  second line of work\n"
                "Bluefin Data Co     Jan 2024 – May 2025\nAnalyst     Austin, TX\n• Other work\n")
        records = self.experience(text)
        self.assertEqual([r["description"] for r in records], ["First line of work second line of work", "Other work"])
        self.assertEqual(records[1]["employer"], "Bluefin Data Co")

    @unittest.skipUnless(shutil.which("pdftotext"), "needs poppler pdftotext; the recorded layout text covers CI")
    def test_synthetic_pdf_through_pdftotext(self):
        path = self.root / "resume.pdf"
        write_resume_pdf(path)
        text = self.module("extraction").extract_source(path)["text"]
        self.assertEqual(self.experience(text), EXPECTED)


if __name__ == "__main__":
    unittest.main()
