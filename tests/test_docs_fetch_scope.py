"""The documents state the v0.2.0a1 ATS scope and the Chrome sync warning (task fix-t24-4).

Job fetch (boards and --url) supports Greenhouse, Workday and Rippling only;
Ashby, Lever and Workable have fill drivers but no job fetch until the next
version. At the Gmail step the user signs in on the web page only and never
turns on Chrome sync in the tool's profile.
"""

import unittest

from test_support import RELEASE

FETCH_SCOPE = {
    "README.md": ("Job fetch (boards and `--url`) supports Greenhouse, Workday and Rippling only",
                  "Ashby, Lever and Workable have fill drivers but no job fetch yet",
                  "next version"),
    "README.vi.md": ("Lấy job (boards và `--url`) chỉ hỗ trợ Greenhouse, Workday và Rippling",
                     "Ashby, Lever và Workable đã có driver điền form nhưng chưa có phần lấy job",
                     "bản tiếp theo"),
    "AGENTS.md": ("Job fetch (boards and `--url`) supports Greenhouse, Workday and Rippling only",),
    "CLAUDE.md": ("Job fetch (boards and `--url`) supports Greenhouse, Workday and Rippling only",),
}
NO_SYNC = {
    "README.md": ("web page only", "do NOT turn on Chrome sync"),
    "README.vi.md": ("trang web", "KHÔNG bật đồng bộ Chrome"),
    "AGENTS.md": ("do NOT turn on Chrome sync",),
    "CLAUDE.md": ("do NOT turn on Chrome sync",),
}


def text(relative):
    return (RELEASE / relative).read_text(encoding="utf-8")


class FetchScopeTests(unittest.TestCase):
    def test_docs_say_which_ats_fetch_supports(self):
        for relative, phrases in FETCH_SCOPE.items():
            for phrase in phrases:
                with self.subTest(document=relative, phrase=phrase):
                    self.assertTrue(phrase in text(relative), f"{relative} lacks: {phrase}")

    def test_docs_warn_against_chrome_sync_in_the_tool_profile(self):
        for relative, phrases in NO_SYNC.items():
            for phrase in phrases:
                with self.subTest(document=relative, phrase=phrase):
                    self.assertTrue(phrase in text(relative), f"{relative} lacks: {phrase}")


if __name__ == "__main__":
    unittest.main()
