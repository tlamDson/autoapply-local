"""AutoApply Local: experimental, local-first job application tools."""

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path


def _selection(value, operation):
    if operation == "apply" and value.lstrip("-").isdigit():
        count = int(value)
        if count < 1:
            raise ValueError("Quick count must be positive")
        return count
    if operation == "review" and value == "all":
        return value
    if operation == "submit" and value == "all-reviewed":
        return value
    if value in ("all", "all-reviewed"):
        raise ValueError("Choose explicit IDs or the supported reviewed selection")
    ids = value.split(",")
    if any(not item.strip() for item in ids):
        raise ValueError("Job IDs cannot be blank")
    return list(dict.fromkeys(item.strip() for item in ids))


def _utf8_process_stream(stream):
    encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "_")
    if hasattr(stream, "reconfigure") and encoding not in ("utf8", "utf_8"):
        stream.reconfigure(encoding="utf-8", errors=stream.errors)
    return stream


def main(argv=None, services=None, stdout=None, stderr=None):
    stdout = _utf8_process_stream(sys.stdout) if stdout is None else stdout
    stderr = _utf8_process_stream(sys.stderr) if stderr is None else stderr
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user-dir", type=Path, default=Path(os.environ.get("AUTOAPPLY_LOCAL_HOME", Path.home() / ".autoapply-local"))
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup")
    boards = commands.add_parser("boards").add_subparsers(dest="boards_command", required=True)
    boards.add_parser("list")
    boards_import = boards.add_parser("import")
    boards_import.add_argument("path", type=Path)
    boards_import.add_argument("--confirm", action="store_true")
    profile = commands.add_parser("profile").add_subparsers(dest="profile_command", required=True)
    update = profile.add_parser("update")
    update.add_argument("path", type=Path)
    update.add_argument("--confirm", action="store_true")
    source = commands.add_parser("source").add_subparsers(dest="source_command", required=True)
    source.add_parser("extract").add_argument("path", type=Path)
    history = commands.add_parser("history").add_subparsers(dest="history_command", required=True)
    history_import = history.add_parser("import")
    history_import.add_argument("path", type=Path)
    history_import.add_argument("--confirm", action="store_true")
    for name in ("doctor", "fetch", "apply", "mail-check"):
        command = commands.add_parser(name)
        command.add_argument("engine", nargs="?", choices=("codex", "claude"))
        if name == "apply":
            command.add_argument("--quick", required=True)
        if name == "fetch":
            command.add_argument("--url")
    tailor = commands.add_parser("tailor")
    tailor.add_argument("operands", nargs="+")
    for name in ("review", "submit"):
        commands.add_parser(name).add_argument("selection")
    commands.add_parser("status")
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                args = parser.parse_args(argv)
            except SystemExit as error:
                return error.code
        name = args.command.replace("-", "_")
        kwargs = {"root": args.user_dir.expanduser().resolve()}
        if name == "boards":
            name = "boards_" + args.boards_command
            if args.boards_command == "import":
                kwargs.update(path=args.path, confirm=args.confirm)
        elif name == "profile":
            name = "profile_update"
            kwargs.update(path=args.path, confirm=args.confirm)
        elif name == "source":
            name = "source_extract"
            kwargs["path"] = args.path
        elif name == "history":
            name = "history_import"
            kwargs.update(path=args.path, confirm=args.confirm)
        elif name in ("doctor", "fetch", "apply", "mail_check"):
            kwargs["engine"] = args.engine
            if name == "apply":
                kwargs["selection"] = _selection(args.quick, "apply")
            if name == "fetch" and args.url:
                kwargs["url"] = args.url
        elif name in ("review", "submit"):
            kwargs["selection"] = _selection(args.selection, name)
        elif name == "tailor":
            operands = args.operands
            engine = operands.pop(0) if operands[0] in ("codex", "claude") else None
            if len(operands) != 1:
                raise ValueError("tailor requires one JD file or URL and an optional engine")
            kwargs.update(engine=engine, jd=operands[0])
        if services is None:
            from .runtime import services as default_services

            services = default_services()
        with contextlib.redirect_stderr(stderr):
            result = services[name](**kwargs)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str), file=stdout)
        return 0
    except (ValueError, KeyError) as error:
        print(str(error), file=stderr)
        return 2
    except (OSError, RuntimeError) as error:
        print(str(error), file=stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
