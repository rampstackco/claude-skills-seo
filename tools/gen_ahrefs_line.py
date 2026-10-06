#!/usr/bin/env python3
"""Generate the README's Ahrefs MCP line from the parent catalog's SKILL.md files.

The README explains which of the full catalog's SEO skills need the Ahrefs
MCP. That used to be typed by hand ("Four require Ahrefs MCP setup"), and it
drifted from the skills. This script derives it instead:

- SEO skills are the parent's skills whose frontmatter `category` is
  seo-foundation or seo-audit-suite.
- A skill requires the Ahrefs MCP when a bullet in its "## Required inputs"
  section names the Ahrefs MCP. That is a stated dependency; a mention
  anywhere else, or a bullet that offers Ahrefs as one tool among several,
  is not.
- A bullet that names the Ahrefs MCP but limits it with "only when" or
  "only if" makes the dependency conditional.

The parent is read at its main branch: two API calls for the commit and
its tree, then each SKILL.md from raw.githubusercontent.com. GITHUB_TOKEN is
sent to the API when set, only to lift the rate limit.

Usage:
  python tools/gen_ahrefs_line.py --check      # exit 1 if README.md is stale
  python tools/gen_ahrefs_line.py --write      # rewrite the marked block
  python tools/gen_ahrefs_line.py --evidence   # print the per-skill evidence

Exit codes:
  0  README.md matches (or was written).
  1  README.md is stale.
  2  The parent could not be read, or the markers are missing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PARENT_REPO = "rampstackco/claude-skills"
PARENT_REF = "main"
ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"
README = ROOT / "README.md"
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
USER_AGENT = f"{ROOT.name} ahrefs-line generator"
SEO_CATEGORIES = ("seo-foundation", "seo-audit-suite")
MARKER = "AHREFS_MCP"
BLOCK = re.compile(rf"(<!-- {MARKER}:START -->\n)(.*?)(<!-- {MARKER}:END -->)", re.DOTALL)
CATEGORY = re.compile(r'^category:\s*"?([\w-]+)"?\s*$', re.MULTILINE)
CONDITIONAL = re.compile(r"\bonly (when|if)\b", re.IGNORECASE)


def fetch(url: str, api: bool = False) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def frontmatter(text: str) -> str:
    parts = text.split("---", 2)
    return parts[1] if text.startswith("---") and len(parts) == 3 else ""


def required_inputs(text: str) -> list[str]:
    """The bullets of the "## Required inputs" section."""
    match = re.search(r"^## Required inputs\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    return [line[2:].strip() for line in match.group(1).splitlines() if line.startswith("- ")]


def classify(text: str) -> tuple[str, list[str]]:
    """Return ("required" | "conditional" | "none", the bullets that decided it)."""
    bullets = [b for b in required_inputs(text) if re.search(r"\bAhrefs MCP\b", b)]
    if not bullets:
        return "none", []
    if all(CONDITIONAL.search(b) for b in bullets):
        return "conditional", bullets
    return "required", bullets


def parent_seo_skills() -> tuple[str, dict[str, str]]:
    commit = json.loads(fetch(f"{API}/repos/{PARENT_REPO}/commits/{PARENT_REF}", api=True))["sha"]
    tree = json.loads(fetch(f"{API}/repos/{PARENT_REPO}/git/trees/{commit}?recursive=1", api=True))
    if tree.get("truncated"):
        raise RuntimeError(f"{PARENT_REPO}@{commit} tree came back truncated")
    paths = [
        e["path"] for e in tree["tree"]
        if e["type"] == "blob" and re.fullmatch(r"skills/[^/]+/SKILL\.md", e["path"])
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        texts = pool.map(
            lambda p: fetch(f"{RAW}/{PARENT_REPO}/{commit}/{p}").decode("utf-8"), paths
        )
        skills = {}
        for path, text in zip(paths, texts):
            category = CATEGORY.search(frontmatter(text))
            if category and category.group(1) in SEO_CATEGORIES:
                skills[path.split("/")[1]] = text
    return commit, skills


def names(items: list[str]) -> str:
    quoted = [f"`{name}`" for name in items]
    if len(quoted) <= 2:
        return " and ".join(quoted)
    return ", ".join(quoted[:-1]) + ", and " + quoted[-1]


def render(skills: dict[str, str]) -> str:
    here = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
    kinds = {name: classify(text)[0] for name, text in sorted(skills.items())}
    required = [n for n, k in kinds.items() if k == "required"]
    conditional = [n for n, k in kinds.items() if k == "conditional"]
    included = [n for n in required + conditional if n in here]
    left_out = [n for n in required + conditional if n not in here]

    sentences = [f"The full claude-skills catalog has {len(skills)} SEO skills."]
    if required:
        verb = "lists" if len(required) == 1 else "list"
        sentences.append(f"{len(required)} {verb} the Ahrefs MCP as a required input: {names(required)}.")
    if conditional:
        verb = "needs" if len(conditional) == 1 else "need"
        sentences.append(
            f"{len(conditional)} {verb} it only when its data is pulled through Ahrefs: "
            f"{names(conditional)}."
        )
    if included:
        sentences.append(f"Of those, this subset includes {names(included)}.")
    if left_out:
        sentences.append(f"The others ({names(left_out)}) stay in the full catalog.")
    return " ".join(sentences) + "\n"


def evidence(commit: str, skills: dict[str, str]) -> str:
    here = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
    rows = [f"Parent {PARENT_REPO}@{commit}", ""]
    for name, text in sorted(skills.items()):
        kind, bullets = classify(text)
        category = CATEGORY.search(frontmatter(text)).group(1)
        where = "in this subset" if name in here else "parent only"
        rows.append(f"{name} [{category}, {where}]: {kind}")
        for bullet in bullets:
            rows.append(f"    Required inputs: \"{bullet}\"")
        if kind == "none":
            options = [b for b in required_inputs(text) if "ahrefs" in b.lower()]
            for bullet in options:
                rows.append(f"    Required inputs, Ahrefs as one option: \"{bullet}\"")
            mentions = sum(1 for line in text.splitlines() if "ahrefs" in line.lower())
            if not mentions:
                rows.append("    no Ahrefs mention")
            elif not options:
                rows.append(f"    {mentions} mention(s), none in a Required inputs bullet")
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--evidence", action="store_true")
    args = parser.parse_args()

    try:
        commit, skills = parent_seo_skills()
    except (urllib.error.URLError, RuntimeError, KeyError) as error:
        print(f"Could not read {PARENT_REPO}@{PARENT_REF}: {error}")
        return 2
    if args.evidence:
        print(evidence(commit, skills))
        return 0

    text = README.read_text(encoding="utf-8")
    match = BLOCK.search(text)
    if not match:
        print(f"README.md has no <!-- {MARKER}:START --> ... END block.")
        return 2
    expected = render(skills)
    if match.group(2) == expected:
        print(f"README.md Ahrefs MCP line matches {PARENT_REPO}@{commit}.")
        return 0
    if args.write:
        README.write_text(text[: match.start(2)] + expected + text[match.end(2):], encoding="utf-8", newline="\n")
        print(f"README.md Ahrefs MCP line written from {PARENT_REPO}@{commit}.")
        return 0
    print(f"README.md Ahrefs MCP line is stale against {PARENT_REPO}@{commit}.")
    print(f"  committed: {match.group(2).strip()}")
    print(f"  expected:  {expected.strip()}")
    print("Run: python tools/gen_ahrefs_line.py --write")
    return 1


if __name__ == "__main__":
    sys.exit(main())
