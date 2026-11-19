"""Shipped public board seeds and their on/off groups (plan aa-09, flow 3.3 step 4).

The seeds are public ATS boards of S&P 500 companies and unicorns: they hold no
personal data. Boards the user adds carry no group and are always fetched.
All configs below are synthetic; no network is used.
"""

import io
import json
import re
from importlib.resources import files
from unittest.mock import Mock

from test_support import RELEASE, ReleaseCase

GROUPS = ("sp500", "unicorn")


def shipped():
    return json.loads(files("autoapply_local").joinpath("data/boards.json").read_text(encoding="utf-8"))


class BoardSeedTests(ReleaseCase):
    def config(self, boards, **extra):
        self.write_json("config.json", {"runtime": {}, "resources": {"lanes": 1}, "boards": boards, **extra})

    def read_config(self):
        return json.loads((self.root / "config.json").read_text(encoding="utf-8"))

    def test_seeds_are_valid_public_boards_tagged_with_a_known_group(self):
        seeds = shipped()
        boards = self.module("boards")
        self.assertEqual(boards.normalize(seeds), seeds)
        for group in GROUPS:
            with self.subTest(group=group):
                self.assertGreaterEqual(sum(seed.get("group") == group for seed in seeds), 10)
        self.assertTrue(all(seed["group"] in GROUPS and seed["company"] for seed in seeds))
        self.assertEqual(len({boards.identity(seed) for seed in seeds}), len(seeds))
        companies = [seed["company"].casefold() for seed in seeds]
        self.assertEqual(len(set(companies)), len(companies), "one board per company")

    def test_group_must_be_a_short_lowercase_name(self):
        boards = self.module("boards")
        good = {"ats": "greenhouse", "board": "examplelabs", "company": "Example Labs", "group": "my-list"}
        self.assertEqual(boards.normalize([good]), [good])
        for bad in ("Not Valid", "x" * 41, "a/b"):
            with self.subTest(group=bad), self.assertRaises(ValueError):
                boards.normalize([{**good, "group": bad}])

    def test_fresh_setup_installs_every_seed_with_all_groups_on(self):
        self.module("runtime").setup(root=self.root)
        config = self.read_config()
        self.assertEqual(config["boards"], shipped())
        listed = self.module("boards").groups(self.root)
        self.assertEqual([item["group"] for item in listed], list(GROUPS))
        self.assertTrue(all(item["enabled"] for item in listed))
        self.assertEqual(sum(item["boards"] for item in listed), len(shipped()))

    def test_turning_a_group_off_skips_its_boards_but_keeps_user_boards(self):
        boards = self.module("boards")
        mine = {"ats": "greenhouse", "board": "examplelabs", "company": "Example Labs"}
        sp = {"ats": "greenhouse", "board": "examplecorp", "company": "Example Corp", "group": "sp500"}
        uni = {"ats": "rippling", "board": "exampleai", "company": "Example AI", "group": "unicorn"}
        self.config([sp, uni, mine], custom={"keep": True})
        result = boards.set_group(self.root, "sp500", False)
        self.assertEqual(result["group"], "sp500")
        self.assertIs(result["enabled"], False)
        self.assertEqual(boards.active_boards(self.root), [uni, mine])
        config = self.read_config()
        self.assertEqual(config["board_groups"], {"sp500": False})
        self.assertEqual(config["custom"], {"keep": True})
        self.assertEqual(config["boards"], [sp, uni, mine], "turning a group off never deletes boards")
        boards.set_group(self.root, "sp500", True)
        self.assertEqual(boards.active_boards(self.root), [sp, uni, mine])
        self.assertEqual(
            {item["group"]: item["enabled"] for item in boards.groups(self.root)}, {"sp500": True, "unicorn": True}
        )

    def test_unknown_group_is_refused_without_writing(self):
        boards = self.module("boards")
        self.config([{"ats": "greenhouse", "board": "examplecorp", "company": "Example Corp", "group": "sp500"}])
        before = (self.root / "config.json").read_bytes()
        with self.assertRaises(ValueError) as caught:
            boards.set_group(self.root, "fortune", False)
        self.assertIn("sp500", str(caught.exception))
        self.assertEqual((self.root / "config.json").read_bytes(), before)


class BoardGroupCliTests(ReleaseCase):
    def invoke(self, *argv):
        self.services = {name: Mock(return_value={"status": "ok"}) for name in ("boards_groups", "boards_group")}
        out, err = io.StringIO(), io.StringIO()
        code = self.module("cli").main(
            ["--user-dir", str(self.root), *argv], services=self.services, stdout=out, stderr=err
        )
        return code, err.getvalue()

    def test_boards_groups_lists_groups(self):
        self.assertEqual(self.invoke("boards", "groups")[0], 0)
        self.services["boards_groups"].assert_called_once_with(root=self.root)
        self.services["boards_group"].assert_not_called()

    def test_boards_group_on_off_forwards_a_bool(self):
        for word, enabled in (("on", True), ("off", False)):
            with self.subTest(word=word):
                self.assertEqual(self.invoke("boards", "group", "unicorn", word)[0], 0)
                self.services["boards_group"].assert_called_once_with(root=self.root, group="unicorn", enabled=enabled)

    def test_boards_group_needs_on_or_off(self):
        code, _ = self.invoke("boards", "group", "unicorn", "maybe")
        self.assertEqual(code, 2)
        self.services["boards_group"].assert_not_called()

    def test_runtime_exposes_group_services(self):
        services = self.module("runtime").services()
        for name in ("boards_groups", "boards_group", "demo"):
            with self.subTest(name=name):
                self.assertTrue(callable(services[name]))


class ImportedBoardGroupTests(ReleaseCase):
    def test_imported_boards_never_join_a_seed_group(self):
        boards = self.module("boards")
        seed = {"ats": "greenhouse", "board": "examplecorp", "company": "Example Corp", "group": "sp500"}
        other = {"ats": "greenhouse", "board": "otherco", "company": "Other Co", "group": "sp500"}
        self.write_json("config.json", {"runtime": {}, "resources": {"lanes": 1}, "boards": [seed, other]})
        mine = [
            {"ats": "greenhouse", "board": "examplelabs", "company": "Example Labs", "group": "sp500"},
            {"ats": "greenhouse", "board": "examplecorp", "company": "Example Corp"},
        ]
        path = self.write_json("mine.json", mine)
        preview = boards.preview_import(self.root, path)
        boards.apply_import(self.root, preview, confirm=True)
        boards.set_group(self.root, "sp500", False)
        active = {board["board"] for board in boards.active_boards(self.root)}
        self.assertEqual(active, {"examplelabs", "examplecorp"})
        self.assertTrue(all("group" not in board for board in boards.active_boards(self.root)))


SEED_DOCS = (
    ".claude/commands/setup.md",
    ".claude/commands/fetch.md",
    ".codex/skills/autoapply-local/SKILL.md",
    "autoapply_local/data/bootstrap/command-setup.md",
    "autoapply_local/data/bootstrap/command-fetch.md",
    "autoapply_local/data/bootstrap/skill.md",
)
GROUP_LABELS = {"sp500": "S&P 500 companies", "unicorn": "unicorns"}
ATS_LABELS = {"greenhouse": "Greenhouse", "workday": "Workday", "rippling": "Rippling",
              "ashby": "Ashby", "lever": "Lever", "workable": "Workable"}


def seed_sentence(seeds):
    """The one description of the seed groups every doc copy carries, derived from boards.json."""
    order = {}
    for seed in seeds:
        order.setdefault(seed["group"], [])
        if seed["ats"] not in order[seed["group"]]:
            order[seed["group"]].append(seed["ats"])
    parts = [
        f"`{group}` ({GROUP_LABELS[group]}: {' and '.join(ATS_LABELS[ats] for ats in ats_list)})"
        for group, ats_list in order.items()
    ]
    return (f"Discovery seeds are the shipped public boards in {len(parts)} groups, all on by default: "
            + " and ".join(parts) + ".")


def unknown_seed_boards(text, seeds):
    """Every Company/ATS pair in text that is not a shipped seed board."""
    known = {(seed["company"], ATS_LABELS[seed["ats"]]) for seed in seeds}
    pair = re.compile(r"([A-Z][A-Za-z0-9&.\- ]*?)/(" + "|".join(ATS_LABELS.values()) + r")(?![a-z])")
    unknown = []
    for words, ats in pair.findall(text):
        words = words.split()
        if not any((" ".join(words[i:]), ats) in known for i in range(len(words))):
            unknown.append(f"{words[-1]}/{ats}")
    return unknown


class SeedDocTests(ReleaseCase):
    def text(self, relative):
        return " ".join((RELEASE / relative).read_text(encoding="utf-8").split())

    def test_every_doc_describes_the_seed_groups_boards_json_defines(self):
        sentence = seed_sentence(shipped())
        for relative in SEED_DOCS:
            with self.subTest(doc=relative):
                text = self.text(relative)
                self.assertIn(sentence, text)
                self.assertIn("`boards group NAME on|off`", text)

    def test_a_doc_names_only_seed_boards_boards_json_has(self):
        seeds = shipped()
        self.assertEqual(unknown_seed_boards("Seeds are NVIDIA/Workday and Capital One/Workday.", seeds), [])
        self.assertEqual(unknown_seed_boards("Seeds are Acme Robotics/Greenhouse.", seeds), ["Robotics/Greenhouse"])
        for relative in SEED_DOCS:
            with self.subTest(doc=relative):
                self.assertEqual(unknown_seed_boards(self.text(relative), seeds), [])
