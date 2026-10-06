#!/usr/bin/env python3
"""Check that every skill here is byte-identical to the parent catalog.

This repo is a subset of rampstackco/claude-skills, and its skills are meant
to be copies. Nothing told this repo when the parent changed, so it drifted
for months unnoticed. This check compares the git blob SHA of every SKILL.md
and every file under references/ against the parent's main branch, in both
directions: a file that differs, a file missing here, and a file missing in
the parent all fail, one line per file.

Per-skill README.md files are out of scope on purpose. The parent generates
them from openaddict.com and checks them there; they are not copied here.

The parent is read with one public, unauthenticated call to resolve main to
a commit and one for that commit's tree. No token is used: the parent is
public, and the result should not depend on who runs it.

Usage:
  python tools/check_parent_parity.py

Exit codes:
  0  Every file matches the parent.
  1  One or more files differ, are missing, or are extra.
  2  The parent could not be read (network, rate limit, truncated tree).
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

PARENT_REPO = "rampstackco/claude-skills"
PARENT_REF = "main"
ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"
API = "https://api.github.com"
USER_AGENT = f"{ROOT.name} parent-parity check"


def get_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def in_scope(rel: str) -> bool:
    """SKILL.md and everything under references/, relative to a skill folder."""
    return rel == "SKILL.md" or rel.startswith("references/")


def blob_sha(path: Path) -> str:
    """The git blob SHA of a working-tree file, as git would store it.

    .gitattributes keeps the working tree LF on every platform; CRLF is still
    folded here so a checkout made before that policy cannot false-fail.
    """
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def local_files() -> dict[str, str]:
    files = {}
    for skill_dir in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        for f in sorted(skill_dir.rglob("*")):
            rel = f.relative_to(skill_dir).as_posix()
            if f.is_file() and in_scope(rel):
                files[f"{skill_dir.name}/{rel}"] = blob_sha(f)
    return files


def parent_files(skill_names: set[str]) -> tuple[str, dict[str, str]]:
    commit = get_json(f"{API}/repos/{PARENT_REPO}/commits/{PARENT_REF}")["sha"]
    tree = get_json(f"{API}/repos/{PARENT_REPO}/git/trees/{commit}?recursive=1")
    if tree.get("truncated"):
        raise RuntimeError(
            f"{PARENT_REPO}@{commit} tree came back truncated; "
            "refusing to compare against a partial file list"
        )
    files = {}
    for entry in tree["tree"]:
        if entry["type"] != "blob" or not entry["path"].startswith("skills/"):
            continue
        parts = entry["path"].split("/", 2)
        if len(parts) == 3 and parts[1] in skill_names and in_scope(parts[2]):
            files[f"{parts[1]}/{parts[2]}"] = entry["sha"]
    return commit, files


def main() -> int:
    local = local_files()
    skill_names = {key.split("/", 1)[0] for key in local}
    try:
        commit, parent = parent_files(skill_names)
    except (urllib.error.URLError, RuntimeError, KeyError) as error:
        print(f"Could not read {PARENT_REPO}@{PARENT_REF}: {error}")
        return 2

    problems = []
    for key in sorted(set(local) | set(parent)):
        if key not in parent:
            problems.append(f"  only here, not in parent:  skills/{key}")
        elif key not in local:
            problems.append(f"  in parent, missing here:   skills/{key}")
        elif local[key] != parent[key]:
            problems.append(f"  differs from parent:       skills/{key}")

    print(f"Compared {len(local)} files in {len(skill_names)} skills "
          f"against {PARENT_REPO}@{commit} ({PARENT_REF}).")
    if problems:
        print(f"FAILED: {len(problems)} file(s) out of parity with the parent:")
        print("\n".join(problems))
        print("Sync these files from the parent byte-for-byte, then regenerate "
              "SKILLS.lock with tools/gen_skills_lock.py.")
        return 1
    print("PASSED: every SKILL.md and reference file matches the parent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
