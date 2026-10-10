# Issue 133 screenshots

Before and after captures use the same fixture (TSLA, 1Y, Industrial Dark and Clean Light) at 1440 and 390. The original column is the signals aside from `design/ai-generated/Main.dc.html`.

## Item 7 differences

| File | Difference | Fix |
| --- | --- | --- |
| `07-pill-position-industrial-dark-1440.png`, `07-pill-position-industrial-dark-390.png` | Regime pills sat below Top drivers | Pills sit under the pressure note, above Top drivers |
| `07-pill-style-industrial-dark-1440.png`, `07-pill-style-industrial-dark-390.png` | Pills were plain `Label · State` text | Each pill is a 6px direction dot, a muted label, and the state |
| `07-pill-inset-industrial-dark-1440.png`, `07-pill-inset-industrial-dark-390.png` | The pill row reused the catalyst-filter inset | The row is flush with the signals body |
| `07-sensitivity-form-industrial-dark-1440.png`, `07-sensitivity-form-industrial-dark-390.png` | Sensitivity was release-correlation sentences | The block is `Catalyst sensitivity · avg \|1d\|` between Top drivers and Recent catalysts |
| `07-sensitivity-rows-industrial-dark-1440.png`, `07-sensitivity-rows-industrial-dark-390.png` | Rows had no bar, percent, or trend | Each row shows name `(n)`, a 6px bar, the average absolute 1-day move, and intensifying / fading / stable |
| `07-panel-original-after-1440.png`, `07-panel-original-after-390.png` | Full panel | Original design beside the updated panel |
| `07-panel-clean-light-1440.png`, `07-panel-clean-light-390.png` | Clean Light | Before beside after |

Intentionally kept: no separate “Trends” heading (the canvas has none); a Price pill; Oil when WTI is published; the all-drivers drawer; `sensitivityRows` remains exported for the release-correlation tests.

## Other items

`01` company-page back link, `02` chart axes, `03` DS-11 line removed, `04` ticker bar, `05` Open company page link, `06` control height, `08` news spacing, `09` theme buttons, `10` header. Each file is before | after for Industrial Dark or Clean Light at 1440 or 390.
