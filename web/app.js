/* KubeDoctor web UI — no build step, no dependencies. */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const state = {
  scenarios: [], scenario: 'all-four', data: null,
  selectedId: null, tab: 'diagnosis', rawFile: null, filter: {q: '', sev: 'all'},
  logs: {text: '', result: null, sev: 'all'}, runbooks: null, checks: {}
};

/* ---------- icons ---------- */
const ICONS = {
  grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  search:'<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  terminal:'<path d="M4 17l6-6-6-6M12 19h8"/>',
  book:'<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  shield:'<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  copy:'<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
  download:'<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
  upload:'<path d="M12 15V3M7 8l5-5 5 5M5 21h14"/>',
  check:'<path d="M5 12l5 5L20 7"/>',
  alert:'<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
  play:'<path d="M7 4l13 8-13 8z"/>',
  command:'<path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  print:'<path d="M6 9V3h12v6M6 18H4v-7h16v7h-2"/><rect x="6" y="14" width="12" height="7"/>',
  file:'<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  lock:'<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  eye:'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  x:'<path d="M6 6l12 12M18 6L6 18"/>'
};
const icon = n => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n] || ''}</svg>`;
const hydrateIcons = (root = document) => $$('[data-icon]', root).forEach(el => { if (!el.firstChild) el.innerHTML = icon(el.dataset.icon); });

/* ---------- helpers ---------- */
async function api(path, opts = {}) {
  const r = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...opts});
  let j = {};
  try { j = await r.json(); } catch (_) { /* non-JSON body */ }
  if (!r.ok) throw new Error(j.error || `Request failed (${r.status})`);
  return j;
}
let toastTimer;
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}
async function copyText(text, msg = 'Copied to clipboard') {
  try { await navigator.clipboard.writeText(text); }
  catch (_) {
    const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (__) { /* ignore */ } ta.remove();
  }
  toast(msg);
}
function download(name, text, type = 'text/plain') {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], {type: type + ';charset=utf-8'}));
  a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 500);
}
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* storage unavailable */ } }
};
const sevLabel = s => s === 'critical' ? 'Critical' : s === 'warning' ? 'Needs attention' : 'Healthy';
const pill = (cls, text) => `<span class="pill ${cls}">${esc(text)}</span>`;
const shortName = w => String(w || '').split('/').pop();
const pct = score => { const [a, b] = String(score).split('/').map(Number); return b ? Math.round(a / b * 100) : 0; };
const cur = () => state.data ? state.data.incidents.find(i => i.id === state.selectedId) || state.data.incidents[0] : null;
const scenarioTitle = id => (state.scenarios.find(s => s.id === id) || {}).title || id;
const byPriority = list => [...list].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === 'critical' ? -1 : 1));
function copyBtn(text, label = 'Copy') { return `<button class="copy" data-copy="${esc(text)}">${icon('copy')}${label}</button>`; }

/* ---------- data ---------- */
async function loadData(id = state.scenario, keepSelection = false) {
  state.scenario = id;
  $('#scenario-select').value = id;
  try {
    state.data = await api(`/api/overview?scenario=${encodeURIComponent(id)}`);
  } catch (e) {
    $('#view').innerHTML = `<div class="card empty"><b>Could not load this snapshot</b>${esc(e.message)}</div>`;
    return;
  }
  if (!keepSelection || !state.data.incidents.some(i => i.id === state.selectedId)) {
    state.selectedId = (byPriority(state.data.incidents)[0] || {}).id || null;
  }
  const n = $('#nav-count'); n.textContent = state.data.incidents.length; n.hidden = !state.data.incidents.length;
  render();
}

async function runInvestigation() {
  const btn = $('#run-btn'); btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:16px;height:16px;border-width:2px"></span>Investigating…';
  try {
    const r = await api('/api/diagnose', {method: 'POST', body: JSON.stringify({scenario: state.scenario})});
    await loadData(state.scenario);
    toast(`Investigation complete: ${r.incidents.length} finding${r.incidents.length === 1 ? '' : 's'} saved to History`);
    if (r.incidents.length) location.hash = '#/investigations';
  } catch (e) { toast(e.message); }
  finally { btn.disabled = false; btn.innerHTML = icon('play') + 'Run investigation'; }
}

/* ---------- routing ---------- */
const PAGES = {overview: pageOverview, investigations: pageInvestigations, logs: pageLogs, runbooks: pageRunbooks, history: pageHistory, safety: pageSafety};
function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  const page = PAGES[parts[0]] ? parts[0] : 'overview';
  if (page === 'investigations' && parts[1]) state.selectedId = decodeURIComponent(parts[1]);
  return page;
}
function render() {
  if (!state.data) return;
  const page = route();
  $$('.nav-item').forEach(a => { const on = a.dataset.page === page; a.classList.toggle('active', on); on ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current'); });
  $('.sidebar').classList.remove('open');
  document.title = `${{overview:'Overview',investigations:'Investigations',logs:'Log analyzer',runbooks:'Runbooks',history:'History',safety:'Safety & setup'}[page]} · KubeDoctor`;
  PAGES[page]();
  hydrateIcons($('#view'));
}
function head(title, desc, actions = '') {
  return `<div class="page-head"><div><h1>${title}</h1><p>${desc}</p></div><div class="head-actions">${actions}</div></div>`;
}

/* ---------- overview ---------- */
function pageOverview() {
  const d = state.data, inc = d.incidents, pods = d.pods || [];
  const incByApp = Object.fromEntries(inc.map(i => [shortName(i.workload), i]));
  const total = pods.length || d.podsSeen, bad = inc.length, healthy = Math.max(0, total - bad);
  const crit = inc.filter(i => i.severity === 'critical').length, warn = bad - crit;
  const frac = total ? healthy / total : 1;
  const ringColor = bad === 0 ? 'var(--ok)' : crit ? 'var(--crit)' : '#e29a1a';
  const C = 2 * Math.PI * 54;
  const headline = bad === 0 ? 'Every workload is healthy' : `${bad} of ${total} workload${total === 1 ? '' : 's'} need${bad === 1 ? 's' : ''} attention`;
  const sub = bad === 0
    ? `All ${total} pods in ${esc(d.namespace)} are running normally.`
    : `${esc(d.title)} · namespace ${esc(d.namespace)}. KubeDoctor ranked the likely cause of each failure and drafted a fix you can review.`;

  const symptoms = {};
  inc.forEach(i => { (symptoms[i.symptom] = symptoms[i.symptom] || {n: 0, sev: i.severity}).n++; });
  const maxN = Math.max(1, ...Object.values(symptoms).map(s => s.n));
  const dismissed = store.get('kd-start-dismissed') === '1';
  const first = byPriority(inc)[0];

  const tiles = (pods.length ? pods : inc.map(i => ({app: shortName(i.workload), reason: i.symptom, restarts: 0, phase: ''}))).map(p => {
    const i = incByApp[p.app];
    const cls = i ? i.severity : 'ok';
    const label = i ? i.symptom : 'Healthy';
    const detail = p.restarts ? `${p.restarts} restart${p.restarts === 1 ? '' : 's'}` : (i ? 'Not running' : 'Running');
    return `<${i ? 'button' : 'div'} class="tile ${cls}${i ? '' : ' static'}" ${i ? `data-open="${esc(i.id)}"` : ''}>
      <b>${esc(p.app)}</b><span class="state ${i ? '' : 'ok'}">${esc(label)}</span><small>${esc(detail)}${i ? ' · View diagnosis' : ''}</small></${i ? 'button' : 'div'}>`;
  }).join('');

  const rows = byPriority(inc).map(i => `<tr class="click" data-open="${esc(i.id)}" tabindex="0">
      <td><div class="wl">${esc(shortName(i.workload))}</div><div class="sub">${esc(i.namespace)}</div></td>
      <td>${esc(i.symptom)}</td><td class="cause">${esc(i.cause)}</td><td>${pill(i.severity, sevLabel(i.severity))}</td><td>${esc(i.confidence)}</td></tr>`).join('');

  const queue = byPriority(inc).map((i, n) => `<li><span class="n">${n + 1}</span>
      <div><b>${esc(shortName(i.workload))}: ${esc(i.fixSummary)}</b>
      ${i.changes.length ? i.changes.map(c => `<div class="sub">${esc(c.field)}: <code>${esc(c.before)}</code> → <code>${esc(c.after)}</code></div>`).join('') : '<div class="sub">Needs a manual change. Open the diagnosis for steps.</div>'}</div>
      <button class="btn sm" data-open="${esc(i.id)}">Review</button></li>`).join('');

  $('#view').innerHTML = `
    ${head('Overview', `Cluster health for <b>${esc(d.namespace)}</b> at a glance, with the fix order that clears the most risk first.`,
      `<button class="btn" id="report-btn">${icon('download')}Download report</button>`)}
    ${dismissed ? '' : `<div class="card start" id="start">
      <div><b>1. Pick a snapshot</b><span>Use the selector at the top to choose which recorded workload set to inspect.</span></div>
      <div><b>2. Run investigation</b><span>KubeDoctor reads pod state, events and logs, then ranks the likely cause.</span></div>
      <div><b>3. Review the fix</b><span>Open a finding to see the evidence and a ready-to-use patch. Nothing is applied for you.</span>
      <button class="x" id="dismiss-start" aria-label="Dismiss">${icon('x')}</button></div></div>`}
    <section class="card hero">
      <div><h1>${headline}</h1><p>${sub}</p>
        <div class="hero-stats"><div><b>${total}</b><span>Pods checked</span></div><div><b style="color:var(--crit)">${crit}</b><span>Critical</span></div>
        <div><b style="color:var(--warn)">${warn}</b><span>Needs attention</span></div><div><b style="color:var(--ok)">${healthy}</b><span>Healthy</span></div></div></div>
      <div class="ring" role="img" aria-label="${healthy} of ${total} pods healthy"><svg viewBox="0 0 132 132"><circle class="track" cx="66" cy="66" r="54"/>
        <circle class="arc" cx="66" cy="66" r="54" stroke="${ringColor}" stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${(C * (1 - frac)).toFixed(1)}"/></svg>
        <div class="ring-n"><b>${healthy}/${total}</b><small>healthy</small></div></div>
    </section>
    <h2 style="font-size:16px;margin:0 0 12px">Workloads</h2>
    <div class="tiles" style="margin-bottom:22px">${tiles || '<div class="card empty">No pods found in this snapshot.</div>'}</div>
    <div class="grid-2 wide">
      <section class="card"><div class="card-head"><div><h2>Findings</h2><small>Most urgent first</small></div><a href="#/investigations" class="btn sm">Open investigations</a></div>
        <div class="table-wrap"><table><thead><tr><th>Workload</th><th>State</th><th>Likely cause</th><th>Severity</th><th>Confidence</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="5"><div class="empty"><b>No issues found</b>All workloads in this snapshot are healthy.</div></td></tr>'}</tbody></table></div></section>
      <div class="col"><section class="card"><div class="card-head"><div><h2>Failure types</h2><small>Grouped by observed state</small></div></div>
        <div class="card-body">${Object.entries(symptoms).map(([k, v]) => `<div class="bar-row"><span>${esc(k)}</span><div class="bar"><i class="${v.sev}" style="width:${v.n / maxN * 100}%"></i></div><b>${v.n}</b></div>`).join('') || '<p class="sub">Nothing to group.</p>'}</div>
        ${first ? `<div class="next"><small>Start here</small><b>${esc(shortName(first.workload))}</b><p>${esc(first.cause)}</p><button class="btn primary sm" data-open="${esc(first.id)}">Open diagnosis</button></div>` : ''}</section></div>
    </div>
    ${inc.length ? `<section class="card"><div class="card-head"><div><h2>Fix queue</h2><small>Suggested order: critical failures first</small></div></div><ol class="queue">${queue}</ol></section>` : ''}`;

  $('#report-btn').onclick = () => { location.href = `/api/report?scenario=${encodeURIComponent(state.scenario)}`; };
  const dm = $('#dismiss-start'); if (dm) dm.onclick = () => { store.set('kd-start-dismissed', '1'); $('#start').remove(); };
}

/* ---------- investigations ---------- */
function pageInvestigations() {
  const d = state.data, list = byPriority(d.incidents);
  const f = state.filter;
  const shown = list.filter(i => (f.sev === 'all' || i.severity === f.sev) && `${i.workload} ${i.symptom} ${i.cause}`.toLowerCase().includes(f.q.toLowerCase()));
  const sel = cur();
  $('#view').innerHTML = head('Investigations', 'Each finding shows what is wrong, the evidence behind it, how it was checked, and the fix to review.',
    `<button class="btn" id="json-btn">${icon('file')}Export JSON</button><button class="btn" id="print-btn">${icon('print')}Print</button><button class="btn" id="report-btn">${icon('download')}Report (.md)</button>`) +
    (list.length ? `<div class="split"><div>
      <div class="filters"><input type="search" id="q" placeholder="Search workloads or causes" value="${esc(f.q)}" aria-label="Search findings">
        <select id="sev" aria-label="Filter by severity"><option value="all">All severities</option><option value="critical">Critical</option><option value="warning">Needs attention</option></select></div>
      <div class="list" id="list">${listItems(shown, sel)}</div></div>
      <div id="detail">${sel ? detail(sel) : ''}</div></div>`
      : `<div class="card empty"><b>No findings in this snapshot</b>Every workload is healthy. Choose another snapshot at the top to investigate failures.</div>`);
  $('#sev').value = f.sev;
  $('#report-btn').onclick = () => { location.href = `/api/report?scenario=${encodeURIComponent(state.scenario)}`; };
  $('#json-btn').onclick = () => download(`kubedoctor-${state.scenario}.json`, JSON.stringify(state.data, null, 2), 'application/json');
  $('#print-btn').onclick = () => window.print();
  if (!list.length) return;
  $('#q').oninput = e => { f.q = e.target.value; refreshList(); };
  $('#sev').onchange = e => { f.sev = e.target.value; refreshList(); };
}
function listItems(items, sel) {
  return items.map(i => `<button class="item ${i.severity}" data-select="${esc(i.id)}" aria-current="${sel && sel.id === i.id}">
    <div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><b>${esc(shortName(i.workload))}</b>${pill(i.severity, sevLabel(i.severity))}</div>
    <div>${esc(i.symptom)}</div><div class="sub">${esc(i.cause)}</div></button>`).join('') || '<div class="card empty">No findings match your search.</div>';
}
function refreshList() {
  const f = state.filter;
  const shown = state.data.incidents.filter(i => (f.sev === 'all' || i.severity === f.sev) && `${i.workload} ${i.symptom} ${i.cause}`.toLowerCase().includes(f.q.toLowerCase()));
  $('#list').innerHTML = listItems(byPriority(shown), cur());
}
function selectIncident(id) {
  state.selectedId = id; state.tab = 'diagnosis'; state.rawFile = null;
  history.replaceState(null, '', `#/investigations/${encodeURIComponent(id)}`);
  refreshList(); $('#detail').innerHTML = detail(cur()); hydrateIcons($('#detail'));
}
const TABS = [['diagnosis', 'Diagnosis'], ['evidence', 'Raw evidence'], ['path', 'How it was checked'], ['fix', 'Fix']];
function detail(i) {
  return `<section class="card">
    <div class="detail-top">
      ${pill(i.severity, i.symptom)}
      <h2>${esc(i.cause)}</h2>
      <div class="meta"><span>Workload <b>${esc(shortName(i.workload))}</b></span><span>Pod <b>${esc(i.pod)}</b></span><span>Namespace <b>${esc(i.namespace)}</b></span></div>
      <div class="conf"><span>Confidence <b>${esc(i.confidence)}</b></span><span class="bar" title="${esc(i.score)} signals matched"><i style="width:${pct(i.score)}%"></i></span><span>${esc(i.score)} signals matched</span></div>
    </div>
    <div class="tabs" role="tablist">${TABS.map(([k, l]) => `<button class="tab" role="tab" data-tab="${k}" aria-selected="${state.tab === k}">${l}</button>`).join('')}</div>
    <div class="panel tabpanel" role="tabpanel">${tabBody(i)}</div></section>`;
}
function tabBody(i) {
  if (state.tab === 'evidence') return evidenceTab(i);
  if (state.tab === 'path') return pathTab(i);
  if (state.tab === 'fix') return fixTab(i);
  return diagnosisTab(i);
}
function diagnosisTab(i) {
  return `<h3>What is happening</h3><p>${esc(i.explanation)}</p>
    ${i.why ? `<h3>Common reasons</h3><p>${esc(i.why)}</p>` : ''}
    <h3>Evidence that points to this cause</h3>
    ${i.evidence.length ? i.evidence.map(e => `<div class="signal"><span class="tick">${icon('check')}</span><div><b>${esc(e.label)}<span class="src">${esc(e.source)}</span></b>${e.line ? `<code>${esc(e.line)}</code>` : ''}</div></div>`).join('') : '<p>No strong matching evidence was collected.</p>'}
    ${i.missing.length ? `<div class="callout">${icon('alert')} Some evidence could not be collected, so treat this diagnosis with care: ${i.missing.map(esc).join(' ')}</div>` : ''}
    ${i.learn ? `<h3>Good to know</h3><div class="callout info">${esc(i.learn)}</div>` : ''}
    <div style="margin-top:18px"><button class="btn primary" data-tab="fix">See the fix</button></div>`;
}
function evidenceTab(i) {
  const files = Object.entries(i.evidenceFiles || {}).filter(([, v]) => String(v).trim());
  if (!files.length) return '<p>No raw evidence was captured for this workload.</p>';
  if (!state.rawFile || !files.some(([k]) => k === state.rawFile)) state.rawFile = files[0][0];
  const text = i.evidenceFiles[state.rawFile];
  const names = {pod: 'Pod status', describe: 'kubectl describe', events: 'Events', logs: 'Previous logs', nodes: 'Nodes'};
  return `<p>The exact output KubeDoctor read from the cluster. Secrets are masked.</p>
    <div class="sub-tabs">${files.map(([k]) => `<button class="chip" data-raw="${esc(k)}" aria-pressed="${k === state.rawFile}">${esc(names[k] || k)}</button>`).join('')}</div>
    <div class="codebox"><div class="tools">${esc(names[state.rawFile] || state.rawFile)}${copyBtn(text)}</div><pre>${esc(text)}</pre></div>`;
}
function pathTab(i) {
  return `<p>Every command below is read-only. KubeDoctor chose each follow-up based on what the previous step returned.</p>
    <ol class="steps" style="margin-top:16px">
    ${i.steps.map((s, n) => `<li><span class="n">${n + 1}</span><div><b>${esc(s.why)}</b><div class="cmd"><span>${esc(s.command)}</span>${copyBtn(s.command)}</div>${s.exitCode !== 0 ? '<div class="sub">This command did not return data.</div>' : ''}</div></li>`).join('')}
    ${i.skipped.map(s => `<li><span class="n skip">–</span><div><b>${esc(s.check)} skipped</b><div class="sub">${esc(s.reason)}</div></div></li>`).join('')}
    </ol>`;
}
function fixTab(i) {
  const hdr = `# Proposed fix for deployment/${i.deployment || shortName(i.workload)} in namespace ${i.namespace}\n# Review before applying:\n# ${i.patchCommand}\n`;
  const checks = state.checks[i.id] || (state.checks[i.id] = {});
  return `<h3>Recommended change</h3><p>${esc(i.fixSummary)}</p>
    ${i.changes.length ? `<div class="diff">${i.changes.map(c => `<div><div class="f">${esc(c.field)}</div><div class="b">${esc(c.before)}</div><div class="a">${esc(c.after)}</div></div>`).join('')}</div>` : ''}
    ${i.patchYaml ? `<h3>Patch file</h3>
      <div class="codebox"><div class="tools">${esc(i.deployment || shortName(i.workload))}-fix.yaml${copyBtn(i.patchYaml, 'Copy YAML')}</div><pre>${esc(i.patchYaml)}</pre></div>
      <h3>Apply it yourself</h3><p>Save the file, check it, then run:</p>
      <div class="cmd"><span>${esc(i.patchCommand)}</span>${copyBtn(i.patchCommand)}</div>
      <div style="margin-top:10px"><button class="btn" data-dl-patch="${esc(i.id)}">${icon('download')}Download patch file</button></div>
      <div class="callout info" style="margin-top:16px">KubeDoctor only drafts the change. Applying it is always your decision, and the CLI asks for explicit approval first.</div>` : ''}
    ${i.manualSteps.length ? `<h3>Checklist</h3><ul class="checklist">${i.manualSteps.map((s, n) => `<li><input type="checkbox" id="chk-${n}" data-check="${n}" ${checks[n] ? 'checked' : ''}><label for="chk-${n}">${esc(s)}</label></li>`).join('')}</ul>` : ''}`;
}

/* ---------- log analyzer ---------- */
function pageLogs() {
  const L = state.logs, withLogs = state.data.incidents.filter(i => (i.evidenceFiles || {}).logs);
  $('#view').innerHTML = head('Log analyzer', 'Paste or drop a log excerpt. KubeDoctor highlights errors and warnings and groups repeated messages so the important lines stand out.') +
    `<div class="grid-2"><section class="card drop" id="drop"><div class="card-head"><div><h2>Logs</h2><small>Up to 1.5 MB. Analysis runs on your machine.</small></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        ${withLogs.length ? `<select id="from-finding" class="sel-sm" aria-label="Load logs from a finding"><option value="">Load from a finding…</option>${withLogs.map(i => `<option value="${esc(i.id)}">${esc(shortName(i.workload))}: previous container logs</option>`).join('')}</select>` : ''}
        <label class="btn sm" for="log-file" style="cursor:pointer">${icon('upload')}Upload file</label><input id="log-file" type="file" accept=".log,.txt,.out,text/plain" hidden></div></div>
      <div class="card-body"><textarea class="log-in" id="log-text" placeholder="Paste application or Kubernetes logs here, or drop a .log file onto this card" aria-label="Log text">${esc(L.text)}</textarea>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px"><button class="btn" id="log-clear">Clear</button><button class="btn primary" id="analyze">Analyze logs</button></div></div></section>
    <section class="card"><div class="card-head"><h2>Summary</h2></div><div class="card-body" id="log-summary">${L.result ? logSummary(L.result) : '<div class="empty" style="padding:24px 0"><b>No analysis yet</b>Add some logs and choose Analyze logs.</div>'}</div></section></div>
    ${L.result ? `<section class="card"><div class="card-head"><div><h2>Lines that need a look</h2><small>${L.result.findings.length} shown</small></div>
      <div class="sub-tabs" style="margin:0">${['all', 'critical', 'error', 'warning'].map(s => `<button class="chip" data-logsev="${s}" aria-pressed="${L.sev === s}">${s[0].toUpperCase() + s.slice(1)}</button>`).join('')}</div></div>
      <div id="findings">${findingRows(L.result.findings, L.sev)}</div></section>` : ''}`;
  const ta = $('#log-text'); ta.oninput = () => { L.text = ta.value; };
  $('#log-clear').onclick = () => { L.text = ''; L.result = null; pageLogs(); hydrateIcons($('#view')); };
  $('#analyze').onclick = analyzeLogs;
  $('#log-file').onchange = async e => { const f = e.target.files[0]; if (f) { L.text = await f.text(); toast(`Loaded ${f.name}`); pageLogs(); hydrateIcons($('#view')); } };
  const ff = $('#from-finding'); if (ff) ff.onchange = e => { const i = state.data.incidents.find(x => x.id === e.target.value); if (i) { L.text = i.evidenceFiles.logs; analyzeLogs(); } };
  const drop = $('#drop');
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', async e => { const f = e.dataTransfer.files[0]; if (f) { L.text = await f.text(); toast(`Loaded ${f.name}`); pageLogs(); hydrateIcons($('#view')); } });
}
function logSummary(r) {
  return `<div class="stat-row"><div class="stat"><b>${r.lines}</b><span>Lines scanned</span></div><div class="stat"><b style="color:var(--crit)">${r.counts.critical + r.counts.error}</b><span>Errors</span></div><div class="stat"><b style="color:var(--warn)">${r.counts.warning}</b><span>Warnings</span></div></div>
    ${r.repeated.length ? `<h3 style="font-size:15px;margin-bottom:8px">Repeated messages</h3>${r.repeated.slice(0, 6).map(x => `<div style="display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid var(--line)"><code style="font-size:12.5px;overflow-wrap:anywhere">${esc(x.pattern)}</code><span class="pill neutral">${x.count}×</span></div>`).join('')}` : ''}
    <div class="callout info" style="margin-bottom:0">${esc(r.note)}</div>`;
}
function findingRows(rows, sev) {
  const shown = rows.filter(f => sev === 'all' || f.severity === sev);
  return shown.length ? shown.map(f => `<div class="finding ${esc(f.severity)}"><span class="ln">:${f.line}</span>${pill(f.severity === 'warning' ? 'warning' : f.severity === 'critical' ? 'critical' : 'neutral', f.severity[0].toUpperCase() + f.severity.slice(1))}<span class="tx">${esc(f.text)}</span></div>`).join('')
    : '<div class="empty"><b>Nothing matched</b>No lines match this filter.</div>';
}
async function analyzeLogs() {
  const L = state.logs;
  if (!L.text.trim()) return toast('Add some log text first.');
  const b = $('#analyze'); if (b) { b.disabled = true; b.textContent = 'Analyzing…'; }
  try { L.result = await api('/api/analyze-logs', {method: 'POST', body: JSON.stringify({text: L.text})}); L.sev = 'all'; }
  catch (e) { toast(e.message); }
  pageLogs(); hydrateIcons($('#view'));
}

/* ---------- runbooks ---------- */
async function pageRunbooks() {
  if (!state.runbooks) {
    $('#view').innerHTML = '<div class="loading"><span class="spinner"></span>Loading runbooks…</div>';
    try { state.runbooks = await api('/api/runbooks'); } catch (e) { $('#view').innerHTML = `<div class="card empty"><b>Could not load runbooks</b>${esc(e.message)}</div>`; return; }
  }
  $('#view').innerHTML = head('Runbooks', `The knowledge base behind every diagnosis: ${state.runbooks.length} failure patterns, each with the signals KubeDoctor looks for and the fix it suggests. Edit <code>runbooks/runbooks.json</code> to teach it something new.`,
    '<input type="search" id="rb-q" placeholder="Search failure patterns" aria-label="Search runbooks" style="min-width:260px">') + '<div class="rb-grid" id="rb-grid"></div>';
  const draw = q => {
    const items = state.runbooks.filter(r => `${r.title} ${r.symptoms.join(' ')} ${r.explanation}`.toLowerCase().includes(q.toLowerCase()));
    $('#rb-grid').innerHTML = items.map(r => `<article class="card rb"><div class="tags">${r.symptoms.map(s => pill('neutral', s)).join('') || pill('neutral', 'Any failing pod')}${r.fallback ? pill('info', 'Fallback') : ''}</div>
      <h3>${esc(r.title)}</h3><p>${esc(r.explanation)}</p>
      <details><summary>Signals it looks for (${r.signals.length})</summary><ul>${r.signals.map(s => `<li>${esc(s.label)} <span class="src">${esc(s.source)}</span></li>`).join('')}</ul></details>
      <details><summary>Suggested fix</summary><p style="margin-top:8px">${esc(r.fix || 'Guidance only. Follow the manual steps in the diagnosis.')}</p></details></article>`).join('') || '<div class="card empty" style="grid-column:1/-1"><b>No matching runbooks</b>Try a different search.</div>';
  };
  draw(''); $('#rb-q').oninput = e => draw(e.target.value);
}

/* ---------- history ---------- */
async function pageHistory() {
  $('#view').innerHTML = '<div class="loading"><span class="spinner"></span>Loading history…</div>';
  let rows = [];
  try { rows = await api('/api/history'); } catch (e) { toast(e.message); }
  $('#view').innerHTML = head('History', 'Every investigation you run is saved here so you can reopen it or download its report.',
    rows.length ? `<button class="btn" id="clear-hist">Clear history</button>` : '') +
    `<section class="card">${rows.map(r => `<div class="hist"><span class="ic-wrap">${icon('check')}</span>
      <div><b>${esc(scenarioTitle(r.scenario))}</b><div class="sub">${r.count} finding${r.count === 1 ? '' : 's'} · ${esc((r.workloads || []).map(shortName).join(', ') || 'No issues')}</div></div>
      <span class="sub">${esc(new Date(r.when).toLocaleString())}</span>
      <div style="display:flex;gap:6px"><button class="btn sm" data-reopen="${esc(r.scenario)}">Open</button><a class="btn sm" href="/api/report?scenario=${encodeURIComponent(r.scenario)}">${icon('download')}Report</a></div></div>`).join('') ||
      '<div class="empty"><b>No investigations yet</b>Choose a snapshot and select Run investigation. Results will be saved here.</div>'}</section>`;
  const c = $('#clear-hist'); if (c) c.onclick = async () => { if (confirm('Clear all saved investigations?')) { await fetch('/api/history', {method: 'DELETE'}); toast('History cleared'); pageHistory(); } };
}

/* ---------- safety & setup ---------- */
async function pageSafety() {
  let doc = '';
  try { doc = (await api('/api/safety')).text; } catch (_) { /* optional */ }
  const cmds = [
    ['Create a throwaway cluster', 'kind create cluster --name triage-lab'],
    ['Deploy the example workloads', 'kubectl apply -f workloads/'],
    ['Diagnose one workload (read-only)', 'python triage.py --live --deployment crashy-api --diagnose-only'],
    ['Clean up', 'kind delete cluster --name triage-lab']
  ];
  $('#view').innerHTML = head('Safety & setup', 'What KubeDoctor will and will not do, and how to point it at a real cluster from the command line.') +
    `<div class="guar">
      <div class="card"><span class="ic-wrap">${icon('eye')}</span><h3>Read-only investigation</h3><p>Only <code>get</code>, <code>describe</code>, <code>logs</code> and <code>events</code> are allowed. Anything else is blocked before it runs.</p></div>
      <div class="card"><span class="ic-wrap">${icon('lock')}</span><h3>You approve every change</h3><p>The browser never edits a cluster. The CLI runs a server-side dry run, then asks for an explicit <code>yes</code>.</p></div>
      <div class="card"><span class="ic-wrap">${icon('shield')}</span><h3>Scoped by default</h3><p>Live mode works on one named workload, in a <code>triage-*</code> namespace, on a recognised local cluster context.</p></div>
      <div class="card"><span class="ic-wrap">${icon('file')}</span><h3>Secrets are masked</h3><p>Tokens and passwords are redacted from evidence, reports and logs before they are shown or saved.</p></div></div>
    <section class="card" style="margin-bottom:18px"><div class="card-head"><div><h2>Run it against a real cluster</h2><small>Use a disposable local cluster such as Kind, Minikube, Docker Desktop or k3d</small></div></div>
      <div class="card-body">${cmds.map(([l, c]) => `<div style="margin-bottom:12px"><b>${l}</b><div class="cmd"><span>${esc(c)}</span>${copyBtn(c)}</div></div>`).join('')}
      <p class="sub">The browser stays in replay mode and never receives kubeconfig credentials. Live diagnosis is a command-line feature.</p></div></section>
    <section class="card"><div class="card-head"><div><h2>Safety guide</h2><small>Full text of SAFETY.md</small></div></div><div class="card-body">${doc ? `<div class="doc">${esc(doc)}</div>` : '<p class="sub">SAFETY.md was not found next to the application.</p>'}</div></section>`;
}

/* ---------- command palette ---------- */
let palIdx = 0, palItems = [];
function paletteSource() {
  const pages = [['overview', 'Overview'], ['investigations', 'Investigations'], ['logs', 'Log analyzer'], ['runbooks', 'Runbooks'], ['history', 'History'], ['safety', 'Safety & setup']]
    .map(([k, l]) => ({label: l, hint: 'Page', run: () => { location.hash = '#/' + k; }}));
  const wl = (state.data ? state.data.incidents : []).map(i => ({label: `${shortName(i.workload)}: ${i.symptom}`, hint: 'Finding', run: () => { state.selectedId = i.id; state.tab = 'diagnosis'; location.hash = '#/investigations/' + encodeURIComponent(i.id); }}));
  const sc = state.scenarios.map(s => ({label: `Switch to ${s.title}`, hint: 'Snapshot', run: () => loadData(s.id)}));
  return [...pages, ...wl, ...sc];
}
function openPalette() {
  $('#palette').hidden = false; const inp = $('#palette-input'); inp.value = ''; drawPalette(''); inp.focus();
}
function closePalette() { $('#palette').hidden = true; }
function drawPalette(q) {
  palItems = paletteSource().filter(x => x.label.toLowerCase().includes(q.toLowerCase())); palIdx = 0;
  $('#palette-list').innerHTML = palItems.map((x, n) => `<li role="option" data-pi="${n}" aria-selected="${n === 0}">${esc(x.label)}<small>${x.hint}</small></li>`).join('') || '<li style="cursor:default;color:var(--muted)">No matches</li>';
}
function movePalette(d) {
  if (!palItems.length) return; palIdx = (palIdx + d + palItems.length) % palItems.length;
  $$('#palette-list li').forEach((li, n) => li.setAttribute('aria-selected', n === palIdx));
  const el = $$('#palette-list li')[palIdx]; if (el) el.scrollIntoView({block: 'nearest'});
}
function runPalette(n) { const it = palItems[n]; if (it) { closePalette(); it.run(); } }

/* ---------- events ---------- */
document.addEventListener('click', e => {
  const t = e.target;
  const open = t.closest('[data-open]');
  if (open) { state.selectedId = open.dataset.open; state.tab = 'diagnosis'; state.rawFile = null; location.hash = '#/investigations/' + encodeURIComponent(open.dataset.open); return; }
  const sel = t.closest('[data-select]'); if (sel) return selectIncident(sel.dataset.select);
  const tab = t.closest('[data-tab]');
  if (tab) { state.tab = tab.dataset.tab; $('#detail').innerHTML = detail(cur()); hydrateIcons($('#detail')); return; }
  const raw = t.closest('[data-raw]'); if (raw) { state.rawFile = raw.dataset.raw; $('#detail').innerHTML = detail(cur()); hydrateIcons($('#detail')); return; }
  const cp = t.closest('[data-copy]'); if (cp) return copyText(cp.dataset.copy);
  const dl = t.closest('[data-dl-patch]');
  if (dl) { const i = state.data.incidents.find(x => x.id === dl.dataset.dlPatch); const hdr = `# Proposed fix for deployment/${i.deployment || shortName(i.workload)} in namespace ${i.namespace}\n# Review before applying:\n# ${i.patchCommand}\n`; return download(`${i.deployment || shortName(i.workload)}-fix.yaml`, hdr + i.patchYaml + '\n', 'text/yaml'); }
  const ls = t.closest('[data-logsev]'); if (ls) { state.logs.sev = ls.dataset.logsev; pageLogs(); hydrateIcons($('#view')); return; }
  const ro = t.closest('[data-reopen]'); if (ro) { loadData(ro.dataset.reopen).then(() => { location.hash = '#/investigations'; }); return; }
  const pi = t.closest('[data-pi]'); if (pi) return runPalette(+pi.dataset.pi);
  if (t.id === 'palette') closePalette();
});
document.addEventListener('change', e => {
  const c = e.target.closest('[data-check]'); if (c) { const i = cur(); (state.checks[i.id] = state.checks[i.id] || {})[c.dataset.check] = c.checked; }
});
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#palette').hidden ? openPalette() : closePalette(); return; }
  if (!$('#palette').hidden) {
    if (e.key === 'Escape') closePalette();
    else if (e.key === 'ArrowDown') { e.preventDefault(); movePalette(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); movePalette(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); runPalette(palIdx); }
    return;
  }
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('tr.click')) { e.preventDefault(); e.target.click(); }
});
$('#palette-input').addEventListener('input', e => drawPalette(e.target.value));
$('#palette-btn').onclick = openPalette;
$('#run-btn').onclick = runInvestigation;
$('#menu-btn').onclick = () => $('.sidebar').classList.toggle('open');
$('#scenario-select').onchange = e => loadData(e.target.value);
window.addEventListener('hashchange', render);

/* ---------- boot ---------- */
(async function boot() {
  hydrateIcons();
  try {
    state.scenarios = await api('/api/scenarios');
    $('#scenario-select').innerHTML = state.scenarios.map(s => `<option value="${esc(s.id)}">${esc(s.title)}</option>`).join('');
    await loadData('all-four');
  } catch (e) {
    $('#view').innerHTML = `<div class="card empty"><b>KubeDoctor could not start</b>${esc(e.message)}</div>`;
  }
})();
