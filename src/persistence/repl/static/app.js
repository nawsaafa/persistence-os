// persistence.repl browser console UI (D8, v0.7.0a1).
//
// Vanilla JS, no build step. Served by aiohttp from the same port as
// the WS endpoint (single-port pattern, design ADR-6).
//
// XSS PIN: ALL user-visible content is rendered via textContent, never
// via the unsafe HTML-string DOM setter. Both surfaces (output pane +
// audit tail) accept untrusted data — operator input on one side, and
// server-emitted audit entries (which include op_kind, args echoes,
// error strings) on the other. The unsafe setter does NOT appear in
// this file (tests assert absence by source-string scan).
//
// Token loading: design 6.2 + W2.MINOR-7 — accept a token via URL
// fragment (#token=...), then immediately scrub via history.replaceState
// so a screen-share or browser-history leak does not expose it.
//
// Audit tail: D7 persists audit entries but does not yet PUSH them via
// WS notifications. v0.7.0a1 polls `repl/inspect kind=audit-window`
// once per second and renders only entries whose `id` was not seen
// previously. Server-side push is a v0.7.x follow-up.

(function() {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const output = $('output');
  const audit = $('audit-tail');
  const status = $('conn-status');
  const session = $('session-info');
  const form = $('cmd-form');
  const input = $('cmd-input');

  let ws = null;
  let nextId = 1;
  const pendingByReqId = new Map();
  const seenAuditIds = new Set();
  let auditPollHandle = null;

  // ----- output pane -------------------------------------------------
  function appendOutput(text, cls) {
    const div = document.createElement('div');
    div.className = 'entry ' + (cls || 'ok');
    div.textContent = text;  // XSS pin
    output.appendChild(div);
    output.scrollTop = output.scrollHeight;
  }

  // ----- audit tail --------------------------------------------------
  function appendAudit(entry) {
    const div = document.createElement('div');
    div.className = 'audit-entry';

    const ts = document.createElement('span');
    ts.className = 'ts';
    const recordedAt = entry.recorded_at;
    if (typeof recordedAt === 'number') {
      ts.textContent = new Date(recordedAt * 1000).toISOString().substr(11, 12);
    } else if (typeof recordedAt === 'string') {
      // ISO-8601 from server; show HH:MM:SS.fff slice if present
      const t = recordedAt.indexOf('T');
      ts.textContent = (t >= 0) ? recordedAt.substr(t + 1, 12) : recordedAt;
    } else {
      ts.textContent = String(recordedAt || '');
    }
    div.appendChild(ts);
    div.appendChild(document.createTextNode(' '));

    const op = document.createElement('span');
    op.className = 'op';
    op.textContent = (entry.principal && entry.principal.op_kind)
      || entry.op_kind
      || entry.op
      || '?';
    div.appendChild(op);
    div.appendChild(document.createTextNode(' '));

    const verdict = document.createElement('span');
    const v = entry.verdict || 'ok';
    verdict.className = 'verdict ' + v;
    verdict.textContent = v;
    div.appendChild(verdict);
    div.appendChild(document.createTextNode(' '));

    const latency = document.createElement('span');
    latency.textContent = (entry.latency_ms != null)
      ? (entry.latency_ms + 'ms')
      : '';
    latency.style.color = '#888';
    latency.style.fontSize = '10px';
    div.appendChild(latency);

    audit.appendChild(div);
    audit.scrollTop = audit.scrollHeight;
  }

  // ----- header status ----------------------------------------------
  function setStatus(text, cls) {
    status.textContent = text;
    status.className = 'status ' + (cls || '');
  }

  // ----- token loading ----------------------------------------------
  function loadToken() {
    const hash = window.location.hash;
    let token = null;
    if (hash && hash.startsWith('#token=')) {
      token = hash.substring(7);
      // Scrub URL immediately so the token does not survive in
      // browser history, screen-shares, or document.referrer.
      history.replaceState({}, '', '/');
    }
    return token;
  }

  // ----- WS request/response ----------------------------------------
  function send(method, params) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      appendOutput('not connected', 'err');
      return Promise.reject(new Error('not connected'));
    }
    const id = nextId++;
    const req = { jsonrpc: '2.0', id: id, method: method, params: params || {} };
    return new Promise((resolve, reject) => {
      pendingByReqId.set(id, { resolve: resolve, reject: reject });
      ws.send(JSON.stringify(req));
    });
  }

  // ----- command parser ---------------------------------------------
  // Input syntax:
  //   help                      -> built-in help text
  //   <method> k=v k2=v2 ...    -> JSON-RPC with coerced params
  //   >>> {json}                -> raw JSON-RPC pass-through
  //
  // Coercion rules for k=v: 'null' -> null, 'true' / 'false' -> bool,
  // ^-?\d+$ -> int, ^-?\d+\.\d+$ -> float, otherwise string.
  function parseCommand(line) {
    line = line.trim();
    if (line === '') return { kind: 'error', message: 'empty input' };
    if (line === 'help') return { kind: 'help' };
    if (line.startsWith('>>>')) {
      // Raw JSON-RPC pass-through
      try {
        const obj = JSON.parse(line.slice(3).trim());
        return {
          kind: 'rpc',
          method: obj.method,
          params: obj.params || {},
        };
      } catch (e) {
        return { kind: 'error', message: 'invalid JSON: ' + e.message };
      }
    }
    const parts = line.split(/\s+/);
    if (parts.length === 0) return { kind: 'error', message: 'empty input' };
    const method = parts[0];
    const params = {};
    for (let i = 1; i < parts.length; i++) {
      const eq = parts[i].indexOf('=');
      if (eq < 0) {
        return { kind: 'error', message: 'bad param: ' + parts[i] };
      }
      const k = parts[i].slice(0, eq);
      const v = parts[i].slice(eq + 1);
      if (v === 'null') params[k] = null;
      else if (v === 'true') params[k] = true;
      else if (v === 'false') params[k] = false;
      else if (/^-?\d+$/.test(v)) params[k] = parseInt(v, 10);
      else if (/^-?\d+\.\d+$/.test(v)) params[k] = parseFloat(v);
      else params[k] = v;
    }
    return { kind: 'rpc', method: method, params: params };
  }

  function helpText() {
    return [
      'commands:',
      '  repl/inspect kind=entity entity_id=<id> [view_cursor_tx_time_iso=<iso>]',
      '  repl/inspect kind=audit-window [limit=50] [op_filter=<op>]',
      '  repl/inspect kind=causal-history entity_id=<id> [limit=50]',
      '  repl/inspect kind=plan plan_id=<id>',
      '  repl/rewind tx_time_iso=<iso>',
      '  repl/branch tx_time_iso=<iso> label=<text>',
      '  repl/edit datoms=[...]   (use >>> raw JSON-RPC for structured payload)',
      '  >>> { "method": "...", "params": {...} }   raw JSON-RPC pass-through',
      '  help    show this help',
    ].join('\n');
  }

  // ----- audit polling ----------------------------------------------
  // D7 persists audit entries but does not yet push them via WS
  // notifications. Poll once per second; diff by entry.id.
  async function pollAudit() {
    try {
      const result = await send('repl/inspect', {
        kind: 'audit-window',
        params: { limit: 50 },
      });
      if (!result || !result.entries) return;
      for (const entry of result.entries) {
        const eid = entry.id || entry.entry_id;
        if (eid != null && !seenAuditIds.has(eid)) {
          seenAuditIds.add(eid);
          appendAudit(entry);
        }
      }
    } catch (e) {
      // ignore poll failures (transient WS state, deny, etc.)
    }
  }

  function startAuditPolling() {
    if (auditPollHandle) clearInterval(auditPollHandle);
    auditPollHandle = setInterval(pollAudit, 1000);
  }

  function stopAuditPolling() {
    if (auditPollHandle) {
      clearInterval(auditPollHandle);
      auditPollHandle = null;
    }
  }

  // ----- WS lifecycle ------------------------------------------------
  function connect(token) {
    const proto = (location.protocol === 'https:') ? 'wss:' : 'ws:';
    const url = proto + '//' + location.host + '/ws';
    ws = new WebSocket(url);

    ws.onopen = async () => {
      setStatus('authenticating', '');
      try {
        const result = await send('repl/auth', { token: token });
        setStatus('connected', 'connected');
        const sid = result.session_id || '?';
        const caps = (result.caps || [])
          .map((c) => c.op + ':' + c.qualifier)
          .join(', ');
        session.textContent = sid.substr(0, 8) + ' [' + caps + ']';
        appendOutput(
          'authenticated as ' + sid.substr(0, 8) + ' caps={' + caps + '}',
          'ok'
        );
        startAuditPolling();
      } catch (e) {
        setStatus('auth failed', 'error');
        appendOutput('auth failed: ' + (e.message || JSON.stringify(e)), 'err');
      }
    };

    ws.onmessage = (msg) => {
      let payload;
      try {
        payload = JSON.parse(msg.data);
      } catch (e) {
        appendOutput('bad payload: ' + msg.data, 'err');
        return;
      }
      if (payload.id != null && pendingByReqId.has(payload.id)) {
        const { resolve, reject } = pendingByReqId.get(payload.id);
        pendingByReqId.delete(payload.id);
        if (payload.error) reject(payload.error);
        else resolve(payload.result);
      } else if (payload.method) {
        // Server-initiated notification (e.g., :repl/audit-event push).
        // Not yet wired in v0.7.0a1; surface as a debug line for now.
        appendOutput('notification: ' + payload.method, 'ok');
      }
    };

    ws.onerror = () => {
      setStatus('connection error', 'error');
    };

    ws.onclose = () => {
      setStatus('disconnected', '');
      stopAuditPolling();
    };
  }

  // ----- form submit -------------------------------------------------
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const line = input.value;
    if (!line.trim()) return;
    input.value = '';
    const parsed = parseCommand(line);
    appendOutput('> ' + line, 'cmd');
    if (parsed.kind === 'help') {
      appendOutput(helpText(), 'ok');
      return;
    }
    if (parsed.kind === 'error') {
      appendOutput(parsed.message, 'err');
      return;
    }
    try {
      const result = await send(parsed.method, parsed.params);
      appendOutput(JSON.stringify(result, null, 2), 'ok');
    } catch (err) {
      appendOutput('error: ' + JSON.stringify(err), 'err');
    }
  });

  // ----- bootstrap ---------------------------------------------------
  const token = loadToken();
  if (!token) {
    setStatus('no token', 'error');
    appendOutput(
      'no token in URL fragment. Reload with #token=<token-str> in URL '
        + 'or paste token via raw RPC: '
        + '>>> {"method": "repl/auth", "params": {"token": "..."}}',
      'err'
    );
  } else {
    connect(token);
  }
})();
