'use strict';

(() => {
  // Only literal Artifact text and a language enter; no credentials or Mission authority.
  const origin = window.location.origin;
  const workers = new Set();
  const objectUrls = new Set();
  const NativeWorker = window.Worker;
  const createObjectURL = URL.createObjectURL.bind(URL);
  const revokeObjectURL = URL.revokeObjectURL.bind(URL);
  let editor;
  let model;
  let channel;
  let pending;
  let disposed = false;

  if (NativeWorker) {
    window.Worker = class extends NativeWorker {
      constructor(...args) { super(...args); workers.add(this); }
      terminate() { workers.delete(this); return super.terminate(); }
    };
  }
  URL.createObjectURL = (blob) => { const url = createObjectURL(blob); objectUrls.add(url); return url; };
  URL.revokeObjectURL = (url) => { objectUrls.delete(url); revokeObjectURL(url); };

  function send(type) { window.parent.postMessage({ type, channel }, origin); }
  function dispose() {
    if (disposed) return;
    disposed = true;
    window.removeEventListener('message', receive);
    window.removeEventListener('keydown', keydown);
    window.removeEventListener('pagehide', dispose);
    for (const release of [() => editor?.dispose(), () => model?.dispose(), ...[...workers].map((worker) => () => worker.terminate()), ...[...objectUrls].map((url) => () => URL.revokeObjectURL(url))]) {
      try { release(); } catch { /* Release the remaining owned resources too. */ }
    }
    pending = undefined; editor = undefined; model = undefined;
  }
  function fail() {
    document.getElementById('error').hidden = false;
    send('artifact-code:error');
    dispose();
  }
  function render() {
    if (!pending || disposed || !window.monaco) return;
    try {
      editor?.dispose(); model?.dispose();
      model = window.monaco.editor.createModel(pending.text, pending.language);
      editor = window.monaco.editor.create(document.getElementById('editor'), {
        model, theme: 'vs-dark', readOnly: true, domReadOnly: true, tabFocusMode: true,
        automaticLayout: true, minimap: { enabled: false }, wordWrap: 'on',
        scrollBeyondLastLine: false, links: false, contextmenu: false,
        hover: { enabled: false }, parameterHints: { enabled: false },
        suggestOnTriggerCharacters: false, quickSuggestions: false,
        codeLens: false, lightbulb: { enabled: 'off' },
        renderValidationDecorations: 'off', accessibilitySupport: 'on',
        unicodeHighlight: { nonBasicASCII: false, ambiguousCharacters: false },
        ariaLabel: '只读产物文本', fontSize: 13,
      });
      pending = undefined;
      send('artifact-code:rendered');
    } catch { fail(); }
  }
  function receive(event) {
    if (disposed || event.source !== window.parent || event.origin !== origin || !event.data || typeof event.data !== 'object') return;
    const data = event.data;
    if (data.type === 'artifact-code:dispose' && channel && data.channel === channel) { dispose(); return; }
    if (data.type !== 'artifact-code:set' || typeof data.channel !== 'string' || data.channel.length > 128 || (channel && data.channel !== channel)) return;
    if (typeof data.text !== 'string' || data.text.includes('\0') || new TextEncoder().encode(data.text).byteLength > 262144) return;
    const languages = ['plaintext', 'json', 'markdown', 'python', 'javascript', 'typescript', 'css', 'html'];
    if (!languages.includes(data.language)) return;
    channel = data.channel;
    pending = { text: data.text, language: data.language };
    render();
  }
  function keydown(event) {
    if (event.key === 'Escape') { event.preventDefault(); send('artifact-code:escape'); }
  }
  window.addEventListener('message', receive);
  window.addEventListener('keydown', keydown);
  window.addEventListener('pagehide', dispose, { once: true });
  const loader = document.createElement('script');
  loader.src = '/monaco/0.55.1/vs/loader.js';
  loader.onerror = fail;
  loader.onload = () => {
    if (disposed) return;
    window.require.config({ paths: { vs: '/monaco/0.55.1/vs' } });
    window.require(['vs/editor/editor.main'], () => {
      if (disposed) return;
      window.monaco.languages.json.jsonDefaults.setDiagnosticsOptions({ validate: false, enableSchemaRequest: false });
      window.monaco.languages.typescript.typescriptDefaults.setDiagnosticsOptions({ noSemanticValidation: true, noSyntaxValidation: true, noSuggestionDiagnostics: true });
      window.monaco.languages.typescript.javascriptDefaults.setDiagnosticsOptions({ noSemanticValidation: true, noSyntaxValidation: true, noSuggestionDiagnostics: true });
      send('artifact-code:ready');
      render();
    }, fail);
  };
  document.head.appendChild(loader);
})();
