// Original AgentHub code. SPDX-License-Identifier: Apache-2.0
import { createHash } from 'node:crypto';

export const PLAYWRIGHT_VERSION = '1.61.1';
export const CHROMIUM_VERSION = '149.0.7827.55';
export const SCHEMA = 'agenthub.browser-read.v1';

export function check(condition, code = 'invalid_input') {
  if (!condition) throw new Error(code);
}

export function canonicalOrigin(value) {
  check(typeof value === 'string' && value.length <= 512);
  const url = new URL(value);
  check(['http:', 'https:'].includes(url.protocol) && !url.username && !url.password);
  check(value === url.origin && !url.hash && !url.search);
  check(url.protocol !== 'http:' || ['127.0.0.1', '[::1]'].includes(url.hostname));
  return url.origin;
}

export function requestUrl(value, origins) {
  check(typeof value === 'string' && value.length <= 2048);
  check(/^[A-Za-z0-9:/.?=&%_~!$()*+,;@\[\]-]+$/.test(value));
  const url = new URL(value);
  check(['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && !url.hash);
  check(origins.includes(url.origin), 'origin_denied');
  check(url.href === value);
  check(!url.pathname.split('/').some(part => ['.', '..'].includes(decodeURIComponent(part))));
  return url.href;
}

export function parseInput(value) {
  check(value && typeof value === 'object' && !Array.isArray(value));
  const fields = ['schemaVersion', 'requestId', 'url', 'origins', 'timeoutMs', 'maxTextChars'];
  check(Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field)));
  check(value.schemaVersion === 1);
  check(typeof value.requestId === 'string' && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value.requestId));
  check(Array.isArray(value.origins) && value.origins.length >= 1 && value.origins.length <= 8);
  const origins = value.origins.map(canonicalOrigin);
  check(new Set(origins).size === origins.length);
  check(Number.isInteger(value.timeoutMs) && value.timeoutMs >= 1 && value.timeoutMs <= 30000);
  check(Number.isInteger(value.maxTextChars) && value.maxTextChars >= 256 && value.maxTextChars <= 65536);
  return Object.freeze({ ...value, origins: Object.freeze(origins), url: requestUrl(value.url, origins) });
}

export function limitedText(text, maximum) {
  const chars = Array.from(text);
  const content = chars.slice(0, maximum).join('');
  return {
    text: content,
    textSha256: 'sha256:' + createHash('sha256').update(content, 'utf8').digest('hex'),
    textBytes: Buffer.byteLength(text, 'utf8'),
    truncated: chars.length > maximum,
  };
}

export const ERROR_CODES = new Set([
  'invalid_input', 'origin_denied', 'runtime_mismatch', 'browser_launch_failed',
  'navigation_failed', 'deadline_exceeded', 'http_error', 'redirect_rejected',
  'document_type_unsupported', 'download_rejected', 'output_limited', 'cleanup_failed',
]);

export function safeError(error) {
  return ERROR_CODES.has(error?.message) ? error.message : 'navigation_failed';
}
