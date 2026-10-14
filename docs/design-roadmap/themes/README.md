# Color theme reference screenshots

Design references for the **color theme switcher** roadmap issue. Each theme is a token set in
[`themes.json`](themes.json) (also emitted as `<id>.css` custom properties), rendered onto the
hi-fi desktop mockup `design/ai-generated/Main.dc.html` with the same 1440px headless-Chrome
capture used for `../desktop-full.png`.

Themes are **inspired by** familiar financial-dashboard looks; none reproduces a brand palette.
Values in the screenshots are **design sample data** from `support.js`, not live market data.

| Theme | Id | Inspired by | Key colors (bg · surface · text · accent · up / down) | Screenshot |
| --- | --- | --- | --- | --- |
| Industrial Dark (default) | `industrial-dark` | Current dashboard design | `#0B0E13` · `#12161D` · `#E6EAF0` · `#6CA6FF` · `#3FB950` / `#F85149` | [png](industrial-dark-desktop-full.png) |
| Terminal Amber | `terminal-amber` | Bloomberg-style market terminals | `#000000` · `#080706` · `#FFA028` · `#FFD23F` · `#22D65F` / `#FF4136` | [png](terminal-amber-desktop-full.png) |
| Charting Navy | `charting-navy` | Dark mode of popular charting platforms | `#0A1120` · `#111A2E` · `#D9E1EE` · `#4C8DFF` · `#26A69A` / `#EF5350` | [png](charting-navy-desktop-full.png) |
| Clean Light | `clean-light` | Mainstream finance portals (light) | `#F3F5F8` · `#FFFFFF` · `#111827` · `#1A62D6` · `#0B7A3B` / `#C62828` | [png](clean-light-desktop-full.png) |
| Colorblind-safe High Contrast | `colorblind-hc` | Accessibility-first dashboards (Okabe-Ito) | `#000000` · `#0B0D10` · `#FFFFFF` · `#FFFFFF` · `#56B4E9` / `#E69F00` | [png](colorblind-hc-desktop-full.png) |
| Midnight Slate | `midnight-slate` | Modern fintech / crypto dashboards | `#0F172A` · `#162036` · `#F1F5F9` · `#818CF8` · `#34D399` / `#FB7185` | [png](midnight-slate-desktop-full.png) |

WCAG contrast for every theme (text, muted text, up/down, warning, heat chips, chart series) is in
[`contrast.md`](contrast.md); all body-text pairs are **≥ 4.5:1 (AA)**.

## Files

| File | Purpose |
| --- | --- |
| `themes.json` | Source of truth: token values per theme (`bg`, `surface`, `surface-2`, `border`, `border-strong`, `grid`, `text`, `text-2`, `text-muted`, `text-faint`, `accent`, `accent-text`, `up`, `down`, `neutral`, `warning`, `series-1…6`, `heat`) |
| `<id>.css` | Generated `:root[data-theme="<id>"] { --token: … }` blocks — the shape proposed for `apps/web` |
| `mockup-color-roles.json` | Maps each literal color in `Main.dc.html` to a token (or a tint mix of two tokens) |
| `render-themes.js` | Regenerates the CSS files, `contrast.md`, and all `<id>-desktop-full.png` |
| `contrast.md` | Generated WCAG ratios |

## Regenerating

```sh
# from the repo root; needs Chrome/Chromium and puppeteer-core
npm i --no-save puppeteer-core
CHROME=/usr/bin/google-chrome node docs/design-roadmap/themes/render-themes.js            # all themes
node docs/design-roadmap/themes/render-themes.js clean-light                               # one theme
node docs/design-roadmap/themes/render-themes.js --no-shots                                # CSS + contrast only
```

The script serves `design/ai-generated/` locally, waits for `support.js` to bind sample data
(`data-dc-preview="ready"`), then recolors the rendered DOM through `mockup-color-roles.json`
(alpha preserved) before capturing. **The mockup file itself is not modified.** The mockup hard-codes
hex literals rather than CSS variables, so this is a render-time token map, not how `apps/web` should
implement themes (see the roadmap issue: CSS custom properties on `:root[data-theme]`).

## Production application themes

Issue #64 implements these palettes through [the application theme module](../../../apps/web/theme.js).
The production module applies semantic properties to the document root, including the account dialog,
and adjusts text, chart series, controls, and overlays for contrast on their actual backgrounds.
[Theme tests](../../../apps/web/theme.test.js) enforce palette parity, AA text contrast, 3:1
non-text contrast, and independent direction-palette overrides. The default direction palette remains
green/red for existing users; choose **Theme default** to use each theme's own up/down colors.

These captures show the **real application** at 1440px, not the design mockup. Authentication,
prices, headlines, filings, fundamentals, and macro values are synthetic fixtures, not live data.
The links below are a local preview of `main` on October 10, 2026, after catalyst rows (#127),
news rows (#130), mini-chart colors and the wider company page (#131), and the #133 UI fixes (#134).
The `production-*.png` files in this folder are the October 8 captures, from before those changes.

| Theme | Production capture |
| --- | --- |
| Industrial Dark | [Desktop](../current-app/industrial-dark-1440.png) |
| Terminal Amber | [Desktop](../current-app/terminal-amber-1440.png) |
| Charting Navy | [Desktop](../current-app/charting-navy-1440.png) |
| Clean Light | [Desktop](../current-app/clean-light-1440.png) / [390px chart](../current-app/clean-light-390-chart.png) |
| Colorblind High Contrast | [Desktop](../current-app/colorblind-hc-1440.png) |
| Midnight Slate | [Desktop](../current-app/midnight-slate-1440.png) |

To regenerate with Chrome and `puppeteer-core` available, serve `apps/web` on
`http://127.0.0.1:8716`, then run:

```sh
node docs/design-roadmap/themes/render-production-themes.js
```

Set `CHROME` for a different Chrome/Chromium executable (the default is macOS Google Chrome).
The script also checks autosave, reload persistence, chart-setting preservation, overlay/legend
color parity, failed-save rollback, console errors, and desktop/mobile overflow.
It intercepts authentication and API requests and never contacts Cognito or market-data services.
For Cognito rollout and real authentication checks, see [operations](../../operations.md#dashboard-themes-and-cognito-branding).
