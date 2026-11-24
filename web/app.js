'use strict';
const $ = id => document.getElementById(id);
const examples = {
  hello: '$name = "Scripta";\necho "Hello, " . $name . "!\\n";\n',
  loops: '$total = 0;\nfor ($i = 1; $i <= 5; $i += 1) {\n    $total += $i;\n    echo str($i) . "\\n";\n}\necho "Total: " . str($total) . "\\n";\n',
  arrays: '$items = ["apple", "banana", "orange"];\n$items[] = "pear";\n\nforeach ($items as $item) {\n    echo $item . "\\n";\n}\necho Json::encode($items) . "\\n";\n',
  functions: 'function greet($name: string): string {\n    return "Hello, " . $name . "!";\n}\n\necho greet("world") . "\\n";\n'
};
const descriptions = {
  hello: 'Start with a greeting, then take it somewhere new.',
  loops: 'A few numbers, a simple loop, and a little repetition.',
  arrays: 'Collect a few things. Give your data a shape.',
  functions: 'Turn a good idea into something you can use again.'
};
let token = location.hash.slice(1);
try {
  if (token) sessionStorage.setItem('playground-token', token);
  else token = sessionStorage.getItem('playground-token') || '';
} catch (_) {}
history.replaceState(null, '', '/');
let running = false;
let closed = false;
let previousExample = 'hello';
let previousSource = examples.hello;
const appScripts = new Map();
let activeTab = 'output';
let lastPainted = null;
let lastRun = false;
let copyTimer;
const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
document.querySelectorAll('.modifier').forEach(element => { element.textContent = isMac ? '⌘' : 'Ctrl'; });

// Highlight for display only. Every token is inserted as text, never as HTML.
function paintCode(source) {
  if (source === lastPainted) return;
  lastPainted = source;
  const pattern = /("(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*')|(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?(?:\*\/|$))|(\$[A-Za-z_]\w*)|\b(function|return|echo|for|foreach|as|if|else|elseif|while|do|break|continue|true|false|null|try|catch|finally|throw|require|require_once|int|float|string|bool|void|list|map|callable)\b|\b(\d+(?:\.\d+)?)\b|\b([A-Z][A-Za-z0-9_]*)(?=::)|\b([A-Za-z_]\w*)(?=\s*\()/g;
  const classes = ['string', 'comment', 'variable', 'keyword', 'number', 'type', 'function'];
  const fragment = document.createDocumentFragment();
  let offset = 0;
  for (const match of source.matchAll(pattern)) {
    if (match.index > offset) fragment.append(document.createTextNode(source.slice(offset, match.index)));
    const span = document.createElement('span');
    const group = match.slice(1).findIndex(value => value !== undefined);
    span.className = `token-${classes[group]}`;
    span.textContent = match[0];
    fragment.append(span);
    offset = match.index + match[0].length;
  }
  fragment.append(document.createTextNode(source.slice(offset) + '\n'));
  $('highlight').replaceChildren(fragment);
}
function updateCursor() {
  const editor = $('editor');
  const before = editor.value.slice(0, editor.selectionStart);
  const line = before.split('\n').length;
  const column = before.length - before.lastIndexOf('\n');
  $('cursor').textContent = `Ln ${line}, Col ${column}`;
  $('active-line').style.top = `${24 + (line - 1) * 25 - editor.scrollTop}px`;
}
function syncScroll() {
  const editor = $('editor');
  $('highlight').style.transform = `translate(${-editor.scrollLeft}px, ${-editor.scrollTop}px)`;
  $('line-numbers').style.transform = `translateY(${-editor.scrollTop}px)`;
  updateCursor();
}
function updateEditor() {
  const source = $('editor').value;
  const count = source.split('\n').length;
  $('lines').textContent = `${count} ${count === 1 ? 'line' : 'lines'}`;
  $('line-numbers').textContent = Array.from({length: count}, (_, index) => index + 1).join('\n');
  paintCode(source);
  updateCursor();
  try { localStorage.setItem('scripta-source', source); } catch (_) {}
}
function replaceSource(source) {
  $('editor').value = source;
  $('editor').scrollTop = 0;
  $('editor').scrollLeft = 0;
  $('editor').setSelectionRange(0, 0);
  updateEditor();
  syncScroll();
}
function setStatus(text, state) {
  $('status').textContent = text;
  if (state) {
    $('run-state').dataset.state = state;
    $('run-state').textContent = {idle:'Ready', running:'Running', success:'Completed', error:'Needs attention', closed:'Closed'}[state];
  }
}
function setEmpty(title, description, showRun = false) {
  $('empty-title').textContent = title;
  $('empty-description').textContent = description;
  $('empty-run').hidden = !showRun;
}
function refreshConsole() {
  const errors = activeTab === 'errors';
  $('output-content').hidden = errors;
  $('error-content').hidden = !errors;
  for (const name of ['output', 'errors']) {
    const selected = name === activeTab;
    $(`tab-${name}`).classList.toggle('active', selected);
    $(`tab-${name}`).setAttribute('aria-selected', String(selected));
    $(`tab-${name}`).tabIndex = selected ? 0 : -1;
  }
  const text = $(errors ? 'stderr' : 'stdout').textContent;
  $('empty').hidden = Boolean(text) || running;
  $('copy').disabled = !text;
  if (!text) {
    if (errors) setEmpty('A little peace and quiet.', 'No errors to show. Keep experimenting.');
    else if (lastRun) setEmpty('All done. Nothing to print.', 'Use echo to send something to the console.');
    else setEmpty('Good things start with a run.', 'Your script’s output will appear here.\nMake a change and see what happens.', true);
  }
}
function selectTab(name, focus = false) {
  activeTab = name;
  refreshConsole();
  if (focus) $(`tab-${name}`).focus();
}
function updateCounts() {
  for (const [id, source] of [['output-count', 'stdout'], ['error-count', 'stderr']]) {
    const text = $(source).textContent.trimEnd();
    $(id).textContent = text ? text.split('\n').length : 0;
    $(id).hidden = !text;
  }
}
function clearOutput() {
  $('stdout').textContent = '';
  $('stderr').textContent = '';
  lastRun = false;
  updateCounts();
  selectTab('output');
}
async function request(path, body = {}) {
  if (!token) throw new Error('Session missing. Open the full URL printed by the playground launcher.');
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json', 'X-Playground-Token':token}, body:JSON.stringify(body)});
  if (!response.ok) {
    if (response.status === 403) throw new Error('Session expired. Open the full URL printed by the launcher.');
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Request failed (${response.status})`);
  }
  return response.json();
}
function toggleRun(value) {
  running = value;
  $('run').disabled = value || closed || !token;
  $('empty-run').disabled = value || closed || !token;
  $('stop').disabled = !value || closed;
  $('clear').disabled = value;
  $('run-label').textContent = value ? 'Running…' : 'Run script';
  refreshConsole();
}
async function run() {
  if (running || closed || !token) return;
  clearOutput();
  toggleRun(true);
  setStatus('Running your script…', 'running');
  try {
    const result = await request('/run', {source:$('editor').value});
    if (result.error) throw new Error(result.error);
    $('stdout').textContent = result.stdout;
    $('stderr').textContent = result.stderr;
    if (result.reason) $('stderr').textContent += `${result.stderr ? '\n' : ''}${result.reason}\n`;
    lastRun = true;
    const failed = result.exitCode !== 0 || Boolean(result.reason);
    setStatus(`${result.reason || (failed ? `Exit code ${result.exitCode ?? 'unknown'}` : 'Finished successfully')} · ${result.elapsedMs} ms`, failed ? 'error' : 'success');
    updateCounts();
    selectTab(failed && $('stderr').textContent ? 'errors' : 'output');
  } catch (error) {
    $('stderr').textContent = error.message;
    lastRun = true;
    updateCounts();
    selectTab('errors');
    setStatus('Could not run script', 'error');
  } finally {
    toggleRun(false);
    if (closed) setStatus('Session closed. You can close this tab.', 'closed');
  }
}
async function chooseExample(name) {
  if ($('editor').value !== previousSource && !confirm('Replace your current script with this script?')) {
    $('example').value = previousExample;
    return;
  }
  const filename = appScripts.get(name);
  let source = examples[name];
  if (filename) {
    try {
      source = (await request('/script', {name: filename})).source;
    } catch (error) {
      $('example').value = previousExample;
      setStatus(error.message, 'error');
      return;
    }
  }
  replaceSource(source);
  previousSource = source;
  previousExample = name;
  $('example').value = name;
  $('example-description').textContent = filename ? `From app_scripts / ${filename}` : descriptions[name];
  $('editor-title').textContent = filename || 'playground.scripta';
  document.querySelectorAll('[data-example]').forEach(button => {
    const selected = button.dataset.example === name;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  $('editor').focus();
}
async function loadAppScripts() {
  if (!token) return;
  try {
    const result = await request('/scripts');
    const group = document.createElement('optgroup');
    group.label = 'Your scripts';
    for (const filename of result.scripts) {
      const key = `file:${filename}`;
      appScripts.set(key, filename);
      const button = document.createElement('button');
      button.className = 'example-card';
      button.dataset.example = key;
      button.setAttribute('aria-pressed', 'false');
      const icon = document.createElement('span');
      icon.className = 'example-icon';
      icon.textContent = '>_';
      const label = document.createElement('span');
      const title = document.createElement('strong');
      title.textContent = filename;
      label.append(title);
      button.append(icon, label);
      button.addEventListener('click', () => chooseExample(key));
      $('app-scripts').append(button);
      const option = document.createElement('option');
      option.value = key;
      option.textContent = filename;
      group.append(option);
    }
    if (result.scripts.length) {
      $('example').append(group);
      $('app-scripts-count').textContent = result.scripts.length;
      $('app-scripts-section').hidden = false;
    }
  } catch (error) { setStatus(`Could not load app_scripts: ${error.message}`); }
}
$('run').addEventListener('click', run);
$('empty-run').addEventListener('click', run);
$('stop').addEventListener('click', async () => {
  $('stop').disabled = true;
  try { await request('/stop'); } catch (error) { setStatus(error.message, 'error'); }
});
$('clear').addEventListener('click', () => { if (!running) { clearOutput(); setStatus('Console cleared', closed ? 'closed' : 'idle'); } });
$('example').addEventListener('change', () => chooseExample($('example').value));
document.querySelectorAll('[data-example]').forEach(button => button.addEventListener('click', () => chooseExample(button.dataset.example)));
for (const name of ['output', 'errors']) {
  $(`tab-${name}`).addEventListener('click', () => selectTab(name));
  $(`tab-${name}`).addEventListener('keydown', event => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      selectTab(event.key === 'Home' ? 'output' : event.key === 'End' ? 'errors' : activeTab === 'output' ? 'errors' : 'output', true);
    }
  });
}
$('copy').addEventListener('click', async () => {
  const text = $(activeTab === 'errors' ? 'stderr' : 'stdout').textContent;
  try {
    await navigator.clipboard.writeText(text);
    $('copy').setAttribute('aria-label', 'Copied to clipboard');
    $('copy').title = 'Copied!';
    setStatus('Copied to clipboard');
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => { $('copy').setAttribute('aria-label', 'Copy visible output'); $('copy').title = 'Copy output'; }, 1800);
  } catch (_) { setStatus('Select the output text and copy it with your keyboard.'); }
});
$('editor').addEventListener('input', updateEditor);
$('editor').addEventListener('scroll', syncScroll);
for (const type of ['keyup', 'click', 'select']) $('editor').addEventListener(type, updateCursor);
$('editor').addEventListener('keydown', event => {
  if (event.key === 'Tab') {
    event.preventDefault();
    const editor = event.target;
    editor.setRangeText('    ', editor.selectionStart, editor.selectionEnd, 'end');
    updateEditor();
  }
});
$('font-size').addEventListener('change', () => {
  document.documentElement.style.setProperty('--code-size', `${$('font-size').value}px`);
  syncScroll();
});
$('save').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([$('editor').value], {type:'text/plain;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = $('editor-title').textContent; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  setStatus('Script downloaded');
});
for (const id of ['load', 'load-mobile']) $(id).addEventListener('click', () => $('file').click());
$('file').addEventListener('change', async () => {
  const file = $('file').files[0]; if (!file) return;
  if (file.size > 256 * 1024) { setStatus('Choose a script smaller than 256 KiB.'); $('file').value = ''; return; }
  if (confirm('Replace your current script with this file?')) {
    try { replaceSource(await file.text()); $('editor').focus(); setStatus(`Opened ${file.name}`); }
    catch (error) { setStatus(error.message, 'error'); }
  }
  $('file').value = '';
});
$('quit').addEventListener('click', async () => {
  if (!confirm('Quit the local playground? Any running script will stop.')) return;
  try {
    await request('/shutdown'); closed = true; toggleRun(false); $('quit').disabled = true;
    setStatus('Session closed. You can close this tab.', 'closed');
    document.querySelector('.connection').textContent = 'Session closed';
  } catch (error) { setStatus(error.message, 'error'); }
});
for (const id of ['guide-open', 'session-details']) $(id).addEventListener('click', () => $('guide').showModal());
$('guide-close').addEventListener('click', () => $('guide').close());
$('guide').addEventListener('click', event => { if (event.target === $('guide')) {
  const rect = $('guide').getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $('guide').close();
} });
document.addEventListener('keydown', event => {
  if ($('guide').open) return;
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); run(); }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); $('save').click(); }
});
const workspace = document.querySelector('.workspace');
const divider = $('workspace-divider');
let editorShare = 0.7;
try {
  const stored = Number(localStorage.getItem('scripta-editor-share'));
  if (stored >= 0.2 && stored <= 0.8) editorShare = stored;
} catch (_) {}
function resizeWorkspace(share = editorShare, persist = false) {
  const available = workspace.clientHeight - divider.offsetHeight;
  if (available <= 0) return;
  const minimum = Math.max(0.2, 280 / available);
  const maximum = Math.min(0.8, 1 - 220 / available);
  editorShare = Math.max(minimum, Math.min(maximum, share));
  workspace.style.setProperty('--editor-fr', `${editorShare}fr`);
  workspace.style.setProperty('--console-fr', `${1 - editorShare}fr`);
  divider.setAttribute('aria-valuemin', Math.ceil(minimum * 100));
  divider.setAttribute('aria-valuemax', Math.floor(maximum * 100));
  divider.setAttribute('aria-valuenow', Math.round(editorShare * 100));
  divider.setAttribute('aria-valuetext', `Editor ${Math.round(editorShare * 100)}%, console ${Math.round((1 - editorShare) * 100)}%`);
  if (persist) {
    try { localStorage.setItem('scripta-editor-share', editorShare); } catch (_) {}
  }
  syncScroll();
}
let resizePointer = null;
let resizeOffset = 0;
divider.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  event.preventDefault();
  resizePointer = event.pointerId;
  resizeOffset = event.clientY - divider.getBoundingClientRect().top;
  divider.setPointerCapture(event.pointerId);
  divider.focus();
  document.body.classList.add('resizing-workspace');
});
divider.addEventListener('pointermove', event => {
  if (resizePointer !== event.pointerId) return;
  const top = workspace.getBoundingClientRect().top + workspace.clientTop;
  resizeWorkspace((event.clientY - top - resizeOffset) / (workspace.clientHeight - divider.offsetHeight));
});
function finishWorkspaceResize() {
  if (resizePointer === null) return;
  resizePointer = null;
  document.body.classList.remove('resizing-workspace');
  resizeWorkspace(editorShare, true);
}
divider.addEventListener('pointerup', finishWorkspaceResize);
divider.addEventListener('pointercancel', finishWorkspaceResize);
divider.addEventListener('lostpointercapture', finishWorkspaceResize);
divider.addEventListener('dblclick', () => resizeWorkspace(0.7, true));
divider.addEventListener('keydown', event => {
  const step = event.shiftKey ? 0.1 : 0.02;
  const share = {ArrowUp: editorShare - step, ArrowDown: editorShare + step, Home: 0.2, End: 0.8}[event.key];
  if (share === undefined) return;
  event.preventDefault();
  resizeWorkspace(share, true);
});
new ResizeObserver(() => resizeWorkspace()).observe(workspace);
resizeWorkspace();
let saved;
try { saved = localStorage.getItem('scripta-source'); } catch (_) {}
replaceSource(saved == null ? examples.hello : saved.replace(/^<\?(?:scr|src)\b\s*/, ''));
refreshConsole();
toggleRun(false);
if (!token) setStatus('Open the full URL from the launcher to connect.', 'idle');
loadAppScripts();
