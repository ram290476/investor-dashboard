# News & sentiment and Filings & events: captures and mockup

Supporting images for the News & sentiment / Filings & events theme, AI summary and sentiment-vs-price issue.

| File | What it shows | Data |
| --- | --- | --- |
| `current-industrial-dark-1440.png`, `current-clean-light-1440.png` | Both side-column panels as they render on `main` (9436287) at 1440px, sentiment overlay on, drawer open | Real production snapshot from Oct 7, 2026 (TSLA) |
| `current-industrial-dark-390-more.png`, `current-clean-light-390-more.png` | The same panels on the mobile **More** tab at 390px | Same snapshot |
| `current-six-themes-1440.png` | News panel top in all six themes (half scale) | Same snapshot |
| `design-news-reference.png` | The design's "TSLA news sentiment" panel, re-rendered from `design/ai-generated/Main.dc.html` lines 426–440 (the PNG artboards don't include this panel) | The design's own placeholders; sparkline path approximated; row times come from the design's `tm()` helper in PT (`Fri 12:42`) |
| `mockup-industrial-dark-1440.png`, `mockup-clean-light-1440.png`, `mockup-industrial-dark-390-more.png` | **MOCKUP** of the proposed item rows (ticker tag, source · PT time, sentiment chip, AI summary, expanded price-reaction row, filing item chips, events split out) injected into the running app so it uses the real theme tokens | **Made up**: headlines are paraphrased and every AI summary, score, return, correlation and Form 4 detail is invented |
| `dates-before-after-industrial-dark.png`, `dates-before-after-clean-light.png` | Before/after for the readable data and date rules: left is what `main` renders, right is the same items formatted with the proposed rules (now = Wed Oct 7 19:15 PT, zone PT) | Real Oct 7 snapshot. Filing titles are derived from `filing_class` and the period date in the filename. Nothing is made up |
| `mockup-six-themes-1440.png` | The mockup in all six themes (half scale); chips follow the up/down palette (blue/orange in colorblind-hc) | Made up, as above |

`mock_dates.js` builds the before/after image. `mock_news.js` is the injected mockup (it only uses existing CSS variables) and `design-ref.html` is the static design re-render.
Captures come from a Playwright harness that serves `apps/web` with mocked `/dashboard`, `/prefs`, `/status` and `/chart` responses at device scale factor 2.
