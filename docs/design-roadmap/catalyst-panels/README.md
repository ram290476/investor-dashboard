# Filings & events and Catalyst calendar: captures and mockup

Supporting images for the "Update Filings & events and Catalyst calendar panels" issue. The lead requirement is that both panels reuse the **Recent TSLA catalysts** row from #91: date, category dot, one-line title, and 1-day move, or `in Nd` for upcoming items.

| File | What it shows | Data |
| --- | --- | --- |
| `current-side-by-side-{industrial-dark,clean-light}-1440.png` | Recent TSLA catalysts next to today's Filings & events panel and the top of the Catalyst calendar drawer | Real Oct 7, 2026 snapshot, `main` @ 4f3c257 |
| `proposed-side-by-side-{industrial-dark,clean-light}-1440.png` | **MOCKUP:** both panels rebuilt from the real `.catalyst-recent-row` markup and CSS, shown next to the unchanged Recent catalysts list | Past rows and 1-day moves are real (snapshot price history). Filing titles are derived from `filing_class`. **Upcoming rows are illustrative**, because the snapshot had no upcoming TSLA or macro items |
| `mobile-390-before-after.png` | Calendar tab and More tab at 390px, today vs MOCKUP | Same as above |
| `design-recent-catalysts.png` | Crop of the `desktop-signals.png` artboard (Recent TSLA catalysts), scaled 2× | Design placeholders |
| `design-catalyst-calendar.png` | Catalyst calendar drawer re-rendered from `design/ai-generated/Main.dc.html` L448–466 with its placeholder rows (L1448–1457) | Design placeholders. Expected-move values are shown as `[x]` |

`mock_rows.js` is the injected mockup. It imports `overlayColor` from `apps/web/theme.js` and uses only existing classes. `design-cal.html` is the design re-render.
