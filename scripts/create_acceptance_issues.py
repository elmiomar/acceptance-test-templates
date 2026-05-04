#!/usr/bin/env python3
"""
Create release acceptance issues from GitHub issue form templates.

For each YAML form under .github/ISSUE_TEMPLATE/, this script:
  1. Replaces [Version] in the template title with the release version.
  2. Renders the form body (markdown, input, textarea, checkboxes, dropdown)
     into a markdown issue body.
  3. Creates one GitHub issue with the template's labels and the resolved
     assignees for that template.

Templates whose titles do not contain [Version] are skipped by default. Pass
--include-no-version to override.

Assignees are resolved per template in this order:
  1. --assign FILENAME=USER1,USER2  (CLI override, repeatable)
  2. Entries in --assignees-file     (defaults to
                                      .github/release-acceptance-assignees.yml
                                      if it exists)
  3. --assignee USER                 (default applied to any template not
                                      covered above, repeatable)

Typical manual run after deploying to test:

    export GITHUB_TOKEN=ghp_xxx
    python scripts/create_acceptance_issues.py \\
        --version 1.15.5 \\
        --repo usnistgov/oar-pdr-py

Use --preview first to see titles, labels, and assignees without making changes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml


GITHUB_API = "https://api.github.com"
PLACEHOLDER = "[Version]"
DEFAULT_TEMPLATES_DIR = ".github/ISSUE_TEMPLATE"
DEFAULT_ASSIGNEES_FILE = ".github/release-acceptance-assignees.yml"
SKIP_FILES = {"config.yml", "config.yaml"}


def render_body(template: dict[str, Any]) -> str:
    """Render an issue form template's body section to markdown."""
    parts: list[str] = []
    body = template.get("body") or []

    for element in body:
        if not isinstance(element, dict):
            continue
        kind = element.get("type")
        attrs = element.get("attributes") or {}
        label = attrs.get("label")

        if kind == "markdown":
            value = (attrs.get("value") or "").strip()
            if value:
                parts.append(value)

        elif kind == "input":
            value = attrs.get("value") or attrs.get("placeholder") or ""
            if label:
                parts.append(f"**{label}:** {value}".rstrip())

        elif kind == "textarea":
            value = (attrs.get("value") or "").strip()
            if label:
                section = f"### {label}"
                parts.append(f"{section}\n\n{value}" if value else section)

        elif kind == "checkboxes":
            lines: list[str] = []
            if label:
                lines.append(f"### {label}")
                lines.append("")
            for opt in attrs.get("options") or []:
                opt_label = (opt or {}).get("label", "")
                lines.append(f"- [ ] {opt_label}")
            if lines:
                parts.append("\n".join(lines))

        elif kind == "dropdown":
            options = attrs.get("options") or []
            if label:
                rendered = ", ".join(str(o) for o in options)
                parts.append(f"**{label}:** {rendered}".rstrip())

    return ("\n\n".join(p for p in parts if p)).strip() + "\n"


def load_templates(templates_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Return (path, parsed_yaml) for every issue form in templates_dir."""
    paths = sorted(
        list(templates_dir.glob("*.yml")) + list(templates_dir.glob("*.yaml"))
    )
    out: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        if path.name in SKIP_FILES:
            continue
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            print(f"warning: skipping {path.name}, YAML parse error: {exc}", file=sys.stderr)
            continue
        if not isinstance(data, dict) or "title" not in data:
            continue
        out.append((path, data))
    return out


def load_assignees_map(path: Path) -> dict[str, list[str]]:
    """Load a YAML map of template filename to list of GitHub usernames."""
    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(
            f"assignees file must be a mapping at the top level, got {type(loaded).__name__}"
        )
    result: dict[str, list[str]] = {}
    for key, value in loaded.items():
        filename = str(key)
        if value is None:
            result[filename] = []
        elif isinstance(value, str):
            result[filename] = [value]
        elif isinstance(value, list):
            users = [str(u).strip() for u in value if str(u).strip()]
            result[filename] = users
        else:
            raise ValueError(
                f"assignees for {filename} must be a list or string, got {type(value).__name__}"
            )
    return result


def parse_assign_override(value: str) -> tuple[str, list[str]]:
    """Parse a --assign argument like 'filename.yml=user1,user2'."""
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"--assign expects FILENAME=USER1[,USER2...], got: {value!r}"
        )
    filename, users = value.split("=", 1)
    filename = filename.strip()
    if not filename:
        raise argparse.ArgumentTypeError(f"--assign filename is empty in: {value!r}")
    user_list = [u.strip() for u in users.split(",") if u.strip()]
    return filename, user_list


def github_request(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2026-03-10")
    req.add_header("User-Agent", "create-acceptance-issues")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {url} failed: {exc.code} {body}") from exc


def create_issue(
    owner: str,
    repo: str,
    token: str,
    title: str,
    body: str,
    labels: list[str],
    assignees: list[str],
) -> dict:
    payload: dict[str, Any] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    if assignees:
        payload["assignees"] = assignees
    return github_request(
        "POST",
        f"{GITHUB_API}/repos/{owner}/{repo}/issues",
        token,
        payload,
    )


def parse_repo(repo_arg: str | None) -> tuple[str, str]:
    repo = repo_arg or os.environ.get("GITHUB_REPOSITORY")
    if not repo or "/" not in repo:
        raise SystemExit(
            "Repository must be provided as 'owner/repo' via --repo or GITHUB_REPOSITORY"
        )
    owner, name = repo.split("/", 1)
    return owner, name


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create release acceptance issues from issue form templates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        required=True,
        help="Release version to substitute for [Version] in template titles.",
    )
    parser.add_argument(
        "--repo",
        help="GitHub repository as owner/repo. Defaults to $GITHUB_REPOSITORY.",
    )
    parser.add_argument(
        "--token",
        help="GitHub token. Defaults to $GITHUB_TOKEN.",
    )
    parser.add_argument(
        "--templates-dir",
        default=DEFAULT_TEMPLATES_DIR,
        help=f"Directory containing issue form templates (default: {DEFAULT_TEMPLATES_DIR}).",
    )
    parser.add_argument(
        "--assignees-file",
        default=None,
        help=(
            "Path to a YAML file mapping template filename to list of GitHub usernames. "
            f"If omitted and {DEFAULT_ASSIGNEES_FILE} exists, it is loaded automatically."
        ),
    )
    parser.add_argument(
        "--assign",
        action="append",
        default=[],
        type=parse_assign_override,
        metavar="FILENAME=USER1,USER2",
        help=(
            "Per-template assignee override. Repeatable. Example: "
            "--assign acceptance-metrics.yml=alice,bob. "
            "Overrides any entry in the assignees file for that template."
        ),
    )
    parser.add_argument(
        "--assignee",
        action="append",
        default=[],
        metavar="USERNAME",
        help=(
            "Default GitHub username applied to any template not covered by "
            "--assign or the assignees file. Repeatable."
        ),
    )
    parser.add_argument(
        "--include-no-version",
        action="store_true",
        help="Also process templates whose title does not contain [Version].",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="FILENAME",
        help="Only process templates with these filenames. Repeatable.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="FILENAME",
        help="Skip templates with these filenames. Repeatable.",
    )
    parser.add_argument(
        "--extra-label",
        action="append",
        default=[],
        metavar="LABEL",
        help="Additional label to apply to every created issue. Repeatable.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Print the planned issues without calling the GitHub API.",
    )
    return parser


def resolve_assignees(
    filename: str,
    overrides: dict[str, list[str]],
    file_map: dict[str, list[str]],
    default_assignees: list[str],
) -> list[str]:
    if filename in overrides:
        return overrides[filename]
    if filename in file_map:
        return file_map[filename]
    return default_assignees


def resolve_assignees_file(arg_value: str | None) -> Path | None:
    """Determine which assignees file to load, or None if no map should apply."""
    if arg_value is not None:
        path = Path(arg_value)
        if not path.is_file():
            raise SystemExit(f"error: assignees file not found: {path}")
        return path
    default_path = Path(DEFAULT_ASSIGNEES_FILE)
    if default_path.is_file():
        return default_path
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    templates_dir = Path(args.templates_dir)
    if not templates_dir.is_dir():
        print(f"error: templates directory not found: {templates_dir}", file=sys.stderr)
        return 2

    owner, repo = parse_repo(args.repo)
    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token and not args.preview:
        print("error: GitHub token must be provided via --token or $GITHUB_TOKEN", file=sys.stderr)
        return 2

    assignees_file_map: dict[str, list[str]] = {}
    assignees_path = resolve_assignees_file(args.assignees_file)
    if assignees_path is not None:
        try:
            assignees_file_map = load_assignees_map(assignees_path)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"using assignees file: {assignees_path}")

    assign_overrides: dict[str, list[str]] = dict(args.assign)
    default_assignees = list(args.assignee)

    templates = load_templates(templates_dir)
    if not templates:
        print(f"error: no issue form templates found in {templates_dir}", file=sys.stderr)
        return 1

    template_filenames = {p.name for p, _ in templates}
    for source, mapping in [
        ("assignees file", assignees_file_map),
        ("--assign", assign_overrides),
    ]:
        unknown = sorted(set(mapping) - template_filenames)
        if unknown:
            print(
                f"warning: {source} references templates not found in {templates_dir}: "
                + ", ".join(unknown),
                file=sys.stderr,
            )

    include = set(args.include)
    exclude = set(args.exclude)

    created: list[dict] = []
    skipped: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []

    for path, tpl in templates:
        if include and path.name not in include:
            skipped.append((path.name, "not in --include list"))
            continue
        if path.name in exclude:
            skipped.append((path.name, "in --exclude list"))
            continue

        title = tpl.get("title", "")
        if PLACEHOLDER not in title and not args.include_no_version:
            skipped.append((path.name, f"title has no {PLACEHOLDER} placeholder"))
            continue

        rendered_title = title.replace(PLACEHOLDER, args.version)
        rendered_body = render_body(tpl)
        labels = list(tpl.get("labels") or [])
        for extra in args.extra_label:
            if extra not in labels:
                labels.append(extra)

        assignees = resolve_assignees(
            path.name, assign_overrides, assignees_file_map, default_assignees
        )

        if args.preview:
            print(f"[preview] {path.name}")
            print(f"  title    : {rendered_title}")
            print(f"  labels   : {labels}")
            print(f"  assignees: {assignees}")
            preview = rendered_body[:200].replace("\n", " ")
            print(f"  body     : {preview}{'...' if len(rendered_body) > 200 else ''}")
            print()
            continue

        try:
            issue = create_issue(
                owner=owner,
                repo=repo,
                token=token,
                title=rendered_title,
                body=rendered_body,
                labels=labels,
                assignees=assignees,
            )
            print(f"created #{issue['number']}: {issue['html_url']}")
            created.append(issue)
        except RuntimeError as exc:
            print(f"error: failed to create issue from {path.name}: {exc}", file=sys.stderr)
            failed.append((path.name, str(exc)))

    print()
    print(f"summary: created={len(created)}, skipped={len(skipped)}, failed={len(failed)}")
    for name, reason in skipped:
        print(f"  skipped {name}: {reason}")
    for name, reason in failed:
        print(f"  failed  {name}: {reason}")

    if failed:
        return 1
    if not args.preview and not created:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
