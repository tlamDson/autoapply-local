"""Skills lines with parenthesised groups (synthetic candidate only).

A group such as "AWS (Lambda, S3)" stays with its parent skill as one name;
a separator inside brackets never splits, and no name has unbalanced brackets.
"""

import unittest

from test_support import ReleaseCase

RESUME = """Alex Rivera
alex.rivera@example.invalid

EDUCATION
Northfield State University                                   Sep 2023 – May 2027

TECHNICAL SKILLS
Languages: Python, SQL (PostgreSQL; SQLite), Java
Cloud: AWS (Lambda, S3, DynamoDB, Bedrock), Docker
Frameworks: React (Hooks, Redux | Context), Flask
Tools: Git, Terraform (modules,
plan, apply), Linux
Data: pandas, NumPy (arrays) (broadcasting), Spark
"""


def balanced(name):
    depth = 0
    for char in name:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth < 0:
            return False
    return depth == 0


class SkillsGroupTests(ReleaseCase):
    def names(self, text=RESUME):
        return [skill["name"] for skill in self.module("resume_facts").parse(text)["skills"]]

    def test_parenthesised_group_stays_with_its_parent(self):
        self.assertEqual(self.names(), [
            "Python", "SQL (PostgreSQL; SQLite)", "Java",
            "AWS (Lambda, S3, DynamoDB, Bedrock)", "Docker",
            "React (Hooks, Redux | Context)", "Flask",
            "Git", "Terraform (modules, plan, apply)", "Linux",
            "pandas", "NumPy (arrays) (broadcasting)", "Spark",
        ])

    def test_no_skill_has_unbalanced_brackets(self):
        for name in self.names():
            with self.subTest(name=name):
                self.assertTrue(balanced(name))

    def test_stray_brackets_are_dropped(self):
        # A closing bracket with no group is dropped; a group never closed by the end of the
        # section loses its opening bracket and splits like plain text.
        text = "Skills\nTools: Git), (Docker, Make (CMake\n"
        self.assertEqual(self.names(text), ["Git", "Docker", "Make CMake"])

    def test_unclosed_group_keeps_its_closed_inner_group(self):
        text = "Skills\nTools: (Docker, Make (CMake)\n"
        self.assertEqual(self.names(text), ["Docker", "Make (CMake)"])

    def test_plain_lines_still_split_on_every_separator(self):
        text = "Skills\nPython, Go; Rust | C • Zig\n"
        self.assertEqual(self.names(text), ["Python", "Go", "Rust", "C", "Zig"])


if __name__ == "__main__":
    unittest.main()
