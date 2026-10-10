(() => {
const css = `
.mk-banner{margin:0 0 8px;padding:6px 8px;border:1px dashed var(--amber);border-radius:6px;color:var(--amber);font-size:11px;font-weight:600}
.mk-summary-bar{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 10px;align-items:center;margin:6px 0 4px;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--surface-raised)}
.mk-summary-bar .big{font-family:"IBM Plex Mono",monospace;font-size:15px}
.mk-summary-bar .lbl{font-size:11px;color:var(--muted)}
.mk-assoc{grid-column:1/-1;font-size:11px;color:var(--secondary)}
.mk-list{list-style:none;margin:6px 0 0;padding:0;border-top:1px solid var(--line)}
.mk-row{display:grid;grid-template-columns:44px minmax(0,1fr) 62px;gap:4px 8px;padding:7px 0;border-bottom:1px solid var(--line);align-items:start}
.mk-tag{font-family:"IBM Plex Mono",monospace;font-size:10px;padding:2px 4px;border-radius:4px;background:var(--surface-raised);border:1px solid var(--line);color:var(--secondary);text-align:center;margin-top:1px}
.mk-tag.form{color:var(--text);font-weight:600}
.mk-tag.macro{color:var(--muted)}
.mk-body{min-width:0;display:grid;gap:2px}
.mk-title{font-size:12.5px;line-height:1.3;color:var(--text);text-decoration:none;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.mk-meta{font-size:10.5px;color:var(--muted)}
.mk-ai{font-size:11.5px;line-height:1.35;color:var(--secondary)}
.mk-ai b{font-family:"IBM Plex Mono",monospace;font-size:9px;font-weight:600;letter-spacing:.04em;padding:0 4px;margin-right:4px;border-radius:3px;border:1px solid var(--line);color:var(--muted)}
.mk-chip{justify-self:end;font-family:"IBM Plex Mono",monospace;font-size:10.5px;padding:2px 6px;border-radius:999px;border:1px solid currentColor;white-space:nowrap}
.mk-chip.up{color:var(--green)} .mk-chip.down{color:var(--red)} .mk-chip.flat{color:var(--muted)}
.mk-items{display:flex;flex-wrap:wrap;gap:4px}
.mk-items span{font-size:10px;padding:1px 5px;border-radius:4px;background:var(--surface-raised);color:var(--secondary);border:1px solid var(--line)}
.mk-react{grid-column:2/-1;display:grid;grid-template-columns:repeat(4,auto);justify-content:start;gap:2px 14px;margin-top:4px;padding:6px 8px;border-radius:6px;background:var(--surface-raised);font-size:10.5px;color:var(--muted)}
.mk-react span b{display:block;font-family:"IBM Plex Mono",monospace;font-size:11.5px;font-weight:500}
.mk-react .up{color:var(--green)} .mk-react .down{color:var(--red)}
.mk-react .d{grid-column:1/-1;font-size:10px}
.mk-sub{margin:12px 0 2px;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.mk-foot{margin:8px 0 0;font-size:10.5px;color:var(--muted)}
.mk-more{font-size:11px;color:var(--blue);margin-top:6px;display:inline-block}
`;
document.head.insertAdjacentHTML('beforeend', `<style>${css}</style>`);
const chip=(v)=>{if(v==null)return '<span class="mk-chip flat">n/a</span>';const c=v>=0.15?'up':v<=-0.15?'down':'flat';return `<span class="mk-chip ${c}">${v>=0?'+':''}${v.toFixed(2)}</span>`};
const news=[
 {tk:'TSLA',t:'Tesla opens its largest East Coast Semi Megacharger site',src:'Basenor',when:'2h ago · Oct 8, 7:06 PM PT',s:0.34,ai:'Tesla opened a Semi charging site in Georgia; the article gives no capacity or opening-date figures beyond the headline.',react:{pub:'+0.9%',d1:'+1.4%',d3:'−0.6%',note:'Oct 8 7:06 PM PT → next close Oct 9 · TSLA 1D/3D from that close'}},
 {tk:'TSLA',t:'Institutional holder trims Tesla stake in Q3 filing',src:'MarketBeat',when:'5h ago · Oct 8, 4:02 PM PT',s:-0.21,ai:'A fund reported selling part of its Tesla position in its latest 13F; the stake was small relative to shares outstanding.'},
 {tk:'TSLA',t:'Track Mode arrives on non-Performance Model Y',src:'Not a Tesla App',when:'Oct 7, 3:22 PM PT',s:0.08,ai:'A software update adds Track Mode to standard Model Y trims, according to release notes cited in the article.'},
 {tk:'TSLA',t:'Croatian robotaxi startup announces Tesla investment',src:'Dealroom',when:'Oct 7, 2:11 PM PT',s:null,ai:'The headline says Tesla took a stake in robotaxi startup Verne; no deal size is given in the snippet.'},
];
const filings=[
 {form:'8-K',title:'Results of operations (Q3 2026 deliveries release)',when:'Oct 2, 1:30 PM PT',items:['2.02 Results','9.01 Exhibits'],ai:'Tesla furnished its Q3 production and deliveries release as an exhibit; no guidance change is stated in the filing text.'},
 {form:'4',title:'Insider sale · director',when:'Sep 9, 3:00 PM PT',items:['S · 12,000 sh','10b5-1'],ai:'A director sold 12,000 shares under a pre-arranged 10b5-1 trading plan.'},
 {form:'10-Q',title:'Quarterly report for Q2 2026',when:'Jul 23, 2:05 PM PT',items:['Periodic'],ai:'Quarterly report covering Q2 results; summary drawn from the MD&A overview section only.'},
];
const events=[
 {tk:'SPCX',t:'Falcon 9 · Starlink Group 15-25',when:'Oct 11, 4:00 PM PT',type:'Launch'},
 {tk:'Macro',t:'FOMC minutes (Sep 15–16 meeting)',when:'Oct 7, 11:00 AM PT',type:'Policy'},
];
const panels=[...document.querySelectorAll('main section.panel')];
const np=panels.find(p=>/News & sentiment/.test(p.querySelector('h2,summary')?.textContent||''));
const fp=panels.find(p=>/Filings & events/.test(p.querySelector('h2')?.textContent||''));
if(np){
 const body=np.querySelector('.panel-drawer-body')||np;
 [...body.children].forEach((c,i)=>{ if(!c.matches('header,summary,.drawer-header,.panel-header')) c.remove(); });
 body.insertAdjacentHTML('beforeend',`
 <p class="mk-banner">MOCKUP · proposed layout · headlines paraphrased, every AI summary, score, return and correlation is made up</p>
 <div class="mk-summary-bar"><span><span class="big positive">+0.06</span> <span class="lbl">7-day sentiment · 14 articles · ▲0.03 1W</span></span><span class="lbl">TSLA</span>
 <span class="mk-assoc">Daily sentiment vs next-day return, 90 days: <b>r = +0.18</b> (n = 41) · historical association, not a prediction</span></div>
 <ul class="mk-list">${news.map((n,i)=>`<li class="mk-row"><span class="mk-tag">${n.tk}</span><span class="mk-body"><a class="mk-title" href="#">${n.t}</a><span class="mk-meta">${n.src} · ${n.when}</span><span class="mk-ai"><b>AI</b>${n.ai}</span></span>${chip(n.s)}${n.react?`<div class="mk-react"><span>Publish → next close<b class="up">${n.react.pub}</b></span><span>1D<b class="up">${n.react.d1}</b></span><span>3D<b class="down">${n.react.d3}</b></span><span>Score<b class="up">+0.34</b></span><span class="d">${n.react.note} · expanded row</span></div>`:''}</li>`).join('')}</ul>
 <a class="mk-more" href="#">Show 10 more headlines</a>
 <p class="mk-foot">AI summaries: Amazon Bedrock, from the headline and provider snippet only. They can be wrong; open the source. Scores: Alpha Vantage, or a headline word list when that's unavailable.</p>`);
}
if(fp){
 [...fp.children].forEach(c=>{ if(!c.matches('header,.panel-header,.section-header')) c.remove(); });
 fp.insertAdjacentHTML('beforeend',`
 <p class="mk-banner">MOCKUP · filing titles from SEC item codes · every AI summary and all Form 4 details are made up</p>
 <p class="mk-sub">Filings · TSLA</p>
 <ul class="mk-list">${filings.map(f=>`<li class="mk-row"><span class="mk-tag form">${f.form}</span><span class="mk-body"><a class="mk-title" href="#">${f.title}</a><span class="mk-meta">SEC EDGAR · ${f.when}</span><span class="mk-items">${f.items.map(x=>`<span>${x}</span>`).join('')}</span><span class="mk-ai"><b>AI</b>${f.ai}</span></span><span></span></li>`).join('')}</ul>
 <p class="mk-sub">Events · next 7 and last 14 days</p>
 <ul class="mk-list">${events.map(e=>`<li class="mk-row"><span class="mk-tag ${e.tk==='Macro'?'macro':''}">${e.tk}</span><span class="mk-body"><span class="mk-title">${e.t}</span><span class="mk-meta">${e.type} · ${e.when}</span></span><span></span></li>`).join('')}</ul>`);
}
return {np:!!np, fp:!!fp};
})()
