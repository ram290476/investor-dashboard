(async () => {
const { overlayColor } = await import('/theme.js');
const SLOT = { rates: 0, inflation: 1, policy: 2, robotaxi: 3, filings: 4, space: 5, other: 6 };
const LABEL = { rates: 'Rates', inflation: 'Inflation', policy: 'Policy & geopolitics', robotaxi: 'Robotaxi', filings: 'Filings', space: 'Space operations', other: 'Other events' };
const theme = '%THEME%';
document.head.insertAdjacentHTML('beforeend', `<style>
.mk-banner{margin:6px 0 8px;padding:5px 8px;border:1px dashed var(--amber);border-radius:6px;color:var(--amber);font-size:10.5px;font-weight:600;white-space:normal}
.mk-link{flex:none;margin-left:4px;color:var(--muted);font-size:11px;text-decoration:none}
.catalyst-recent-move.neutral{color:var(--muted)}
.mk-more{display:inline-block;margin-top:6px;font-size:11px;color:var(--blue)}
.mk-h{margin:12px 0 2px}
.mk-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(380px,100%),1fr));gap:0 20px}
</style>`);
const row = (r) => {
  const color = overlayColor(SLOT[r.cat], theme);
  const pol = r.move == null ? 'neutral' : r.move > 0 ? 'positive' : r.move < 0 ? 'negative' : 'neutral';
  const mv = r.when ? r.when : r.move == null ? '—' : `${r.move > 0 ? '▲ +' : r.move < 0 ? '▼ ' : ''}${(r.move * 100).toFixed(1)}%`;
  return `<button type="button" class="catalyst-recent-row" title="${r.full || r.title}"><span class="catalyst-recent-date">${r.date}</span><span class="catalyst-recent-title"><span class="catalyst-dot" style="--catalyst-color:${color}" aria-hidden="true"></span><span class="visually-hidden">${LABEL[r.cat]}</span><span class="catalyst-recent-name">${r.title}</span>${r.url ? '<span class="mk-link" aria-hidden="true">↗</span>' : ''}</span><span class="catalyst-recent-move mono ${pol}">${mv}</span></button>`;
};
const list = (rows) => `<div class="catalyst-recent-list">${rows.map(row).join('')}</div>`;
const H = (t) => `<h3 class="signal-label mk-h">${t}</h3>`;
const filingsPast = [
  { date: 'Oct 2', cat: 'filings', title: '8-K · Item 2.02 Results of operations', move: 0.0464, url: 1, full: '8-K · Item 2.02 Results of operations and financial condition · filed Fri, Oct 2, 2026 · SEC EDGAR' },
  { date: 'Sep 29', cat: 'filings', title: '8-K · Item 1.01 Material agreement', move: -0.0126, url: 1 },
  { date: 'Sep 9', cat: 'filings', title: 'Form 4 · Insider transaction', move: -0.0010, url: 1 },
  { date: 'Jul 23', cat: 'filings', title: '10-Q · Quarterly report (period Jun 30)', move: -0.1452, url: 1 },
  { date: 'Jul 22', cat: 'filings', title: '8-K · Item 2.02 Results of operations', move: -0.0130, url: 1 },
];
const tslaEvents = [
  { date: 'Sep 30', cat: 'policy', title: 'SAFE Vehicles Rule III (fuel economy, MY2022–2031)', move: 0.0055, url: 1 },
];
const upcoming = [
  { date: 'Oct 15', cat: 'inflation', title: 'CPI (Sep) release', when: 'in 8d', full: 'CPI (Sep) · Thu Oct 15 · 05:30 PT · in 8 days' },
  { date: 'Oct 21', cat: 'filings', title: 'Tesla Q3 2026 earnings', when: 'in 14d', full: 'Tesla Q3 2026 earnings · Wed Oct 21 · after close · in 14 days' },
  { date: 'Oct 28', cat: 'rates', title: 'FOMC decision', when: 'in 21d', full: 'FOMC decision · Wed Oct 28 · 11:00 PT · in 21 days' },
  { date: 'Oct 30', cat: 'inflation', title: 'PCE (Sep) release', when: 'in 23d', full: 'PCE (Sep) · Fri Oct 30 · 05:30 PT · in 23 days' },
];
const past14 = [
  { date: 'Oct 7', cat: 'rates', title: 'FOMC minutes (Sep 15–16 meeting)', move: -0.0079, url: 1 },
  filingsPast[0],
  tslaEvents[0],
  filingsPast[1],
];
const panels = [...document.querySelectorAll('section.panel')];
const fp = panels.find(p => /Filings & events/.test(p.querySelector('h2')?.textContent || ''));
if (fp) {
  [...fp.children].forEach(c => { if (!c.matches('header,.panel-header,.section-header')) c.remove(); });
  const sub = [...fp.querySelectorAll('p')].find(e => /Latest SEC filings/.test(e.textContent)); if (sub) sub.textContent = 'TSLA filings and company events · newest first';
  fp.insertAdjacentHTML('beforeend', `<p class="mk-banner">MOCKUP · reuses .catalyst-recent-row from Recent catalysts · past rows and 1-day moves are real (Oct 7 snapshot) · titles derived from filing_class · the upcoming row is illustrative</p>
  ${H('Upcoming · TSLA')}${list([upcoming[1]])}
  ${H('SEC filings')}${list(filingsPast)}
  ${H('Company & sector events')}${list(tslaEvents)}<a class="mk-more" href="#">All filings on SEC EDGAR ↗</a>`);
}
const cal = panels.find(p => /Catalyst calendar/.test(p.textContent.slice(0, 80)));
if (cal) {
  const body = cal.querySelector('.panel-drawer-body') || cal;
  [...body.children].forEach(c => { if (!c.matches('header,summary,.drawer-header,.panel-header')) c.remove(); });
  const sum = [...cal.querySelectorAll('*')].find(e => e.children.length === 0 && /upcoming/.test(e.textContent));
  if (sum) sum.textContent = '4 upcoming · next: CPI (Sep) · Oct 15 · in 8d';
  body.insertAdjacentHTML('beforeend', `<p class="mk-banner">MOCKUP · same row component · upcoming rows are illustrative (the Oct 7 snapshot had no upcoming TSLA or macro items) · past rows and moves are real · 6 proclamations hidden as "Other events"</p>
  <div class="mk-grid"><div>${H('Upcoming')}${list(upcoming)}</div><div>${H('Past 14 days')}${list(past14)}<a class="mk-more" href="#">Show 6 other events</a></div></div>`);
}
return { fp: !!fp, cal: !!cal };
})()
