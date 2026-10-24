# AGENTS.md

Guidance for people and coding agents working on investor-dashboard.

## Standard workflow for any new work

1. **Issue first.** Create a GitHub issue describing the work before any code changes.
2. **Link the wiki.** If the issue relates to an existing wiki page, update that wiki page to reference the new issue (link it, for example `#123`).
3. **Implement against the issue.** Do the work (via a coding agent) on a branch, and open a PR that links the issue (for example `Closes #123`).
4. **Wait for CI.** Wait for the `validate` job to pass.
5. **Squash-merge.** Once `validate` passes, squash-merge the PR (`gh pr merge --squash`). Do not rely on GitHub auto-merge: the repo has no required checks, so auto-merge would merge immediately.
6. **Deploy destroy check.** Merging to `main` automatically triggers the "Deploy production" workflow. After the merge, check the Terraform `Plan:` line in the deploy. If it would destroy or replace any resource, cancel the deploy immediately and report back instead of letting it proceed. A normal plan shows `0 to destroy`.
7. **Update the wiki with the fix.** Once merged, update the related wiki page with screenshots of the fix. Commit the image files into the wiki repo (for example an `images/` folder in the `.wiki.git` repo) and render them inline with relative markdown links, for example `![Fix](images/issue-123-after.png)`. Never use external URLs such as `raw.githubusercontent.com` for wiki images.

## Related rules

- Changes apply to every watchlist ticker. Do not hard-code tickers.
- Never run a local `terraform apply`.
- Embed PR screenshots with `raw.githubusercontent.com` URLs.
- In the later sections of `apps/web/styles.css`, use `rgb()` or CSS variables instead of `#` hex colors, because the tests reject hex colors there.
