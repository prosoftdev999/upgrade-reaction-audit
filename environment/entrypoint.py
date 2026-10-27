#!/usr/bin/env python3
"""Lock grader-only log namespaces, then run the agent container as uid/gid 1000."""

import os
from pathlib import Path
import stat
import sys

PROTECTED_LOG_DIRS = (
    Path('/logs/verifier'),
    Path('/logs/artifacts'),
)


def prepare_logs() -> None:
    logs = Path('/logs')
    logs.mkdir(parents=True, exist_ok=True)

    # Keep /logs available for the platform's agent/session logs, but prevent an
    # unprivileged process from replacing grader-owned child directories.
    os.chown(logs, 0, 0)
    os.chmod(logs, 0o1777)

    for path in PROTECTED_LOG_DIRS:
        if path.is_symlink():
            path.unlink()
        elif path.exists() and not stat.S_ISDIR(path.stat().st_mode):
            path.unlink()

        path.mkdir(parents=False, exist_ok=True)
        os.chown(path, 0, 0)
        os.chmod(path, 0o555)


def drop_privileges_and_exec(argv: list[str]) -> None:
    if not argv:
        raise SystemExit('entrypoint requires a command')

    os.setgroups([])
    os.setgid(1000)
    os.setuid(1000)
    os.umask(0o022)
    os.execvp(argv[0], argv)


def main() -> None:
    prepare_logs()
    drop_privileges_and_exec(sys.argv[1:])


if __name__ == '__main__':
    main()
