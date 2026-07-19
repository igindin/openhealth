#!/usr/bin/env python3
"""oh_ship — ship a contribution without knowing git.

For a newcomer who only uses Claude Code / Codex. Given a short description, this
creates a branch, commits the current changes, and (only with --push) opens a PR.
The agent runs this for the person; they never type a git command.

Safety: by default it stays LOCAL (branch + commit). Pushing to the remote
requires --push AND the maintainer having set OPENHEALTH_ALLOW_PUSH=1, so nothing
leaves the machine unless explicitly enabled.

Usage:
    python scripts/oh_ship.py "add resting-hr metric to pulse"
    python scripts/oh_ship.py "add resting-hr metric to pulse" --push
"""

import argparse
import os
import re
import subprocess
import sys


def run(cmd, check=True):
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:40] or "change")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("message", help="Plain description of what you changed.")
    ap.add_argument("--push", action="store_true", help="Also push and open a PR (needs OPENHEALTH_ALLOW_PUSH=1).")
    args = ap.parse_args()

    # Must be inside a git repo.
    if run(["git", "rev-parse", "--is-inside-work-tree"], check=False).returncode != 0:
        print("Not a git repository.", file=sys.stderr)
        return 1

    status = run(["git", "status", "--porcelain"]).stdout.strip()
    if not status:
        print("Nothing to ship — no changes detected.")
        return 0

    branch = "contrib/%s" % slug(args.message)
    # Create/switch to the branch.
    run(["git", "checkout", "-B", branch])
    run(["git", "add", "-A"])
    run(["git", "commit", "-m", args.message])
    print("Committed on branch %s" % branch)

    if not args.push:
        print("Stayed local. To open a PR later, re-run with --push (maintainer must allow it).")
        return 0

    if os.environ.get("OPENHEALTH_ALLOW_PUSH") != "1":
        print("Push is disabled. The maintainer enables it with OPENHEALTH_ALLOW_PUSH=1.")
        return 0

    run(["git", "push", "-u", "origin", branch])
    pr = run(["gh", "pr", "create", "--fill"], check=False)
    print(pr.stdout or pr.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
