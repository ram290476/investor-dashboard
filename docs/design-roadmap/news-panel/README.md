# News & sentiment panel: current vs MOCKUP

Captures for the News & sentiment issue (shared catalyst row from #127). The state of `main` at `c7bb90c` is rendered with the Oct 7, 2026 dashboard snapshot (served at 02:15 UTC, i.e. Oct 7, 19:15 PT).

- `news-before-after-industrial-dark-1440.png`, `news-before-after-clean-light-1440.png`: today on the left, MOCKUP on the right, at 1440 px.
- `news-before-after-mobile-390.png`: the More tab at 390 px.

What's real and what's illustrative in the MOCKUP:
- **Real:**
  - the ten "Today" headlines, their titles with the " - Publisher" suffix removed, and their publishers
  - ages relative to the snapshot time
  - every 1-day move, taken from the snapshot's price history with the after-close next-session rule. Headlines published after Oct 7's close show `—` because the Oct 8 session hadn't closed yet.
- **Illustrative:**
  - **every sentiment score and dot color**, because per-headline scores aren't served yet
  - the four "This week" / "Earlier" rows, which show how grouping looks
