'use strict';

(() => {
  const origin = window.location.origin;
  const host = document.getElementById('terminal-host');
  let terminal;
  let channel;
  let lastText;
  let completed = false;
  let disposed = false;
  let version = 0;

  function send(type) { window.parent.postMessage({ type, channel }, origin); }
  function dispose() {
    if (disposed) return;
    disposed = true;
    ++version;
    window.removeEventListener('message', receive);
    window.removeEventListener('keydown', keydown);
    window.removeEventListener('pagehide', dispose);
    document.removeEventListener('paste', blockInput, true);
    document.removeEventListener('drop', blockInput, true);
    try { terminal?.dispose(); } catch { /* Frame removal is the final lifecycle boundary. */ }
    terminal = undefined;
    lastText = undefined;
    host.replaceChildren();
  }
  function fail() {
    document.getElementById('error').hidden = false;
    send('artifact-log:error');
    dispose();
  }
  function printable(text) {
    if (typeof text !== 'string' || text.length > 200000) return false;
    let characters = 0;
    let lines = 1;
    let lineCharacters = 0;
    for (const character of text) {
      if (++characters > 100000) return false;
      const code = character.codePointAt(0);
      if (code === 10) { if (++lines > 2000) return false; lineCharacters = 0; continue; }
      if (++lineCharacters > 2000) return false;
      if (code < 32 || code >= 127 && code <= 159 || code === 0xad || code === 0x61c
          || code >= 0x200b && code <= 0x200f || code >= 0x2028 && code <= 0x202e
          || code >= 0x2060 && code <= 0x206f || code === 0xfeff || code >= 0xd800 && code <= 0xdfff) return false;
    }
    return true;
  }
  function render(text) {
    if (text === lastText) { if (completed) send('artifact-log:rendered'); return; }
    lastText = text;
    completed = false;
    const current = ++version;
    try {
      terminal?.dispose();
      host.replaceChildren();
      terminal = new window.Terminal({
        cols: 80, rows: 24, scrollback: 6000, disableStdin: true,
        cursorBlink: false, screenReaderMode: true, convertEol: false,
        windowOptions: {}, linkHandler: { activate: () => {} }, logLevel: 'off',
        fontSize: 13, fontFamily: 'ui-monospace, Consolas, monospace',
        theme: { background: '#121418', foreground: '#E4E7EC', cursor: '#121418', selectionBackground: '#43546b' },
      });
      terminal.attachCustomKeyEventHandler(() => false);
      terminal.open(host);
      if (terminal.textarea) {
        terminal.textarea.readOnly = true;
        terminal.textarea.setAttribute('aria-label', '登记日志只读浏览，不能输入命令');
      }
      // Only trusted LF-to-CRLF layout conversion enters the parser. The parent
      // projection and this frame both reject EVERY source control character.
      terminal.write(text.replace(/\n/g, '\r\n'), () => {
        if (disposed || current !== version) return;
        completed = true;
        terminal.scrollToTop();
        send('artifact-log:rendered');
      });
    } catch { fail(); }
  }
  function receive(event) {
    if (disposed || event.source !== window.parent || event.origin !== origin || !event.data || typeof event.data !== 'object') return;
    const data = event.data;
    if (data.type === 'artifact-log:dispose' && channel && data.channel === channel) { dispose(); return; }
    // Dialog focus loops target the frame itself. Enter the owned read-only
    // text area only for that explicit, authenticated parent focus request.
    if (data.type === 'artifact-log:focus' && channel && data.channel === channel) { terminal?.focus(); return; }
    if (data.type !== 'artifact-log:set' || typeof data.channel !== 'string' || !data.channel || data.channel.length > 128 || channel && data.channel !== channel || !printable(data.text)) return;
    channel = data.channel;
    render(data.text);
  }
  function keydown(event) {
    if (event.key === 'Escape') { event.preventDefault(); send('artifact-log:escape'); }
  }
  function blockInput(event) { event.preventDefault(); event.stopImmediatePropagation(); }
  window.addEventListener('message', receive);
  window.addEventListener('keydown', keydown);
  window.addEventListener('pagehide', dispose, { once: true });
  document.addEventListener('paste', blockInput, true);
  document.addEventListener('drop', blockInput, true);
  send('artifact-log:ready');
})();
