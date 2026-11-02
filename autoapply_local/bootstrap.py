"""Install packaged runtime instructions without replacing user-owned files."""

from importlib.resources import files
from pathlib import Path


COMMANDS = (
    "setup",
    "doctor",
    "profile-update",
    "fetch",
    "apply",
    "tailor",
    "review",
    "submit",
    "mail-check",
    "status",
)


def install(root):
    root = Path(root)
    data = files("autoapply_local").joinpath("data")
    templates = data.joinpath("bootstrap")
    assets = {f".claude/commands/{name}.md": templates.joinpath(f"command-{name}.md") for name in COMMANDS}
    assets.update(
        {
            ".codex/skills/autoapply-local/SKILL.md": templates.joinpath("skill.md"),
            "AGENTS.md": templates.joinpath("AGENTS.md"),
            "README.md": templates.joinpath("README.md"),
            "autoapply-local.py": templates.joinpath("launcher.py"),
            "examples/boards.json": data.joinpath("boards.json"),
        }
    )
    for relative, resource in assets.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(resource.read_bytes())
        except FileExistsError:
            pass
