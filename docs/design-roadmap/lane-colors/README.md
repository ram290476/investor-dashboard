# Mini-chart (lane) colors: reference captures

| File | What it shows |
| --- | --- |
| `design-lanes-1440.png` | Crop of `docs/design-roadmap/desktop-full.png` (design artboard `design/ai-generated/Main.dc.html`, **design sample data**): Volume bars colored green/red by up/down day, Macro pressure with green/red fill, neutral lane toggles. |
| `current-<theme>-1440.png` | Local render of `main` @ `8d7c36d` (after #105–#107), price panel from the Volume lane down, 1M period. |
| `mockup-proposed-<theme>-1440.png` | **MOCKUP**: the same render with the proposed lane colors injected as CSS (not implemented). |
| `current-vs-mockup-<theme>-390.png` | 390px: current on the left, proposed MOCKUP on the right. |

Data: TSLA prices and volume come from a cached live dashboard snapshot (Oct 7, 2026). **Short interest, Options and Macro pressure series are synthetic test data**, used only to show colors and layout. Sign-in and preferences are mocked.

#131 later gave each lane its own color (volume stays the theme down color, short interest is lime, options are tan, macro pressure keeps a neutral line with an up/down fill). The `current-*` files stay the pre-#131 side of this comparison. The shipped lanes are in [`../current-app/industrial-dark-1440.png`](../current-app/industrial-dark-1440.png) and [`../current-app/clean-light-1440.png`](../current-app/clean-light-1440.png). There is no separate post-#131 crop of only the lanes.
