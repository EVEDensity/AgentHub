import { MAX_PREVIEW_BYTES } from './missionArtifactPreview';

export const LOG_MAX_LINES = 2000;
export const LOG_MAX_LINE_CHARACTERS = 2000;
export const LOG_MAX_CHARACTERS = 100_000;
export const LOG_LINE_MARKER = ' [行已截断]';

export interface ArtifactLogProjection {
  text: string;
  sourceLines: number;
  omittedLines: number;
  truncatedLines: number;
  escapedControls: number;
}

function control(code: number): boolean {
  return code < 32 || code >= 127 && code <= 159 || code === 0xad || code === 0x61c
    || code >= 0x200b && code <= 0x200f || code >= 0x2028 && code <= 0x202e
    || code >= 0x2060 && code <= 0x206f || code === 0xfeff;
}

function visibleLine(line: string) {
  const tokens: Array<{ text: string; length: number; escaped: boolean }> = [];
  let characters = 0;
  let escaped = 0;
  let truncated = false;
  for (const character of line) {
    const code = character.codePointAt(0)!;
    const escapedControl = control(code) && code !== 9;
    const token = code === 9 ? '    ' : escapedControl ? `[U+${code.toString(16).toUpperCase().padStart(4, '0')}]` : character;
    const length = Array.from(token).length;
    if (characters + length > LOG_MAX_LINE_CHARACTERS) { truncated = true; break; }
    tokens.push({ text: token, length, escaped: escapedControl });
    characters += length;
    if (escapedControl) ++escaped;
  }
  const markerLength = Array.from(LOG_LINE_MARKER).length;
  while (truncated && characters + markerLength > LOG_MAX_LINE_CHARACTERS) {
    const removed = tokens.pop()!;
    characters -= removed.length;
    if (removed.escaped) --escaped;
  }
  const text = tokens.map(token => token.text).join('') + (truncated ? LOG_LINE_MARKER : '');
  return { text, characters: characters + (truncated ? Array.from(LOG_LINE_MARKER).length : 0), escaped, truncated };
}

/** Display transformation after the ORIGINAL bytes have passed Artifact verification.
 * No source escape sequence, carriage return or bidi format control reaches xterm.
 * Deliberately show CR as data, including the CR in CRLF. Hashes never describe this projection.
 */
export function projectArtifactLog(text: string): ArtifactLogProjection {
  const bytes = new TextEncoder().encode(text);
  if (bytes.length > MAX_PREVIEW_BYTES) throw new Error('日志超过 256 KiB，无法显示');
  if (new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes) !== text) throw new Error('日志不是有效的 UTF-8 文本');
  const source = text.split('\n');
  const lines: string[] = [];
  let characters = 0;
  let escapedControls = 0;
  let truncatedLines = 0;
  for (let index = source.length - 1; index >= Math.max(0, source.length - LOG_MAX_LINES); --index) {
    const line = visibleLine(source[index]);
    const size = line.characters + (lines.length ? 1 : 0);
    if (characters + size > LOG_MAX_CHARACTERS) break;
    lines.push(line.text);
    characters += size;
    escapedControls += line.escaped;
    if (line.truncated) ++truncatedLines;
  }
  return { text: lines.reverse().join('\n'), sourceLines: source.length,
    omittedLines: source.length - lines.length, truncatedLines, escapedControls };
}
