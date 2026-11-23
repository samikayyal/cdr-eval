## Python

Use `uv` for all Python work:
- Manage dependencies with `uv add <package>`.
- Run Python files with `uv run <file.py>`.

## GitHub workflow

- Start the next GitHub issue only after confirming on GitHub that the pull requests for all previously started issues have merged.
- For each GitHub issue, fetch the latest default branch and create a fresh branch in a new Git worktree from it. Do the issue work in that worktree.
- Before wrapping up any task that changes project files, commit the task changes, push the branch to GitHub, and verify that GitHub has the pushed commit. For issue work, open or update its pull request before reporting completion.
- Include `Closes #<issue>` in the pull request description so GitHub closes the linked issue when the pull request merges into the default branch.

## Agent skills

### Issue tracker

Track issues and specs in GitHub Issues for `samikayyal/cdr-eval`.
Read `docs/agents/issue-tracker.md` before issue operations.

### Triage labels

Use the five default triage labels.
Read `docs/agents/triage-labels.md` before classifying issues.

### Domain docs

Use a single-context layout: root `GLOSSARY.md` and `docs/adr/`.
Read `docs/agents/domain.md` before exploring the codebase.
