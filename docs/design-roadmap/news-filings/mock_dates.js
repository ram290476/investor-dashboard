(() => {
const B = (rows) => rows.map(r => `<li class="news-item">${r[0]?`<span class="news-title">${r[0]}</span>`:''}<p class="news-meta">${r[1]}</p></li>`).join('');
const A = (rows) => rows.map(r => `<li class="dt-row"><span class="dt-tag">${r[0]}</span><span class="dt-body"><span class="dt-title">${r[1]}</span><span class="dt-meta">${r[2]}</span></span><span class="dt-when" title="${r[4]||''}">${r[3]}</span></li>`).join('');
const css = `
.dt-wrap{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px;background:var(--page);width:940px;box-sizing:border-box}
.dt-col{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.dt-col h3{margin:0 0 2px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--text)}
.dt-col .dt-note{margin:0 0 8px;font-size:10.5px;color:var(--amber)}
.dt-sub{margin:10px 0 2px;font-size:10.5px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.dt-col ul{list-style:none;margin:0;padding:0;border-top:1px solid var(--line)}
.dt-row{display:grid;grid-template-columns:44px minmax(0,1fr) auto;gap:8px;padding:6px 0;border-bottom:1px solid var(--line);align-items:center}
.dt-tag{font-family:"IBM Plex Mono",monospace;font-size:10px;padding:2px 4px;border-radius:4px;background:var(--surface-raised);border:1px solid var(--line);color:var(--secondary);text-align:center}
.dt-body{display:grid;gap:1px;min-width:0}
.dt-title{font-size:12px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dt-meta{font-size:10.5px;color:var(--muted)}
.dt-when{font-family:"IBM Plex Mono",monospace;font-size:10.5px;color:var(--secondary);text-align:right;white-space:nowrap}
.dt-col .news-item{padding:6px 0}
.dt-col .news-title{font-size:12px}
`;
const before = `<h3>Before · main today</h3><p class="dt-note">Real Oct 7, 2026 snapshot, as the app renders it</p>
<p class="dt-sub">News & sentiment</p><ul class="news-list">${B([
['Tesla Opens First East Coast Semi Megacharger in Georgia - BASENOR','BASENOR · 2026-10-08T02:06:20+00:00 · neutral'],
['4 charged after pineapple thrown at moving Tesla - WHIO TV','WHIO TV · 2026-10-08T00:18:00+00:00 · neutral'],
['Cooper Financial Group Cuts Stake in Tesla, Inc. $TSLA - MarketBeat','MarketBeat · 2026-10-07T23:44:33+00:00 · neutral']])}</ul>
<p class="dt-sub">Filings</p><ul class="news-list">${B([
['8-K · 8-K','2026-10-02 · earnings'],['4 · OWNERSHIP DOCUMENT','2026-09-09 · insider'],['10-Q · 10-Q','2026-07-23 · periodic']])}</ul>
<p class="dt-sub">Events ("last 14 days")</p><ul class="news-list">${B([
['Falcon 9 Block 5 | Bandwagon 5 (Dedicated Mid-Inclination Rideshare)','2026-10-31T00:00:00Z · launch'],
['Falcon 9 Block 5 | Starlink Group 15-25','2026-10-11T23:00:00Z · launch'],
['Falcon 9 Block 5 | SDA Tranche 1 Transport Layer A','2026-10-10T07:29:00Z · launch'],
['Minutes of the Federal Open Market Committee, September 15-16, 2026','2026-10-07T18:00:00+00:00 · other']])}</ul>`;
const after = `<h3>After · proposed rules</h3><p class="dt-note">Same items, now = snapshot time (Wed Oct 7, 19:15 PT), time zone PT from Profile. Filing titles derived from filing_class and the period in the filename.</p>
<p class="dt-sub">News & sentiment</p><ul>${A([
['TSLA','Tesla Opens First East Coast Semi Megacharger in Georgia','BASENOR','9m','Wed, Oct 7, 2026, 19:06 PT'],
['TSLA','4 charged after pineapple thrown at moving Tesla','WHIO TV','1h','Wed, Oct 7, 2026, 17:18 PT'],
['TSLA','Cooper Financial Group Cuts Stake in Tesla, Inc. $TSLA','MarketBeat','2h','Wed, Oct 7, 2026, 16:44 PT']])}</ul>
<p class="dt-sub">Filings · TSLA</p><ul>${A([
['8-K','Results of operations (Item 2.02)','SEC EDGAR','Oct 2','Filed Fri, Oct 2, 2026'],
['4','Insider transaction','SEC EDGAR','Sep 9','Filed Wed, Sep 9, 2026'],
['10-Q','Quarterly report · period ended Jun 30','SEC EDGAR','Jul 23','Filed Thu, Jul 23, 2026 · period Jun 30, 2026']])}</ul>
<p class="dt-sub">Upcoming</p><ul>${A([
['SPCX','Falcon 9 · SDA Tranche 1 Transport Layer A','Launch · Sat Oct 10 · 00:29 PT','in 3d','Sat, Oct 10, 2026, 00:29 PT'],
['SPCX','Falcon 9 · Starlink Group 15-25','Launch · Sun Oct 11 · 16:00 PT','in 4d','Sun, Oct 11, 2026, 16:00 PT'],
['SPCX','Falcon 9 · Bandwagon 5 (Rideshare)','Launch · Sat Oct 31 · time TBD','in 24d','Sat, Oct 31, 2026 (date only, no time)']])}</ul>
<p class="dt-sub">Past 14 days</p><ul>${A([
['Macro','FOMC minutes (Sep 15–16 meeting)','Policy · Federal Reserve','8h','Wed, Oct 7, 2026, 11:00 PT']])}</ul>`;
document.head.insertAdjacentHTML('beforeend', `<style>${css}</style>`);
document.body.innerHTML = `<div class="dt-wrap" id="dt"><div class="dt-col">${before}</div><div class="dt-col">${after}</div></div>`;
document.body.style.margin='0';
return true;
})()
