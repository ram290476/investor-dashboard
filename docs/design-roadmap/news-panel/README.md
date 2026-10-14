# News & sentiment panel: current vs MOCKUP

Captures for the News & sentiment issue (shared catalyst row from #127). The state of `main` at `c7bb90c` is rendered with the Oct 7, 2026 dashboard snapshot (served at 02:15 UTC, i.e. Oct 7, 19:15 PT).

- `news-before-after-industrial-dark-1440.png`, `news-before-after-clean-light-1440.png`: `main` at `c7bb90c` (before #130) on the left, MOCKUP on the right, at 1440 px.
- `news-before-after-mobile-390.png`: the More tab at 390 px, same commit on the left.

#130 then rendered headlines as catalyst rows, and #134 fixed the panel's button spacing. The current panel is the right side of [`../issue-133/08-news-industrial-dark-1440.png`](../issue-133/08-news-industrial-dark-1440.png), [`../issue-133/08-news-clean-light-1440.png`](../issue-133/08-news-clean-light-1440.png), [`../issue-133/08-news-industrial-dark-390.png`](../issue-133/08-news-industrial-dark-390.png), and [`../issue-133/08-news-clean-light-390.png`](../issue-133/08-news-clean-light-390.png). Those files stay before | after comparisons.

What's real and what's illustrative in the MOCKUP:
- **Real:**
  - the ten "Today" headlines, their titles with the " - Publisher" suffix removed, and their publishers
  - ages relative to the snapshot time
  - every 1-day move, taken from the snapshot's price history with the after-close next-session rule. Headlines published after Oct 7's close show `—` because the Oct 8 session hadn't closed yet.
- **Illustrative:**
  - **every sentiment score and dot color**, because per-headline scores aren't served yet
  - the four "This week" / "Earlier" rows, which show how grouping looks
