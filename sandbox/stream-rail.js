// Side controls beside the WhatsApp panel: trigger the simulated data stream and
// view the data files each run leaves behind. Self-contained, so the chat code
// in index.html knows nothing about it.
(() => {
  'use strict';

  const API = '/sim/api/stream/';
  const WATCH_MS = 4000;
  const REASONS = {
    out_of_area: 'Outside Lucknow', off_topic: 'Not a civic issue', duplicate: 'Repeat report',
    rate_limited: 'Rate limited', guardrail: 'Other guardrail',
  };
  const RATES = [['Slow, 12 a minute', 12], ['Normal, 30 a minute', 30], ['Fast, 60 a minute', 60], ['Burst, 120 a minute', 120]];

  const ICON_PLAY = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
  const ICON_STOP = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7h10v10H7z"/></svg>';
  const ICON_FILES = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 2h8l5 5v15H6V2zm7 1.5V8h4.5L13 3.5zM8.5 12v1.6h8V12h-8zm0 3.5v1.6h8v-1.6h-8z"/></svg>';

  const style = document.createElement('style');
  style.textContent = `
    /* 300px clears the 560px sign-in card as well as the narrower chat panel. */
    .srail { position: fixed; z-index: 50; top: 50%; right: calc(50% + 300px); transform: translateY(-50%);
      width: 200px; display: flex; flex-direction: column; gap: 8px; padding: 12px; border-radius: 12px;
      background: var(--panel); color: var(--text); box-shadow: 0 4px 18px rgba(11,20,26,.18);
      font: 13px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
    .srail h2 { margin: 0; font-size: 13px; font-weight: 700; }
    .srail label { display: block; font-size: 11px; color: var(--text-2); margin-bottom: 3px; }
    .srail select { width: 100%; height: 32px; padding: 0 6px; border-radius: 6px; border: 1px solid var(--border);
      background: var(--input-bg); color: var(--text); font: inherit; font-size: 12px; }
    .srail-btn { display: flex; align-items: center; justify-content: center; gap: 7px; width: 100%; min-height: 40px;
      padding: 6px 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--panel); color: var(--text);
      font: 600 13px/1.2 inherit; font-family: inherit; cursor: pointer; }
    .srail-btn svg { width: 18px; height: 18px; fill: currentColor; flex: none; }
    .srail-btn.go { background: var(--green-deep); border-color: var(--green-deep); color: #fff; }
    .srail-btn.halt { background: var(--danger); border-color: var(--danger); color: #fff; }
    .srail-btn:disabled { opacity: .6; cursor: default; }
    .srail-status { font-size: 12px; color: var(--text-2); min-height: 17px; overflow-wrap: anywhere; }
    .srail-status.live { color: var(--green-deep); font-weight: 600; }
    .srail-status.bad { color: var(--danger); }
    .srail a { color: var(--green-deep); font-size: 12px; text-align: center; }
    .srail :focus-visible, .sfiles :focus-visible { outline: 2px solid var(--green); outline-offset: 2px; }

    .sfiles { margin: auto; border: 0; border-radius: 12px; padding: 0; background: var(--panel); color: var(--text);
      box-shadow: 0 12px 40px rgba(0,0,0,.35); font: 13px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
    .sfiles { width: min(980px, calc(100vw - 32px)); max-height: calc(100dvh - 48px); }
    .sfiles::backdrop { background: rgba(11,20,26,.55); }
    .sfiles-head { display: flex; align-items: center; gap: 10px; padding: 14px 16px; border-bottom: 1px solid var(--border);
      position: sticky; top: 0; background: var(--panel); flex-wrap: wrap; }
    .sfiles-head h2 { margin: 0; font-size: 15px; flex: 1; min-width: 160px; }
    .sfiles-body { padding: 12px 16px 16px; }
    .sfile { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px 12px; align-items: center;
      padding: 10px 0; border-bottom: 1px solid var(--border); }
    .sfile b { display: block; }
    .sfile span { color: var(--text-2); font-size: 12px; }
    .sfile-actions { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
    .slink { display: inline-flex; align-items: center; min-height: 32px; padding: 0 10px; border-radius: 6px;
      border: 1px solid var(--border); background: var(--panel); color: var(--text); font: 600 12px/1 inherit;
      font-family: inherit; cursor: pointer; text-decoration: none; }
    .stable-wrap { overflow-x: auto; }
    .stable { width: 100%; border-collapse: collapse; font-size: 12px; }
    .stable th { text-align: left; color: var(--text-2); font-weight: 600; padding: 6px 8px; border-bottom: 1px solid var(--border); white-space: nowrap; }
    .stable td { padding: 6px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
    .stable td.msg { min-width: 240px; }
    .stable .filed { color: var(--green-deep); font-weight: 600; }
    .stable .rejected, .stable .error { color: var(--danger); font-weight: 600; }
    .sempty { color: var(--text-2); padding: 20px 0; text-align: center; margin: 0; }

    /* Too narrow to sit beside the panel: shrink to two icons over the chat's edge. */
    @media (max-width: 1040px) {
      .srail { top: 64px; right: 8px; transform: none; width: auto; padding: 6px; gap: 6px; border-radius: 999px; }
      .srail h2, .srail .srail-field, .srail-status, .srail a { display: none; }
      .srail-btn { width: 44px; height: 44px; min-height: 44px; padding: 0; border-radius: 50%; }
      .srail-btn .srail-label { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
    }
    @media (max-width: 640px) { .sfile { grid-template-columns: minmax(0, 1fr); } .sfile-actions { justify-content: flex-start; } }
  `;
  document.head.append(style);

  const h = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------- markup ----------
  const rail = h('aside', 'srail');
  rail.setAttribute('aria-label', 'Live data stream controls');
  rail.append(h('h2', '', 'Live data stream'));

  function field(labelText, id) {
    const wrap = h('div', 'srail-field');
    const label = h('label', '', labelText);
    label.htmlFor = id;
    const select = h('select');
    select.id = id;
    wrap.append(label, select);
    rail.append(wrap);
    return select;
  }
  const scenarioSelect = field('Scenario', 'srail-scenario');
  const rateSelect = field('Rate', 'srail-rate');
  RATES.forEach(([label, value]) => {
    const opt = h('option', '', label);
    opt.value = value;
    rateSelect.append(opt);
  });
  rateSelect.value = '30';

  const toggle = h('button', 'srail-btn go');
  toggle.type = 'button';
  const filesBtn = h('button', 'srail-btn');
  filesBtn.type = 'button';
  filesBtn.innerHTML = ICON_FILES;
  filesBtn.append(h('span', 'srail-label', 'View data files'));
  const status = h('div', 'srail-status');
  status.setAttribute('role', 'status');
  const consoleLink = h('a', '', 'Open live console');
  consoleLink.href = '/stream.html';
  consoleLink.target = '_blank';
  consoleLink.rel = 'noopener';
  rail.append(toggle, filesBtn, status, consoleLink);

  const filesDialog = h('dialog', 'sfiles');
  filesDialog.setAttribute('aria-label', 'Data stream files');
  document.body.append(rail, filesDialog);

  // ---------- state ----------
  let driving = false;
  let othersLive = false;
  let runId = null;
  let holdStatusUntil = 0;

  async function call(path, body) {
    const res = await fetch(API + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch { /* not JSON */ }
    if (!res.ok) {
      const err = new Error((data && data.detail) || 'Request failed (' + res.status + ')');
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ---------- painting ----------
  function paintToggle() {
    const running = driving || othersLive;
    toggle.className = 'srail-btn ' + (running ? 'halt' : 'go');
    toggle.innerHTML = running ? ICON_STOP : ICON_PLAY;
    toggle.append(h('span', 'srail-label', running ? 'Stop data stream' : 'Start data stream'));
    toggle.setAttribute('aria-label', running ? 'Stop data stream' : 'Start data stream');
    scenarioSelect.disabled = running;
    rateSelect.disabled = running;
  }

  function say(text, kind) {
    status.textContent = text;
    status.className = 'srail-status' + (kind ? ' ' + kind : '');
    holdStatusUntil = kind === 'bad' ? Date.now() + 8000 : 0;
  }

  function paintState(s) {
    othersLive = !!s.live && !driving;
    paintToggle();
    // An error stays readable rather than being replaced by the next poll.
    if (Date.now() < holdStatusUntil) return;
    if (driving || s.live) {
      say(`Live: ${s.filed} filed, ${s.rejected} refused`, 'live');
    } else if (s.events) {
      say(`Last run: ${s.filed} filed, ${s.rejected} refused`);
    } else {
      say('Not running');
    }
  }

  // ---------- driving ----------
  async function drive(rate) {
    const batch = Math.max(1, Math.min(5, Math.ceil((rate / 60) * 2.5)));
    const intervalMs = (batch * 60000) / rate;
    let failures = 0;
    while (driving) {
      const began = performance.now();
      try {
        const out = await call('tick', { run_id: runId, batch });
        failures = 0;
        if (out.state.status !== 'running') driving = false;
        paintState(out.state);
      } catch (e) {
        if (e.status === 429) { await sleep(1500); continue; }
        failures += 1;
        if (e.status === 409 || failures >= 5) {
          driving = false;
          paintToggle();
          say('Stopped: ' + e.message, 'bad');
          return;
        }
        await sleep(2000);
        continue;
      }
      await sleep(Math.max(0, intervalMs - (performance.now() - began)));
    }
    paintToggle();
  }

  async function start() {
    const rate = Number(rateSelect.value);
    try {
      const run = await call('start', { scenario: scenarioSelect.value || 'steady', rate_per_min: rate, max_events: 300 });
      runId = run.run_id;
      driving = true;
      paintState(run);
      drive(rate);
    } catch (e) {
      say(e.message, 'bad');
    }
  }

  async function stop() {
    driving = false;
    try {
      paintState(await call('stop', {}));
    } catch (e) {
      say(e.message, 'bad');
    }
  }

  toggle.addEventListener('click', async () => {
    toggle.disabled = true;
    try {
      await (driving || othersLive ? stop() : start());
    } finally {
      toggle.disabled = false;
    }
  });

  // ---------- data files ----------
  function filesHeader(titleText, backTo) {
    const head = h('div', 'sfiles-head');
    if (backTo) {
      const back = h('button', 'slink', 'All files');
      back.type = 'button';
      back.addEventListener('click', backTo);
      head.append(back);
    }
    head.append(h('h2', '', titleText));
    return head;
  }

  function closeButton() {
    const close = h('button', 'slink', 'Close');
    close.type = 'button';
    close.addEventListener('click', () => filesDialog.close());
    return close;
  }

  function downloadLink(runIdValue, format, label) {
    const a = h('a', 'slink', label);
    a.href = `${API}files/${runIdValue}?format=${format}`;
    a.download = `urbanos-stream-${runIdValue}.${format}`;
    return a;
  }

  async function showFileList() {
    filesDialog.textContent = '';
    const head = filesHeader('Data stream files');
    head.append(closeButton());
    const body = h('div', 'sfiles-body');
    body.append(h('p', 'sempty', 'Loading...'));
    filesDialog.append(head, body);
    try {
      const { files } = await call('files');
      body.textContent = '';
      if (!files.length) {
        body.append(h('p', 'sempty', 'No files yet. Each stream run creates one here.'));
        return;
      }
      files.forEach((f) => {
        const row = h('div', 'sfile');
        const info = h('div');
        info.append(h('b', '', `${f.scenario_label}`),
          h('span', '', `${new Date(f.started_at * 1000).toLocaleString()} · ${f.events} citizens, ${f.filed} filed, ${f.rejected} refused · ${f.status}`));
        const actions = h('div', 'sfile-actions');
        const view = h('button', 'slink', 'View');
        view.type = 'button';
        view.addEventListener('click', () => showFile(f));
        actions.append(view, downloadLink(f.run_id, 'csv', 'CSV'), downloadLink(f.run_id, 'jsonl', 'JSONL'));
        row.append(info, actions);
        body.append(row);
      });
    } catch (e) {
      body.textContent = '';
      body.append(h('p', 'sempty', 'Could not load the files: ' + e.message));
    }
  }

  async function showFile(file) {
    filesDialog.textContent = '';
    const head = filesHeader(file.scenario_label, showFileList);
    head.append(downloadLink(file.run_id, 'csv', 'CSV'), downloadLink(file.run_id, 'jsonl', 'JSONL'), closeButton());
    const body = h('div', 'sfiles-body');
    body.append(h('p', 'sempty', 'Loading...'));
    filesDialog.append(head, body);
    try {
      const { rows } = await call('files/' + file.run_id);
      body.textContent = '';
      if (!rows.length) { body.append(h('p', 'sempty', 'This run has no results yet.')); return; }
      const wrap = h('div', 'stable-wrap');
      const table = h('table', 'stable');
      const headRow = h('tr');
      ['Time', 'Sender', 'Message', 'Location', 'Result', 'Decision', 'Reference', 'Took'].forEach((t) => headRow.append(h('th', '', t)));
      const thead = h('thead');
      thead.append(headRow);
      const tbody = h('tbody');
      rows.slice().reverse().forEach((r) => {
        const tr = h('tr');
        const decision = r.outcome === 'filed'
          ? [r.category, r.priority, r.department].filter(Boolean).join(' · ')
          : (REASONS[r.reason] || r.reason || '');
        tr.append(
          h('td', '', new Date(r.at * 1000).toLocaleTimeString()),
          h('td', '', r.phone),
          h('td', 'msg', r.text),
          h('td', '', r.location),
          h('td', r.outcome, r.outcome),
          h('td', '', decision),
          h('td', '', r.reference_id || ''),
          h('td', '', r.pipeline_ms + ' ms'),
        );
        tbody.append(tr);
      });
      table.append(thead, tbody);
      wrap.append(table);
      body.append(wrap);
    } catch (e) {
      body.textContent = '';
      body.append(h('p', 'sempty', 'Could not load this file: ' + e.message));
    }
  }

  function openFiles() {
    filesDialog.showModal();
    showFileList();
  }
  filesBtn.addEventListener('click', openFiles);

  // ---------- watching ----------
  async function watch() {
    if (driving || document.hidden) return;
    try {
      const s = await call('state');
      if (s.scenarios && !scenarioSelect.options.length) {
        s.scenarios.forEach((sc) => {
          const opt = h('option', '', sc.label);
          opt.value = sc.key;
          opt.title = sc.description;
          scenarioSelect.append(opt);
        });
      }
      if (!driving) paintState(s);
    } catch { /* a missed poll corrects itself on the next one */ }
  }

  paintToggle();
  say('Not running');
  watch();
  setInterval(watch, WATCH_MS);
  if (location.hash === '#stream-files') openFiles();
})();
