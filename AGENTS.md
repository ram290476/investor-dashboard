# AGENTS.md

Guidance for people and coding agents working on investor-dashboard.

## Standard workflow for any new work

1. **Issue first.** Create a GitHub issue describing the work before any code changes.
2. **Implement against the issue.** Do the work (via a coding agent) on a branch, and open a PR that links the issue (for example `Closes #123`).
3. **Wait for CI.** Wait for the `validate` job to pass.
4. **Squash-merge.** Once `validate` passes, squash-merge the PR (`gh pr merge --squash`). Do not rely on GitHub auto-merge: the repo has no required checks, so auto-merge would merge immediately.
5. **Deploy destroy check.** Merging to `main` automatically triggers the "Deploy production" workflow. After the merge, check the Terraform `Plan:` line in the deploy. If it would destroy or replace any resource, cancel the deploy immediately and report back instead of letting it proceed. A normal plan shows `0 to destroy`.

## Related rules

- Changes apply to every watchlist ticker. Do not hard-code tickers.
- Never run a local `terraform apply`.
- Embed PR screenshots with `raw.githubusercontent.com` URLs.
- In the later sections of `apps/web/styles.css`, use `rgb()` or CSS variables instead of `#` hex colors, because the tests reject hex colors there.
