"""Set up or use the runtime next to this source checkout."""

import os
import sys
from pathlib import Path

from autoapply_local import cli


if __name__ == "__main__":
    arguments = sys.argv[1:] or ["setup"]
    if "AUTOAPPLY_LOCAL_HOME" not in os.environ and not any(
        argument == "--user-dir" or argument.startswith("--user-dir=") for argument in arguments
    ):
        arguments = ["--user-dir", str(Path(__file__).resolve().parent / ".autoapply-local"), *arguments]
    raise SystemExit(cli.main(arguments))
