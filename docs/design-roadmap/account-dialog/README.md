# Account settings dialog: current UI screenshots

Captured Oct 7, 2026 from `apps/web` on main @ 6e13c86 (Account settings dialog #49, Data refresh tab #54).

- Served `apps/web` locally with headless Chrome via Playwright (`capture.mjs`).
- Cognito token exchange and the API (`/prefs`, `/dashboard`, `/status`) were mocked.
- `/dashboard` and `/status` used the live `serving/dashboard.json` and `serving/status.json` from Oct 7, 2026 7:15 PM PT.
- `/prefs` is a sample watchlist: 7 tickers, with TSLA, NVDA and SPCX pinned. Time zone is PT.
- The signed-in email is a placeholder.

| File | View |
|---|---|
| account-menu-1440.png | Account menu open (1440px) |
| my-tickers-1440.png | My tickers tab |
| profile-time-zone-1440.png | Profile & time zone tab |
| [../issue-133/09-theme-industrial-dark-1440.png](../issue-133/09-theme-industrial-dark-1440.png), [../issue-133/09-theme-clean-light-1440.png](../issue-133/09-theme-clean-light-1440.png), [../issue-133/09-theme-industrial-dark-390.png](../issue-133/09-theme-industrial-dark-390.png), [../issue-133/09-theme-clean-light-390.png](../issue-133/09-theme-clean-light-390.png) | Theme & display after #134. Each file is before \| after; the right side is the current one-line theme buttons. `theme-display-1440.png` is the October 7 tab, before that fix. |
| data-refresh-1440.png | Data refresh tab (job status table) |
| my-tickers-390.png | My tickers, 390px mobile |
| data-refresh-390.png | Data refresh, 390px mobile |
| data-refresh-390-scrolled.png | Data refresh, 390px, table scrolled right |

`capture.mjs` hard-codes local paths: `/workspace/invdash/wt-final/apps/web`, plus payloads in `/workspace/invdash/acct/payloads`.
