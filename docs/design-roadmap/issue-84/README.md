# Issue #84: mini charts below the price chart (before the #84 layout change)

These captures show the price panel of `main` @ `5b45137`, before any #84 changes, rendered locally in Chrome at device scale 2. They are not the current chart. #84 shipped in #105, #131 recolored the lanes, and #134 changed the axis labels and control height. The current price panel is [`../current-app/industrial-dark-1440.png`](../current-app/industrial-dark-1440.png).

| File | Viewport | Theme | Period |
| --- | --- | --- | --- |
| `industrial-dark-1440-1M.png` | 1440px | Industrial Dark | 1M |
| `industrial-dark-1440-1Y.png` | 1440px | Industrial Dark | 1Y |
| `clean-light-1440-1M.png` | 1440px | Clean Light | 1M |
| `industrial-dark-390-1M.png` | 390px | Industrial Dark | 1M |
| `clean-light-390-1M.png` | 390px | Clean Light | 1M |

Data: TSLA prices and volume come from a cached live dashboard snapshot (generated Oct 7, 2026 7:15 PM PT). **Short interest, Options and Macro pressure series are synthetic test data**, used only to show layout. Sign-in and preferences are mocked locally.

Measured plot areas at 1440px: the price line spans x 115–889 px, while the mini charts span x ≈208–791 px (about 25% narrower, centered). At 390px the mini-chart plot is about 20px tall inside a 52px box. The Volume lane's near-zero bars from 2026-09-29 reflect the snapshot's switch to IEX-only volume.
