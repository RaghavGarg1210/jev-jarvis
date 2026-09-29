/* A plan is a proposal. Only the explicit approval button can execute it. */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = {
    token: null, ready: false, mode: 'rehearsal', provider: '', planner: '',
    voiceEnabled: false, plan: null, history: [], timers: [], generation: 0,
    planning: false, executing: false, transcribing: false, recording: false,
    recorder: null, stream: null, recordingTimer: null, planTimer: null,
    view: 'command', booting: false, historyGeneration: 0, finishedTimers: new Map(),
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function icon(name) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    node.setAttribute('class', 'icon');
    node.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', `#i-${name}`);
    node.append(use);
    return node;
  }

  function button(text, className, handler, iconName) {
    const node = el('button', `button ${className}`);
    node.type = 'button';
    if (iconName) node.append(icon(iconName));
    node.append(el('span', '', text));
    node.addEventListener('click', handler);
    return node;
  }

  function announce(text) { $('live-status').textContent = text; }

  function showError(message, target = 'command-error', reconnect = false) {
    const box = $(target);
    box.replaceChildren(el('div', '', message));
    if (reconnect) box.append(button('Reconnect to Jev', 'secondary', bootstrap, 'history'));
    box.hidden = false;
  }

  function clearError(target = 'command-error') {
    $(target).hidden = true;
    $(target).replaceChildren();
  }

  function connection(connected) {
    $('connection-dot').className = `status-dot ${connected ? 'connected' : 'disconnected'}`;
    $('connection-label').textContent = connected ? 'Connected' : 'Offline';
  }

  function displayValue(value) {
    if (typeof value === 'string') return value;
    if (value === undefined || value === null) return '—';
    return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
  }

  async function request(path, payload, options = {}) {
    const token = state.token;
    const headers = {};
    const init = { method: 'GET', credentials: 'same-origin', cache: 'no-store', headers };
    if (payload !== undefined) {
      init.method = 'POST';
      headers['X-Jarvis-Token'] = token || '';
      if (options.raw) {
        headers['Content-Type'] = payload.type || 'application/octet-stream';
        init.body = payload;
      } else {
        headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(payload);
      }
    }
    let response;
    try { response = await fetch(path, init); }
    catch (_) {
      connection(false);
      throw new Error('Jev could not reach its local server. Check that it is still running, then try again.');
    }
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error('The local server returned an unreadable response. Please try again.'); }
    if (!response.ok || data.error) {
      if (response.status === 403) {
        state.ready = false;
        state.token = null;
        state.generation += 1;
        state.plan = null;
        clearTimeout(state.planTimer);
        $('plan-preview').hidden = true;
        $('plan-empty').hidden = false;
        $('plan-state').textContent = 'Reconnect needed';
        updateControls();
      }
      const error = new Error(data.error || `Request failed (${response.status}). Please try again.`);
      error.status = response.status;
      throw error;
    }
    // A response from a previous server session must never become an approvable plan.
    if (payload !== undefined && token !== state.token) throw new Error('The session changed. Please make a new plan.');
    connection(true);
    return data;
  }

  function updateControls() {
    $('plan-button').disabled = !state.ready || state.planning || state.executing || state.transcribing || state.recording;
    $('plan-button').querySelector('span').textContent = state.planning ? 'Preparing preview…' : 'Preview';
    $('command-input').disabled = state.executing;
    $('voice-button').disabled = !state.ready || state.planning || state.executing || state.transcribing;
    $('voice-label').textContent = state.transcribing ? 'Transcribing' : state.recording ? 'Stop' : 'Dictate';
    $('voice-button').classList.toggle('recording', state.recording);
    $('voice-button').setAttribute('aria-pressed', String(state.recording));
    $('command-form').setAttribute('aria-busy', String(state.planning || state.executing || state.transcribing));
    document.querySelectorAll('.scene-card, .app-shortcut, [data-command]').forEach((node) => { node.disabled = !state.ready || state.planning || state.executing || state.transcribing || state.recording; });
    const approve = $('approve-plan');
    if (approve) approve.disabled = !state.ready || !state.plan || state.planning || state.executing || state.transcribing || state.recording;
  }

  function setView(view, focus = false) {
    if (!['command', 'routines', 'activity', 'setup'].includes(view)) view = 'command';
    state.view = view;
    for (const name of ['command', 'routines', 'activity', 'setup']) $(name + '-view').hidden = name !== view;
    document.querySelectorAll('[data-view]').forEach((link) => {
      const active = link.dataset.view === view;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
      // Collapsed navigation still has an accessible name and pointer tooltip.
      link.setAttribute('aria-label', link.textContent.trim());
      link.title = link.textContent.trim();
    });
    $('view-label').textContent = view[0].toUpperCase() + view.slice(1);
    document.title = `Jev · ${$('view-label').textContent}`;
    if (focus) $('main').focus({ preventScroll: true });
    if (view === 'activity' && state.ready) refreshHistory();
  }

  function chooseCommand(text) {
    if (!state.ready || state.planning || state.executing || state.transcribing || state.recording) return;
    location.hash = 'command';
    setView('command');
    $('command-input').value = text;
    state.generation += 1;
    makePlan();
  }

  function renderApps(apps) {
    const root = $('quick-apps');
    if (!root) return;
    root.replaceChildren();
    const known = {
      safari: ['Safari', 'compass'], browser: ['Browser', 'compass'],
      notes: ['Notes', 'note'], note: ['Notes', 'note'],
      calendar: ['Calendar', 'calendar'], messages: ['Messages', 'message'],
      message: ['Messages', 'message'], imessage: ['Messages', 'message'],
      reminders: ['Reminders', 'check'], music: ['Music', 'play'],
      code: ['VS Code', 'code'], vscode: ['VS Code', 'code'],
      'visual studio code': ['VS Code', 'code'], terminal: ['Terminal', 'terminal'],
      iterm: ['iTerm', 'terminal'], iterm2: ['iTerm', 'terminal'],
    };
    apps.slice(0, 6).forEach((alias) => {
      const name = String(alias);
      const key = name.toLowerCase();
      const [label, symbol] = known[key] || [name.charAt(0).toUpperCase() + name.slice(1), 'app'];
      const node = el('button', 'app-shortcut');
      node.type = 'button';
      node.title = `Preview opening ${label}`;
      const badge = el('span', `app-icon app-icon-${symbol}`);
      badge.append(icon(symbol));
      node.append(badge, el('span', 'app-label', label));
      node.addEventListener('click', () => chooseCommand(`open ${name}`));
      root.append(node);
    });
    if (!apps.length) root.append(el('p', 'muted', 'No apps configured.'));
  }

  function renderScenes(scenes) {
    $('quick-scenes').replaceChildren();
    $('all-scenes').replaceChildren();
    const symbols = ['clock', 'moon', 'command', 'app', 'note'];
    if (!scenes.length) {
      $('quick-scenes').append(el('p', 'muted', 'No routines configured.'));
      $('all-scenes').append(el('p', 'muted', 'Add a routine in your configuration to use it here.'));
      return;
    }
    scenes.forEach((scene, index) => {
      function card(expanded) {
        const node = el('button', 'scene-card');
        node.type = 'button';
        const symbol = el('span', 'scene-icon');
        symbol.append(icon(symbols[index % symbols.length]));
        const copy = el('span', 'scene-copy');
        copy.append(el('strong', '', scene.title || 'Untitled routine'));
        const description = expanded ? scene.prompt || scene.description : scene.description;
        if (description) copy.append(el('p', '', description));
        node.append(symbol, copy, icon('arrow'));
        node.addEventListener('click', () => chooseCommand(scene.prompt || ''));
        return node;
      }
      if (index < 3) $('quick-scenes').append(card(false));
      $('all-scenes').append(card(true));
    });
  }

  function renderSetup(data) {
    $('setup-details').replaceChildren();
    for (const [label, value] of [
      ['Mode', state.mode === 'live' ? 'Live · approval required' : 'Rehearsal · no Mac changes'],
      ['Intent routing', data.provider || 'Not available'],
      ['Planning', data.planner || 'Not available'],
      ['Speech', data.voice_enabled ? 'Local transcription enabled' : 'Optional · not enabled'],
    ]) {
      const row = el('div');
      row.append(el('dt', '', label), el('dd', '', value));
      $('setup-details').append(row);
    }
    for (const [target, values, empty] of [
      ['allowed-apps', data.apps, 'No apps configured'],
      ['allowed-contacts', data.contacts, 'No contacts configured'],
    ]) {
      $(target).replaceChildren();
      (Array.isArray(values) && values.length ? values : [empty]).forEach((value) => $(target).append(el('span', 'tag', value)));
    }
    $('voice-setup-state').textContent = state.voiceEnabled ? 'Local transcription is enabled. Press Dictate to use your microphone.' : 'Add the speech extra, then start with JARVIS_VOICE=1 jev-jarvis.';
    const capabilities = document.querySelector('#setup-view .quiet-callout p');
    capabilities.textContent = 'Open apps and websites, save notes, set timers, adjust volume, speak text, send messages, and run configured Apple Shortcuts.';
    if (Array.isArray(data.shortcuts) && data.shortcuts.length) {
      const row = el('div');
      row.append(el('dt', '', 'Apple Shortcuts'), el('dd', '', data.shortcuts.join(', ')));
      $('setup-details').append(row);
    }
  }

  async function bootstrap() {
    if (state.booting || state.executing) return;
    state.booting = true;
    state.ready = false;
    state.generation += 1;
    clearError();
    updateControls();
    try {
      const data = await request('/api/bootstrap');
      if (!data.token || !['live', 'rehearsal'].includes(data.mode)) throw new Error('Jev returned an incomplete session. Restart the local server and reconnect.');
      state.token = data.token;
      state.ready = true;
      state.mode = data.mode;
      state.provider = data.provider || '';
      state.planner = data.planner || '';
      state.voiceEnabled = Boolean(data.voice_enabled);
      state.history = Array.isArray(data.history) ? data.history : [];
      state.timers = Array.isArray(data.timers) ? data.timers : [];
      state.plan = null;
      clearTimeout(state.planTimer);
      $('plan-preview').hidden = true;
      $('plan-empty').hidden = false;
      $('execution-result').hidden = true;
      $('plan-state').textContent = 'Ready';
      $('mode-badge').textContent = state.mode === 'live' ? 'Live mode' : 'Rehearsal mode';
      $('mode-badge').classList.toggle('live', state.mode === 'live');
      $('planner-label').textContent = state.planner === 'ollama' ? 'Local language model' : 'Command mode';
      const flexible = state.planner === 'ollama';
      $('command-input').placeholder = flexible ? 'Open Safari and set a timer for 25 minutes' : 'Try “open Safari”';
      document.querySelector('.intro-copy').textContent = 'Open an app, create a note, or run a routine.';
      $('safety-title').textContent = state.mode === 'live' ? 'Approval required' : 'Rehearsal is on';
      $('safety-detail').textContent = state.mode === 'live' ? 'Review the preview before running an action.' : 'Actions are simulated; your Mac stays unchanged.';
      $('footer-mode').textContent = `${state.mode === 'live' ? 'Live' : 'Rehearsal'} · macOS`;
      renderApps(Array.isArray(data.apps) ? data.apps : []);
      renderScenes(Array.isArray(data.scenes) ? data.scenes : []);
      renderSetup(data);
      renderHistory();
      announce(`Connected. ${state.mode === 'live' ? 'Live mode: approved plans can change your Mac.' : 'Rehearsal mode: no actions change your Mac.'}`);
    } catch (error) {
      connection(false);
      $('mode-badge').textContent = 'Offline';
      $('planner-label').textContent = 'Start the local server to connect';
      showError(error.message, 'command-error', true);
    } finally {
      state.booting = false;
      updateControls();
    }
  }

  async function cancelPlan({ silent = false } = {}) {
    if (state.executing) return;
    const previous = state.plan;
    state.plan = null;
    state.generation += 1;
    clearTimeout(state.planTimer);
    $('plan-preview').hidden = true;
    $('plan-empty').hidden = false;
    $('plan-state').textContent = 'Ready';
    if (!previous) return;
    try { await request('/api/cancel', { id: previous.id }); }
    catch (error) { if (!silent) showError(error.message, 'command-error', error.status === 403); }
    if (!silent) announce('Plan cancelled. Nothing was run.');
  }

  async function makePlan(event) {
    if (event) event.preventDefault();
    if (!state.ready || state.planning || state.executing || state.transcribing || state.recording) return;
    const text = $('command-input').value.trim();
    if (!text) {
      showError('Tell Jev what you would like to do first.');
      $('command-input').focus();
      return;
    }
    clearError();
    state.planning = true;
    updateControls();
    const cancellation = cancelPlan({ silent: true });
    const generation = state.generation;
    await cancellation;
    if (generation !== state.generation || text !== $('command-input').value.trim()) {
      state.planning = false;
      updateControls();
      announce('Your request changed. Make a new plan when you are ready.');
      return;
    }
    if (!state.ready) {
      state.planning = false;
      updateControls();
      showError('Your session changed. Reconnect, then make a new plan.', 'command-error', true);
      return;
    }
    const token = state.token;
    $('execution-result').hidden = true;
    $('plan-state').textContent = 'Preparing preview…';
    announce('Preparing preview. No actions are running.');
    try {
      const plan = await request('/api/plan', { text });
      if (generation !== state.generation || token !== state.token || text !== $('command-input').value.trim()) {
        if (plan.id && token === state.token) request('/api/cancel', { id: plan.id }).catch(() => {});
        $('plan-state').textContent = 'Draft changed';
        announce('Your request changed. Make a new plan when you are ready.');
        return;
      }
      if (!plan.id || !Array.isArray(plan.actions) || !plan.actions.length) throw new Error('Jev could not build an actionable plan. Try a specific app, note, timer, or message request.');
      state.plan = plan;
      renderPlan(plan);
      if (window.innerWidth < 800) {
        $('plan-preview').tabIndex = -1;
        $('plan-preview').focus({ preventScroll: true });
        $('plan-preview').scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' });
      }
      announce(`Plan ready: ${plan.title || 'Your request'}. Review ${plan.actions.length} ${plan.actions.length === 1 ? 'step' : 'steps'} before approving.`);
    } catch (error) {
      if (generation === state.generation || error.status === 403) {
        $('plan-state').textContent = 'Needs another try';
        showError(error.message, 'command-error', error.status === 403);
      }
    } finally {
      state.planning = false;
      updateControls();
    }
  }

  function dateValue(value) {
    if (typeof value === 'number') return new Date(value < 1e12 ? value * 1000 : value);
    return new Date(value);
  }

  function renderPlan(plan) {
    const root = $('plan-preview');
    root.replaceChildren();
    root.hidden = false;
    $('plan-empty').hidden = true;
    $('execution-result').hidden = true;
    $('plan-state').textContent = 'Preview ready';
    root.append(el('h2', 'plan-title', plan.title || 'Preview action'));
    root.append(el('p', 'plan-mode-note', state.mode === 'live' ? 'Runs on your Mac after approval.' : 'Rehearsal only. Your Mac stays unchanged.'));
    const list = el('ol', 'plan-actions');
    plan.actions.forEach((action, index) => {
      const item = el('li', 'plan-action');
      const heading = el('div', 'action-heading');
      heading.append(el('span', 'action-number', String(index + 1).padStart(2, '0')), el('h3', '', action.title || action.kind || 'Action'));
      item.append(heading);
      if (action.detail) item.append(el('p', 'action-detail', action.detail));
      if (!action.detail) {
        const args = el('dl', 'action-args');
        Object.entries(action.args || {}).forEach(([key, value]) => {
          const pair = el('div');
          pair.append(el('dt', '', key.replaceAll('_', ' ')), el('dd', '', displayValue(value)));
          args.append(pair);
        });
        if (args.childElementCount) item.append(args);
      }
      if (action.reversible) item.append(el('p', 'action-undo', 'Undo available'));
      else if (/message|send/.test(action.kind || '')) item.append(el('p', 'action-undo consequential', 'Sent messages cannot be undone.'));
      list.append(item);
    });
    root.append(list);
    const controls = el('div', 'plan-controls');
    const approve = button(state.mode === 'live' ? 'Approve & run' : 'Run rehearsal', 'primary', () => executePlan(plan.id), 'check');
    approve.id = 'approve-plan';
    const cancel = button('Cancel', 'ghost', () => cancelPlan(), 'close');
    cancel.id = 'cancel-plan';
    controls.append(approve, cancel);
    root.append(controls);
    const decision = el('details', 'decision-details');
    decision.append(el('summary', '', 'Decision details'));
    if (plan.explanation) decision.append(el('p', 'plan-small', plan.explanation));
    const provenance = [plan.provider ? `Routing: ${plan.provider}` : '', plan.model ? `Model: ${plan.model}` : ''].filter(Boolean).join(' · ');
    if (provenance) decision.append(el('p', 'plan-small', provenance));
    if (typeof plan.confidence === 'number' && Number.isFinite(plan.confidence)) {
      decision.append(el('p', 'plan-small', `Model score: ${(plan.confidence * 100).toFixed(1)}%. This does not verify the action.`));
    }
    decision.append(el('p', 'plan-small', 'Approval applies to this exact preview. Editing the request cancels it.'));
    root.append(decision);
    if (plan.expires_at) {
      const remaining = dateValue(plan.expires_at).getTime() - Date.now();
      if (Number.isFinite(remaining)) {
        const expire = () => {
          if (state.plan?.id !== plan.id || state.executing) return;
          state.plan = null;
          approve.disabled = true;
          approve.querySelector('span').textContent = 'Plan expired';
          $('plan-state').textContent = 'Make a fresh plan';
          root.append(el('p', 'plan-small', 'This plan has expired. Make a new one to review current details.'));
          announce('The plan expired. Make a new plan to continue.');
        };
        if (remaining <= 0) expire();
        else state.planTimer = setTimeout(expire, Math.min(remaining, 2147483647));
      }
    }
  }

  async function executePlan(id) {
    if (!state.ready || state.executing || state.planning || state.transcribing || state.recording || state.plan?.id !== id) return;
    if (state.plan.expires_at && dateValue(state.plan.expires_at).getTime() <= Date.now()) {
      showError('This plan expired. Make a fresh plan before approving it.');
      await cancelPlan({ silent: true });
      return;
    }
    state.executing = true;
    state.historyGeneration += 1;
    clearTimeout(state.planTimer);
    clearError();
    updateControls();
    $('approve-plan').disabled = true;
    $('cancel-plan').disabled = true;
    $('approve-plan').querySelector('span').textContent = state.mode === 'live' ? 'Running…' : 'Rehearsing…';
    $('plan-state').textContent = state.mode === 'live' ? 'In progress' : 'Rehearsing';
    try {
      const receipt = await request('/api/execute', { id });
      state.plan = null;
      state.history = [receipt, ...state.history.filter((item) => item.id !== receipt.id)];
      showReceipt(receipt);
      renderHistory();
      announce(receipt.status === 'simulated' ? 'Rehearsal complete. Your Mac was not changed.' : `Plan finished: ${receipt.status || 'see receipt'}.`);
      refreshHistory();
    } catch (error) {
      // Do not offer to run again after an ambiguous network result. The server
      // also consumes plan IDs once, but the UI asks for history inspection.
      state.plan = null;
      $('plan-preview').hidden = true;
      $('plan-empty').hidden = false;
      $('plan-state').textContent = 'Check activity';
      showError(`${error.message} Check Activity before making another plan; some steps may already have run.`, 'command-error', error.status === 403);
    } finally {
      state.executing = false;
      updateControls();
    }
  }

  function formatDate(value) {
    const date = dateValue(value);
    if (!Number.isFinite(date.getTime())) return '';
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date);
  }

  function receiptNode(receipt, compact = false) {
    const node = el('article', 'receipt');
    node.dataset.status = receipt.status || 'recorded';
    const heading = el('div', 'receipt-heading');
    const interrupted = ['failed', 'partial', 'interrupted'].includes(receipt.status);
    const symbol = icon(interrupted ? 'close' : receipt.status === 'undone' ? 'undo' : 'check');
    if (interrupted) symbol.classList.add('failed-icon');
    const content = el('div');
    content.append(el('h3', 'receipt-title', receipt.text || 'Completed action'));
    const meta = el('div', 'receipt-meta');
    const labels = { simulated: 'Rehearsal', done: 'Completed', partial: 'Partly completed', failed: 'Failed', undone: 'Undone', interrupted: 'Interrupted', running: 'Running' };
    meta.append(el('span', `mini-badge${interrupted ? ' warn' : ''}`, labels[receipt.status] || receipt.status || 'Recorded'));
    const date = el('time', 'receipt-date', formatDate(receipt.created_at));
    if (Number.isFinite(dateValue(receipt.created_at).getTime())) date.dateTime = dateValue(receipt.created_at).toISOString();
    meta.append(date);
    content.append(meta);
    heading.append(symbol, content);
    node.append(heading);
    if (!compact && Array.isArray(receipt.steps)) {
      const steps = el('ul', 'receipt-steps');
      receipt.steps.forEach((step) => {
        const row = el('li', 'receipt-step');
        row.append(el('strong', '', step.title || 'Step'), el('span', 'step-status', step.status || ''));
        if (step.summary) row.append(el('p', '', step.summary));
        if (step.undo_result) row.append(el('p', '', step.undo_result));
        steps.append(row);
      });
      node.append(steps);
    }
    if (!compact && receipt.notice) node.append(el('p', 'receipt-notice', receipt.notice));
    if (!compact && receipt.can_undo) {
      node.append(button('Undo available changes', 'ghost undo-button', (event) => undoReceipt(receipt, event.currentTarget), 'undo'));
    }
    return node;
  }

  function showReceipt(receipt) {
    $('plan-preview').hidden = true;
    $('plan-empty').hidden = true;
    const root = $('execution-result');
    root.replaceChildren();
    root.hidden = false;
    const titles = { simulated: 'Rehearsal complete', done: 'Completed', partial: 'Partly completed', failed: 'Action failed', undone: 'Changes undone', interrupted: 'Action interrupted' };
    root.append(el('h2', 'result-title', titles[receipt.status] || 'Action result'));
    root.append(el('p', 'result-subtitle', receipt.status === 'simulated' ? 'Your Mac was not changed.' : receipt.status === 'undone' ? 'Available changes were reversed; check each result below.' : 'Results for each action are recorded below.'));
    root.append(receiptNode(receipt));
    root.append(button('Next request', 'secondary', () => {
      root.hidden = true;
      $('plan-empty').hidden = false;
      $('plan-state').textContent = 'Ready';
      $('command-input').value = '';
      $('command-input').focus();
    }, 'arrow'));
    $('plan-state').textContent = titles[receipt.status] || 'Finished';
  }

  async function undoReceipt(receipt, control) {
    if (state.executing || !state.ready || !receipt.can_undo) return;
    state.executing = true;
    state.historyGeneration += 1;
    updateControls();
    control.disabled = true;
    control.querySelector('span').textContent = 'Undoing…';
    const errorTarget = state.view === 'activity' ? 'activity-error' : 'command-error';
    clearError(errorTarget);
    try {
      const result = await request('/api/undo', { id: receipt.id });
      receipt.can_undo = false;
      state.history = [result, ...state.history.filter((item) => item.id !== result.id)];
      renderHistory();
      if (state.view === 'command') showReceipt(result);
      announce('Undo finished. Check the receipt for each result.');
      refreshHistory();
    } catch (error) {
      showError(`${error.message} Refresh Activity to check which changes were undone.`, errorTarget, error.status === 403);
      control.querySelector('span').textContent = 'Check Activity';
    } finally {
      state.executing = false;
      updateControls();
    }
  }

  function emptyHistory() {
    const node = el('div', 'empty-history');
    const copy = el('div');
    copy.append(el('strong', '', 'No activity yet. Completed actions will appear here.'));
    node.append(el('span', 'empty-history-mark', '—'), copy);
    return node;
  }

  function renderHistory() {
    $('recent-receipts').replaceChildren();
    $('activity-list').replaceChildren();
    if (!state.history.length) {
      $('recent-receipts').append(emptyHistory());
      $('activity-list').append(emptyHistory());
    } else {
      state.history.slice(0, 2).forEach((item) => $('recent-receipts').append(receiptNode(item, true)));
      state.history.forEach((item) => $('activity-list').append(receiptNode(item)));
    }
    $('timers-list').replaceChildren();
    const timers = new Map([...state.timers, ...state.finishedTimers.values()].map((timer) => [timer.id, timer]));
    $('timers-section').hidden = !timers.size;
    timers.forEach((timer) => {
      const row = el('div', 'timer-row');
      row.append(icon('clock'), el('span', '', timer.label || timer.title || 'Timer'));
      const finish = timer.ends_at || timer.due_at || timer.end_at;
      const time = el('time', '', finish ? formatDate(finish) : displayValue(timer.status || 'Scheduled'));
      if (finish) {
        time.dataset.endsAt = String(dateValue(finish).getTime());
        time.dataset.timerId = timer.id;
        time.title = `Finishes ${formatDate(finish)}`;
      }
      row.append(time);
      $('timers-list').append(row);
    });
    updateTimers();
  }

  function updateTimers() {
    document.querySelectorAll('#timers-list time[data-ends-at]').forEach((node) => {
      const remaining = Math.max(0, Math.ceil((Number(node.dataset.endsAt) - Date.now()) / 1000));
      if (!Number.isFinite(remaining)) return;
      if (remaining === 0) {
        node.textContent = 'Finished';
        node.parentElement.classList.add('timer-finished');
        const id = node.dataset.timerId;
        if (!state.finishedTimers.has(id)) {
          const timer = state.timers.find((item) => item.id === id);
          if (timer) {
            state.finishedTimers.set(id, timer);
            announce(`${timer.label || 'Your timer'} has finished.`);
          }
        }
      } else {
        const hours = Math.floor(remaining / 3600);
        const minutes = Math.floor((remaining % 3600) / 60);
        const seconds = String(remaining % 60).padStart(2, '0');
        node.textContent = `${hours ? `${hours}:` : ''}${hours ? String(minutes).padStart(2, '0') : minutes}:${seconds} left`;
      }
    });
  }
  const timerTick = setInterval(updateTimers, 1000);

  async function refreshHistory() {
    if (!state.ready) return;
    const generation = ++state.historyGeneration;
    $('refresh-history').disabled = true;
    clearError('activity-error');
    try {
      const data = await request('/api/history');
      if (generation !== state.historyGeneration) return;
      state.history = Array.isArray(data.history) ? data.history : [];
      state.timers = Array.isArray(data.timers) ? data.timers : [];
      renderHistory();
    } catch (error) { showError(error.message, 'activity-error', error.status === 403); }
    finally { if (generation === state.historyGeneration) $('refresh-history').disabled = false; }
  }

  function releaseMicrophone() {
    clearTimeout(state.recordingTimer);
    if (state.stream) state.stream.getTracks().forEach((track) => track.stop());
    state.stream = null;
    state.recording = false;
    updateControls();
  }

  async function toggleVoice() {
    if (state.recording && state.recorder) {
      state.recorder.stop();
      return;
    }
    if (!state.ready || state.transcribing || state.executing || state.planning) return;
    const notice = $('voice-notice');
    notice.hidden = false;
    if (!state.voiceEnabled) {
      notice.replaceChildren(el('span', '', "Local voice is optional. Install with pip install -e '.[voice]', then start with JARVIS_VOICE=1 jev-jarvis. "));
      const link = el('a', 'text-link', 'See speech setup');
      link.href = '#setup';
      notice.append(link);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      notice.textContent = 'This browser cannot record audio here. Use a recent browser on localhost, or type your request.';
      return;
    }
    state.transcribing = true; // Also blocks a second start while permission is pending.
    updateControls();
    notice.textContent = 'Waiting for microphone access…';
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      state.stream = stream;
      const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find((type) => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      state.recorder = recorder;
      const chunks = [];
      let failed = false;
      recorder.addEventListener('dataavailable', (event) => { if (event.data.size) chunks.push(event.data); });
      recorder.addEventListener('error', () => {
        failed = true;
        state.transcribing = false;
        releaseMicrophone();
        notice.textContent = 'Recording failed. Your microphone is off. Try again or type your request.';
      });
      recorder.addEventListener('stop', async () => {
        releaseMicrophone();
        state.recorder = null;
        if (failed) return;
        const recording = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        if (!recording.size) {
          notice.textContent = 'No audio was captured. Try speaking again.';
          return;
        }
        state.transcribing = true;
        const inputAtStart = $('command-input').value;
        const generation = state.generation;
        updateControls();
        notice.textContent = 'Microphone off. Transcribing on your local server…';
        try {
          const result = await request('/api/transcribe', recording, { raw: true });
          if (!result.text?.trim()) throw new Error('No speech was recognized. Try again or type your request.');
          if ($('command-input').value !== inputAtStart || generation !== state.generation) {
            notice.replaceChildren(el('span', '', 'Your draft changed while transcribing. Transcript: '), el('strong', '', result.text));
            notice.append(button('Use this transcript', 'ghost', async () => {
              if (state.executing) return;
              await cancelPlan({ silent: true });
              $('command-input').value = result.text;
              state.generation += 1;
              notice.textContent = 'Transcript added. Review it, then preview the action.';
              $('command-input').focus();
            }));
          } else {
            await cancelPlan({ silent: true });
            $('command-input').value = result.text;
            state.generation += 1;
            notice.textContent = 'Transcript added. Review it, then preview the action.';
            $('command-input').focus();
          }
          announce('Transcription is ready to review. No plan has run.');
        } catch (error) {
          notice.textContent = error.message;
          if (error.status === 403) showError(error.message, 'command-error', true);
        } finally {
          state.transcribing = false;
          updateControls();
        }
      });
      state.transcribing = false;
      state.recording = true;
      recorder.start();
      state.recordingTimer = setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); }, 30000);
      notice.textContent = 'Listening for up to 30 seconds. Press Stop when you’re done.';
      updateControls();
      announce('Microphone on. Press the microphone button again to finish recording.');
    } catch (error) {
      state.transcribing = false;
      releaseMicrophone();
      notice.textContent = error.name === 'NotAllowedError' ? 'Microphone access was not granted. Allow it in browser settings, or type your request.' : `Could not start recording: ${error.message}`;
    }
  }

  $('command-form').addEventListener('submit', makePlan);
  $('command-input').addEventListener('input', () => {
    state.generation += 1;
    clearError();
    if (state.plan) {
      cancelPlan({ silent: true });
      announce('Draft changed. Make a fresh plan before approving.');
    }
  });
  document.querySelectorAll('[data-command]').forEach((node) => {
    node.addEventListener('click', () => chooseCommand(node.dataset.command || ''));
  });
  $('refresh-history').addEventListener('click', refreshHistory);
  $('voice-button').addEventListener('click', toggleVoice);
  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (location.hash !== '#command') history.pushState(null, '', '#command');
      setView('command');
      $('command-input').focus();
    }
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && state.view === 'command') {
      event.preventDefault();
      makePlan();
    }
    if (event.key === 'Escape' && !state.executing) {
      if (state.recording && state.recorder?.state === 'recording') state.recorder.stop();
      else if (state.plan || state.planning) {
        cancelPlan();
        state.generation += 1;
      }
    }
  });
  window.addEventListener('hashchange', () => setView(location.hash.slice(1), true));
  window.addEventListener('pagehide', () => {
    clearInterval(timerTick);
    clearTimeout(state.recordingTimer);
    if (state.stream) state.stream.getTracks().forEach((track) => track.stop());
  });
  if (!/Mac|iPhone|iPad/.test(navigator.platform)) $('shortcut-key').textContent = 'Ctrl';
  setView(location.hash.slice(1));
  bootstrap();
})();
