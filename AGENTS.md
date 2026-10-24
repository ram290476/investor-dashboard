# AGENTS.md

Guidance for people and coding agents working on investor-dashboard.

## Standard workflow for any new work

1. **Issue first.** Create a GitHub issue describing the work before any code changes. The issue must state which tests are expected (new or updated), so CI catches gaps.
2. **Link the wiki.** If the issue relates to an existing wiki page, update that wiki page to reference the new issue (link it, for example `#123`).
3. **Implement against the issue, with tests.** Do the work (via a coding agent) on a branch, and open a PR that links the issue (for example `Closes #123`). New work ships with the tests the issue calls for.
4. **All required checks pass.** Merge only when every required check passes (currently `validate`). A green run with skipped tests is not validation. Never merge on a red, cancelled, or flaky run, and do not re-run a failing check until it happens to go green.
5. **Squash-merge.** Once all required checks pass, squash-merge the PR (`gh pr merge --squash`). Do not rely on GitHub auto-merge: the repo has no required checks, so auto-merge would merge immediately.
6. **Destroy/replace is a hard block.** A deploy whose Terraform plan shows any destroy or replace must be stopped automatically, without a human needing to remember. Merging to `main` automatically triggers the "Deploy production" workflow; check its `Plan:` line. A normal plan shows `0 to destroy`. Fallback: if a plan would destroy or replace any resource, cancel the deploy immediately and report back instead of letting it proceed.
   - Note: today the plan runs only post-merge, inside Deploy production. An automatic pre-merge/pre-apply block is the required target; until a PR-time plan gate exists, this rule is enforced by the post-merge check above.
7. **Verify, or roll back.** Confirm the Deploy production run succeeded, the site serves `?v=<merge shortsha>`, and the dashboard actually loads and renders. If not, report it and revert the PR (the revert goes through a new PR that follows this workflow). A merged PR is not a working dashboard.
8. **Update the wiki with the fix.** Once merged and verified, update the related wiki page with screenshots of the fix. Commit the image files into the wiki repo (for example an `images/` folder in the `.wiki.git` repo) and render them inline with relative markdown links, for example `![Fix](images/issue-123-after.png)`. Never use external URLs such as `raw.githubusercontent.com` for wiki images.

## Related rules

- Changes apply to every watchlist ticker. Do not hard-code tickers.
- Never run a local `terraform apply`.
- Embed PR screenshots with `raw.githubusercontent.com` URLs.
- In the later sections of `apps/web/styles.css`, use `rgb()` or CSS variables instead of `#` hex colors, because the tests reject hex colors there.
