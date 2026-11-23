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
function chooseExample(name) {
  if ($('editor').value !== examples[previousExample] && !confirm('Replace your current script with this example?')) {
    $('example').value = previousExample;
    return;
  }
  replaceSource(examples[name]);
  previousExample = name;
  $('example').value = name;
  $('example-description').textContent = descriptions[name];
  document.querySelectorAll('[data-example]').forEach(button => {
    const selected = button.dataset.example === name;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  $('editor').focus();
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
  const link = document.createElement('a'); link.href = url; link.download = 'playground.scripta'; link.click();
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
let saved;
try { saved = localStorage.getItem('scripta-source'); } catch (_) {}
replaceSource(saved == null ? examples.hello : saved.replace(/^<\?(?:scr|src)\b\s*/, ''));
refreshConsole();
toggleRun(false);
if (!token) setStatus('Open the full URL from the launcher to connect.', 'idle');
