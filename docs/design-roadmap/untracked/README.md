# Captures for the untracked design and ops issues (Oct 9, 2026)

These are the reference images embedded in the issues filed from the design-vs-live gap review. Each issue links its own files.

| Prefix | Source |
| --- | --- |
| `design-*.png` | Crops of `docs/design-roadmap/desktop-full.png` (the AI-generated design artboard `design/ai-generated/Main.dc.html`). **Design sample data**, not live values. |
| `current-*-1440.png` crops (overlays, legend, recent catalysts, rates & yields, below-chart, volume) | Crops of `docs/design-roadmap/issue-76/industrial-dark-1440.png` on main: live market data captured Oct 8–9, 2026, with mock sign-in and preferences. The panels shown are unchanged on `main` @ `5b45137`. |
| `current-header-watchlist-*.png`, `current-data-refresh-*.png` | Fresh local render of `main` @ `5b45137` in Chrome (device scale 2), Industrial Dark, at 1440px and 390px. Uses a cached live dashboard snapshot (Oct 7, 2026 7:15 PM PT) and `/status` from the same time. Sign-in and preferences are mocked. Mini-chart series (short interest, options, macro pressure) in the background are **synthetic test data**. |
| `mockup-tesla-page-1440.png` | **MOCKUP with made-up data** for #87, built by injecting placeholder panels into the real `/ticker/TSLA` research page, so it uses the app's styles and theme. Not real Tesla figures. |

Header, ticker bar, lane colors, and control height changed after these captures (#131 and #134). The later shots of those panels are [`../issue-133/10-header-industrial-dark-1440.png`](../issue-133/10-header-industrial-dark-1440.png), [`../issue-133/04-ticker-industrial-dark-1440.png`](../issue-133/04-ticker-industrial-dark-1440.png), [`../issue-133/06-controls-industrial-dark-1440.png`](../issue-133/06-controls-industrial-dark-1440.png), and [`../current-app/industrial-dark-1440.png`](../current-app/industrial-dark-1440.png). The files in this folder stay the October 9 record.
