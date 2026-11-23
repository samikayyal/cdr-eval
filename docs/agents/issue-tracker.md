# Issue tracker: GitHub

Issues and specs live in https://github.com/samikayyal/cdr-eval.
Use the `gh` CLI for issue operations.

## Conventions

Pass `--repo samikayyal/cdr-eval` to issue commands so they also
work when this folder is not a Git clone.

- Create: `gh issue create --repo samikayyal/cdr-eval --title "..." --body-file <file>`
- Read: `gh issue view <number> --repo samikayyal/cdr-eval --json number,title,body,labels,comments`
- List: `gh issue list --repo samikayyal/cdr-eval --state open --json number,title,body,labels`
- Comment: `gh issue comment <number> --repo samikayyal/cdr-eval --body-file <file>`
- Label: `gh issue edit <number> --repo samikayyal/cdr-eval --add-label "<label>"`
- Remove label: `gh issue edit <number> --repo samikayyal/cdr-eval --remove-label "<label>"`
- Close: `gh issue close <number> --repo samikayyal/cdr-eval`

Use a temporary UTF-8 file for multiline issue bodies and comments.
Use the label vocabulary in `triage-labels.md`.

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub shares issue and PR numbers. Resolve ambiguous references
with `gh pr view`, falling back to `gh issue view`.

## Skill operations

When a skill says "publish to the issue tracker", create a GitHub issue.
When it says "fetch the relevant ticket", read the referenced issue.

## Wayfinding operations

- Map: one issue labelled `wayfinder:map`, containing Notes,
  Decisions-so-far, and Fog.
- Children: link tickets as native GitHub sub-issues. If unavailable,
  use a task list in the map and `Part of #<map>` in each child.
  Use `wayfinder:<type>` for research, prototype, grilling, or task.
- Blocking: use native GitHub issue dependencies. If unavailable,
  record `Blocked by: #<number>` in the child. A child is unblocked
  when every blocker is closed.
- Frontier: choose the first open, unblocked, unassigned child
  in map order.
- Claim: assign the ticket to the driving developer before work.
- Resolve: comment with the answer, close the child, and append
  a summary and link to the map's Decisions-so-far.
