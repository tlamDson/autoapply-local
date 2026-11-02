"""Run AutoApply Local against this launcher's own runtime directory."""

import sys
from pathlib import Path

from autoapply_local import cli


if __name__ == "__main__":
    raise SystemExit(cli.main(["--user-dir", str(Path(__file__).resolve().parent), *sys.argv[1:]]))
