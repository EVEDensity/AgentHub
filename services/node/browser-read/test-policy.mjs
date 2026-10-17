import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { parseInput, requestUrl, canonicalOrigin, limitedText } from './policy.mjs';

const fixture = () => ({ schemaVersion: 1, requestId: '9e2b3872-4086-4656-9802-26c54a36faee',
  origins: ['https://example.com'], url: 'https://example.com/docs', timeoutMs: 15000, maxTextChars: 512 });

test('only the exact bounded request shape is admitted', () => {
  assert.equal(parseInput(fixture()).url, 'https://example.com/docs');
  for (const patch of [{ timeoutMs: 0 }, { timeoutMs: 30001 }, { timeoutMs: true },
    { maxTextChars: 1 }, { maxTextChars: 65537 }, { schemaVersion: 2 },
    { script: 'not supported' }, { requestId: 'model-selected-id' },
    { origins: [] }, { origins: ['https://example.com', 'https://example.com'] }]) {
    assert.throws(() => parseInput({ ...fixture(), ...patch }));
  }
});

test('origins have no credentials, paths, wildcards or implicit port aliases', () => {
  for (const origin of ['https://u:p@example.com', 'https://example.com/',
    'https://example.com:443', 'https://example.com/a', 'file:///etc/passwd', '*.example.com']) {
    assert.throws(() => canonicalOrigin(origin));
  }
});

test('URL cannot add origin, credentials, fragments, file protocol or backslashes', () => {
  for (const url of ['https://other.example/docs', 'https://u:p@example.com/',
    'https://example.com/#secret', 'file:///etc/passwd', 'https://example.com/\\path',
    'https://example.com/ bad', 'https://example.com/a/../b', 'https://example.com/%2e%2e/b',
    'https://example.com/中文', "https://example.com/?q='bad'"]) {
    assert.throws(() => requestUrl(url, fixture().origins));
  }
});

test('text bounds count Unicode codepoints and digest the returned UTF-8', () => {
  const result = limitedText('a😀中z', 3);
  assert.equal(result.text, 'a😀中');
  assert.equal(result.textBytes, 9);
  assert.equal(result.truncated, true);
  assert.equal(result.textSha256, 'sha256:' + createHash('sha256').update('a😀中').digest('hex'));
});
