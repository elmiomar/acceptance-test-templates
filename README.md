# Acceptance Test Templates

`scripts/create_acceptance_issues.py` reads every form under `.github/ISSUE_TEMPLATE/`, substitutes `[Version]` in each title with a release version, and creates one GitHub issue per template with the right labels and assignees.

## Prerequisites

- Python 3.9+
- `pip install pyyaml`
- A GitHub token with `repo` scope:

  ```powershell
  $env:GITHUB_TOKEN = "ghp_..."
  ```

## Preview (no API calls)

```
python scripts/create_acceptance_issues.py --version 1.15.5 --repo OWNER/REPO --preview
```

Prints the title, labels, and assignees for each template. Templates with no `[Version]` in the title are skipped unless you pass `--include-no-version`.

## Create the issues

```
python scripts/create_acceptance_issues.py --version 1.15.5 --repo OWNER/REPO
```

To limit the run, use `--include FILENAME` (repeatable) or `--exclude FILENAME`.

## Assignees

`.github/release-acceptance-assignees.yml` maps each template filename to a list of GitHub usernames. Auto-loaded if present. Override per-template on the CLI:

```
--assign acceptance-metrics.yml=alice,bob
```

Or set a default for any unmapped template:

```
--assignee carol
```

GitHub emails assignees automatically when the issue is created.
