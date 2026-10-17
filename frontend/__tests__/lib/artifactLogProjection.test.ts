import { expect, it } from 'vitest';
import { LOG_MAX_CHARACTERS, LOG_MAX_LINE_CHARACTERS, LOG_LINE_MARKER, projectArtifactLog } from '../../lib/artifacts/artifactLogProjection';

it('keeps CJK, emoji and source text literal without evaluating HTML or URLs', () => {
  const text = '真实日志 中文 😀\n<img src=https://untrusted.test onerror=execute()>\nhttps://untrusted.test';
  expect(projectArtifactLog(text)).toEqual({ text, sourceLines: 3, omittedLines: 0, truncatedLines: 0, escapedControls: 0 });
});

it('neutralizes every C0/C1/DEL control while keeping only LF and generated TAB spaces', () => {
  const input = Array.from({ length: 160 }, (_, code) => String.fromCodePoint(code)).join('');
  const result = projectArtifactLog(input);
  expect(result.text).not.toMatch(/[\x00-\x09\x0b-\x1f\x7f-\x9f]/);
  for (const code of [...Array.from({ length: 32 }, (_, n) => n), ...Array.from({ length: 33 }, (_, n) => n + 127)]) {
    if ([9, 10].includes(code)) continue;
    expect(result.text).toContain(`[U+${code.toString(16).toUpperCase().padStart(4, '0')}]`);
  }
  expect(result.escapedControls).toBe(63);
  expect(result.text).toContain('    \n');
});

it('cannot erase or rewrite prior observations or activate OSC/DSR/link/clipboard data', () => {
  const text = 'failure\rPASS\b!\x1b[2J\x1b[1;1H\x1b[6n\x1b]52;c;c2VjcmV0\x07\x1b]8;;https://untrusted.test\x1b\\link\x1b]8;;\x1b\\';
  const result = projectArtifactLog(text);
  expect(result.text).toContain('failure[U+000D]PASS[U+0008]!');
  expect(result.text).toContain('[U+001B]]52;c;c2VjcmV0[U+0007]');
  expect(result.text).toContain('[U+001B][6n');
  expect(result.text).not.toMatch(/[\x00-\x1f\x7f-\x9f]/);
});

it('makes bidi and zero-width format changes visible, including CRLF conversion differences', () => {
  const result = projectArtifactLog('name\u202egnp.exe\u2066\u200b\u200f\u061c\ufeff\r\nnext\tcolumn');
  expect(result.text).toBe('name[U+202E]gnp.exe[U+2066][U+200B][U+200F][U+061C][U+FEFF][U+000D]\nnext    column');
});

it('keeps a bounded tail with an accurate omission count', () => {
  const result = projectArtifactLog(Array.from({ length: 2500 }, (_, i) => 'line-' + i).join('\n'));
  expect(result.sourceLines).toBe(2500); expect(result.omittedLines).toBe(500);
  expect(result.text.split('\n')).toHaveLength(2000);
  expect(result.text.startsWith('line-500\n')).toBe(true); expect(result.text.endsWith('line-2499')).toBe(true);
});

it('clips long lines at code points with an explicit marker and bounds expanded control text', () => {
  const result = projectArtifactLog('😀'.repeat(2100));
  expect(result.text).toBe('😀'.repeat(LOG_MAX_LINE_CHARACTERS - Array.from(LOG_LINE_MARKER).length) + LOG_LINE_MARKER);
  expect(Array.from(result.text).length).toBe(LOG_MAX_LINE_CHARACTERS);
  expect(result.truncatedLines).toBe(1);
  const expanded = projectArtifactLog(Array.from({ length: 140 }, () => '\x1b'.repeat(1800)).join('\n'));
  expect(Array.from(expanded.text).length).toBeLessThanOrEqual(LOG_MAX_CHARACTERS);
  expect(expanded.omittedLines).toBeGreaterThan(0);
  expect(expanded.truncatedLines).toBe(expanded.sourceLines - expanded.omittedLines);
  expect(expanded.text).not.toContain('\x1b');
});

it('shows empty content without inventing lines of output and refuses oversized raw input', () => {
  expect(projectArtifactLog('')).toEqual({ text: '', sourceLines: 1, omittedLines: 0, truncatedLines: 0, escapedControls: 0 });
  expect(() => projectArtifactLog('x'.repeat(262145))).toThrow('超过 256 KiB');
  expect(() => projectArtifactLog('\ud800')).toThrow('有效的 UTF-8');
});
