(() => {
document.querySelectorAll('.ticker-research, .ticker-stock-link').forEach(e => e.remove());
const ov = document.querySelector('.price-panel .overview');
const chips = ov && ov.querySelector('.period-chips');
if (!ov || !chips) return 'no overview';
document.head.insertAdjacentHTML('beforeend', `<style>
.mk-actions{display:flex;flex-direction:column;align-items:flex-end;gap:8px;flex:0 1 auto;max-width:100%}
.mk-pagelink{display:inline-flex;align-items:center;gap:6px;min-height:32px;padding:0 12px;border:1px solid var(--line);border-radius:9px;background:var(--surface);color:var(--blue);font-size:13px;font-weight:500;text-decoration:none;white-space:nowrap}
.mk-pagelink:hover,.mk-pagelink:focus-visible{border-color:var(--blue);background:var(--hover)}
.mk-tag{position:absolute;right:16px;top:-10px;font-size:10px;font-weight:600;color:var(--amber);background:var(--surface);padding:0 6px;border:1px dashed var(--amber);border-radius:4px}
@media (max-width:720px){.mk-actions{flex:1 1 100%;align-items:stretch}.mk-pagelink{align-self:flex-end;min-height:44px}}
</style>`);
const box = document.createElement('div'); box.className = 'mk-actions';
const sym = (document.querySelector('.overview-kicker')?.textContent || 'TSLA').split(' ')[0];
box.innerHTML = `<a class="mk-pagelink" href="/ticker/${sym}" aria-label="Open ${sym} research page">Open ${sym} page <span aria-hidden="true">↗</span></a>`;
chips.replaceWith(box); box.append(chips);
ov.style.position = 'relative';
ov.insertAdjacentHTML('beforeend', '<span class="mk-tag">MOCKUP · layout only · real Oct 7 data</span>');
return 'ok';
})()
