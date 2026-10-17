/** Byte/resource gate only. It never starts an executable, service or WebView. */
import { createHash, randomBytes } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { lstat, mkdir, open, readFile, readdir, realpath, rename, unlink, writeFile } from 'node:fs/promises';
import { basename, dirname, isAbsolute, relative, resolve, posix } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Script } from 'node:vm';

const SCOPE = 'desktop-package-resources';
const EMBEDDED = 'package-resources.json';
const MONACO = 'frontend/public/monaco/0.55.1/';
const XTERM = 'frontend/public/xterm/6.0.0/';
const COMMIT = /^[a-f0-9]{40}$/;
const SHA = /^[a-f0-9]{64}$/;
// Exact distributed texts from the reviewed versions; normalize CRLF only.
const LICENSES = {
  [`${MONACO}LICENSE`]: '33e4ff1a06ef62ba21788ea162564ee8165269a24a9ce6ef301837447eab0ac6',
  [`${MONACO}ThirdPartyNotices.txt`]: '790537262fc78a764e121e6b92b959bcd3f5c310b47d9d9b9e92e17fe0af5336',
  [`${MONACO}notices/_assistant-ui_react@0.15.29.LICENSE.txt`]: 'eafb05991a572efa34097474b31fef45b14a8879ed1d1a8fd77de62e6fa7d275',
  [`${MONACO}notices/_radix-ui_react-dialog@1.3.1.LICENSE.txt`]: '0e80a2d229d2fd4fc7e8636142ec5d0ff0bc031f14c15b682e2ac01dfd5b5138',
  [`${MONACO}notices/_fontsource_material-symbols-outlined@5.3.9.LICENSE.txt`]: '18aabf190848725e2576eefb5c29ba06aac1029d02132252a7f312eac2e50cf3',
  [`${MONACO}notices/shadcn-ui.LICENSE.txt`]: '1564074e13439397221ffd522e2e504d56561994a23d371aa5e3ad43e4f5423f',
  [`${MONACO}notices/_xterm_xterm@6.0.0.LICENSE.txt`]: 'b569f629d00f2626a8100df2a1798210535621e42164dfd426a6fe5aac7b0ccd',
  [`${XTERM}LICENSE.txt`]: 'b569f629d00f2626a8100df2a1798210535621e42164dfd426a6fe5aac7b0ccd',
  [`${MONACO}notices/use-composed-ref@1.4.0.DECLARED-MIT-NOTICE.txt`]: '116522bb7efe796536f715e154eae462cc961884471f0749088ed347fdeed5a2',
};
const REWRITES = [
  ...['missions', 'chat', 'sessions', 'access'].map(name => ({ source: `/api/v1/${name}/:path*`, destination: `http://127.0.0.1:28000/api/v1/${name}/:path*` })),
  { source: '/api/admin/tools', destination: 'http://127.0.0.1:28001/api/admin/tools' },
  { source: '/api/admin/tools/:path*', destination: 'http://127.0.0.1:28001/api/admin/tools/:path*' },
  { source: '/api/:path*', destination: 'http://127.0.0.1:28000/api/:path*' },
  { source: '/platform/:path*', destination: 'http://127.0.0.1:28001/:path*' },
];

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const json = value => `${JSON.stringify(value, null, 2)}\n`;
const fail = message => { throw new Error(message); };
const key = name => name.normalize('NFC').toLowerCase();
const samePath = (a, b) => process.platform === 'win32' ? key(a) === key(b) : a === b;
const within = (root, target) => { const part = relative(root, target); return part === '' || !part.startsWith('..') && !isAbsolute(part); };

function safeRelative(name) {
  if (typeof name !== 'string' || !name || name.includes('\\') || /[:\x00-\x1f\x7f]/.test(name)
      || name.startsWith('/') || name.split('/').some(part => !part || part === '.' || part === '..' || /[. ]$/.test(part))) {
    fail('Resource path is not a canonical relative path');
  }
  return name;
}

async function checkedPath(path, { directory = false } = {}) {
  const absolute = resolve(path);
  // Check every ancestor: a regular file reached through a junction is unsafe.
  let ancestor = absolute;
  for (;;) {
    const entry = await lstat(ancestor);
    if (entry.isSymbolicLink()) fail('Symbolic links and junctions are not accepted');
    if (dirname(ancestor) === ancestor) break;
    ancestor = dirname(ancestor);
  }
  const entry = await lstat(absolute);
  if (directory ? !entry.isDirectory() : !entry.isFile()) fail('Resource is not the required regular file or directory');
  if (!directory && entry.nlink !== 1) fail('Hard-linked resources are not accepted');
  // Windows TEMP can use a harmless 8.3 alias. Ancestors above were checked
  // before resolving it, so normalize that alias instead of rejecting it.
  return { absolute: await realpath(absolute), entry };
}

async function fileHash(path) {
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(path)) digest.update(chunk);
  return digest.digest('hex');
}

async function inventory(root) {
  await checkedPath(root, { directory: true });
  const files = [], seen = new Set();
  async function walk(directory, prefix = '') {
    for (const name of (await readdir(directory)).sort()) {
      const path = safeRelative(prefix + name);
      if (seen.has(key(path))) fail('Duplicate or case-colliding resource path');
      seen.add(key(path));
      const absolute = resolve(root, path);
      if (!within(root, absolute)) fail('Resource escapes its root');
      const entry = await lstat(absolute);
      if (entry.isSymbolicLink()) fail('Symbolic links and junctions are not accepted');
      if (entry.isDirectory()) { await walk(absolute, `${path}/`); continue; }
      await checkedPath(absolute);
      if (key(path) === key(EMBEDDED) && path !== EMBEDDED) fail('Embedded manifest has a case-colliding path');
      if (path === EMBEDDED) continue;
      const sha256 = await fileHash(absolute);
      const after = await lstat(absolute);
      if (entry.size !== after.size || entry.mtimeMs !== after.mtimeMs || entry.ino !== after.ino) fail('Resource changed while being read');
      files.push({ path, sizeBytes: entry.size, sha256 });
    }
  }
  await walk(root);
  return files.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
}

async function text(root, path) { return readFile(resolve(root, safeRelative(path)), 'utf8'); }
async function readJson(root, path) { return JSON.parse((await text(root, path)).replace(/^\uFEFF/, '')); }

function requireFiles(files, names) {
  const table = new Map(files.map(file => [file.path, file]));
  for (const name of names) if (!table.get(name)?.sizeBytes) fail(`Required nonempty resource missing: ${name}`);
  return table;
}

function rewriteList(value, compiled = false) {
  if (!Array.isArray(value)) {
    if (!value || Object.keys(value).sort().join() !== 'afterFiles,beforeFiles,fallback'
        || !Array.isArray(value.beforeFiles) || value.beforeFiles.length
        || !Array.isArray(value.fallback) || value.fallback.length) fail('Baked rewrite phases are invalid');
    value = value.afterFiles;
  }
  if (!Array.isArray(value)) fail('Baked rewrites must be ordered');
  return value.map(rule => {
    if (!rule || Object.keys(rule).some(name => !['source', 'destination', ...(compiled ? ['regex'] : [])].includes(name))) fail('Unexpected conditional or additional rewrite fields');
    return { source: rule.source, destination: rule.destination };
  });
}

async function validateRewrites(root) {
  const required = await readJson(root, 'frontend/.next/required-server-files.json');
  const routes = await readJson(root, 'frontend/.next/routes-manifest.json');
  const server = await text(root, 'frontend/server.js');
  const literals = [...server.matchAll(/^const nextConfig = (\{[^\r\n]+\})\s*$/gm)];
  if (literals.length !== 1 || [...server.matchAll(/process\.env\.__NEXT_PRIVATE_STANDALONE_CONFIG\s*=/g)].length !== 1
      || !/^process\.env\.__NEXT_PRIVATE_STANDALONE_CONFIG = JSON\.stringify\(nextConfig\)\s*$/m.test(server)) fail('Standalone runtime configuration is missing or ambiguous');
  // Parse the generated JSON literal; never evaluate or start packaged JavaScript.
  const runtime = JSON.parse(literals[0][1]);
  for (const config of [required.config, runtime]) {
    if (config?.output !== 'standalone' || !['.next', './.next'].includes(config.distDir)) fail('Frontend must be a standalone build using .next');
    if (JSON.stringify(rewriteList(config._originalRewrites)) !== JSON.stringify(REWRITES)) fail('Baked rewrites differ from the ordered local-services origins');
  }
  if (JSON.stringify(rewriteList(routes.rewrites, true)) !== JSON.stringify(REWRITES)) fail('Baked rewrites differ from the ordered local-services origins');
}

async function validateFontsAndStatic(root, files, table) {
  const chunks = files.filter(file => /^frontend\/\.next\/static\/chunks\/.+\.js$/.test(file.path));
  const styles = files.filter(file => /^frontend\/\.next\/static\/css\/.+\.css$/.test(file.path));
  if (!chunks.length || !styles.length) fail('Next static JavaScript and CSS are required');
  for (const chunk of chunks) {
    const content = await text(root, chunk.path);
    if (!content.trim()) fail('Next JavaScript chunk is empty');
    new Script(content, { filename: chunk.path }); // Compile only; never execute package code.
  }
  const fonts = new Set();
  for (const style of styles) {
    const css = await text(root, style.path);
    if (!css.trim() || !css.includes('{') || !css.includes('}')) fail('Next CSS resource is empty or malformed');
    for (const match of css.matchAll(/url\(\s*(?:"([^"]*)"|'([^']*)'|([^\s'"\)]+))\s*\)/gi)) {
      const value = match[1] ?? match[2] ?? match[3];
      if (/^data:/i.test(value)) continue;
      if (/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(value)) fail('CSS resource URL is external');
      const url = decodeURIComponent(value.split(/[?#]/, 1)[0]);
      const path = url.startsWith('/_next/') ? `frontend/.next/${url.slice(7)}`
        : url.startsWith('/') ? `frontend/public${url}` : posix.normalize(posix.join(posix.dirname(style.path), url));
      safeRelative(path);
      if (!path.startsWith('frontend/') || !table.get(path)?.sizeBytes) fail('CSS references a missing local resource');
      if (path.endsWith('.woff2')) fonts.add(path);
    }
  }
  if (!fonts.size) fail('Next CSS must reference a shipped WOFF2 font');
  for (const path of fonts) {
    const handle = await open(resolve(root, path), 'r');
    try {
      const header = Buffer.alloc(48);
      const { bytesRead } = await handle.read(header, 0, 48, 0);
      if (bytesRead !== 48 || header.toString('ascii', 0, 4) !== 'wOF2' || header.readUInt32BE(8) !== table.get(path).sizeBytes
          || header.readUInt16BE(12) === 0 || header.readUInt16BE(14) !== 0) fail('Shipped WOFF2 header is invalid');
    } finally { await handle.close(); }
  }
}

async function validateResources(root, files, sourceCommit) {
  const executables = ['agenthub-mission-control.exe', 'agenthub-gateway.exe', 'agenthub-mcp-gateway.exe', 'frontend/node.exe'];
  const table = requireFiles(files, [...executables, 'stack-manifest.json', 'frontend/server.js', 'frontend/.next/BUILD_ID',
    'frontend/.next/required-server-files.json', 'frontend/.next/routes-manifest.json',
    `${MONACO}vs/loader.js`, `${MONACO}vs/editor/editor.main.js`, `${MONACO}vs/editor/editor.main.css`,
    `${XTERM}xterm.js`, `${XTERM}xterm.css`, `${XTERM}SOURCE.txt`,
    ...Object.keys(LICENSES), `${MONACO}notices/fontsource-material-symbols-outlined.SOURCE.txt`,
    `${MONACO}notices/use-composed-ref@1.4.0.package.json`,
    ...['code', 'log'].flatMap(kind => ['html', 'js', 'css'].map(extension => `frontend/public/artifact-${kind}-frame.${extension}`)),
  ]);
  for (const path of executables) {
    const handle = await open(resolve(root, path), 'r');
    try {
      const signature = Buffer.alloc(2);
      await handle.read(signature, 0, 2, 0);
      if (signature.toString('ascii') !== 'MZ') fail('Packaged service or Node executable lacks an MZ header');
    } finally { await handle.close(); }
  }
  for (const label of ['editor', 'json', 'css', 'html', 'ts']) {
    if (!files.some(file => file.sizeBytes && new RegExp(`^${MONACO.replaceAll('.', '\\.')}vs/assets/${label}\\.worker-[^/]+\\.js$`).test(file.path))) fail(`Required Monaco worker missing: ${label}`);
  }
  for (const [path, expected] of Object.entries(LICENSES)) {
    if (hash((await text(root, path)).replace(/\r\n/g, '\n')) !== expected) fail(`Reviewed license or declaration text differs: ${path}`);
  }
  const declaration = await readJson(root, `${MONACO}notices/use-composed-ref@1.4.0.package.json`);
  if (declaration.name !== 'use-composed-ref' || declaration.version !== '1.4.0' || declaration.license !== 'MIT'
      || files.some(file => file.path.startsWith(`${MONACO}notices/use-composed-ref@1.4.0.LICENSE`))) fail('Declared MIT notice must remain distinct from a full upstream license');
  const stack = await readJson(root, 'stack-manifest.json');
  if (stack.schemaVersion !== 1 || typeof stack.version !== 'string' || !stack.version.trim() || stack.commit !== sourceCommit
      || JSON.stringify(stack.services) !== JSON.stringify([...executables.slice(0, 3), 'frontend/server.js'])) fail('Supervisor stack manifest has an invalid version, commit or service entrypoints');
  await validateRewrites(root);
  await validateFontsAndStatic(root, files, table);
}

function validateManifest(manifest, commit) {
  if (!manifest || Object.keys(manifest).sort().join() !== ['buildMode', 'files', 'schemaVersion', 'scope', 'sourceCommit'].sort().join()
      || manifest.schemaVersion !== 1 || manifest.scope !== SCOPE || manifest.buildMode !== 'local-services'
      || manifest.sourceCommit !== commit || !Array.isArray(manifest.files) || !manifest.files.length) fail('Expected resource manifest has invalid identity or commit');
  const seen = new Set();
  for (const file of manifest.files) {
    if (!file || Object.keys(file).sort().join() !== 'path,sha256,sizeBytes') fail('Resource entry has unexpected fields');
    const path = safeRelative(file.path);
    if (key(path) === key(EMBEDDED) || seen.has(key(path)) || !Number.isSafeInteger(file.sizeBytes) || file.sizeBytes < 0
        || typeof file.sha256 !== 'string' || !SHA.test(file.sha256)) fail('Resource manifest has an invalid or duplicate entry');
    seen.add(key(path));
  }
}

async function outputPath(output, root, expected) {
  if (!output) fail('Output is required');
  const requested = resolve(output);
  await mkdir(dirname(requested), { recursive: true });
  const parent = await checkedPath(dirname(requested), { directory: true });
  const target = resolve(parent.absolute, basename(requested));
  const canonical = async path => realpath(resolve(path)).catch(error => { if (error.code !== 'ENOENT') throw error; return resolve(path); });
  if (within(await canonical(root), target) || expected && samePath(target, await canonical(expected))) fail('Output must be external to resources and distinct from trusted expected input');
  try { await checkedPath(target); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  return target;
}

async function atomicWrite(path, value) {
  const temporary = `${path}.${randomBytes(8).toString('hex')}.tmp`;
  try { await writeFile(temporary, json(value), { flag: 'wx' }); await rename(temporary, path); }
  finally { await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; }); }
}

export async function createManifest({ root, sourceCommit, output }) {
  if (!COMMIT.test(sourceCommit)) fail('Source commit must be 40 lowercase hexadecimal characters');
  root = (await checkedPath(root, { directory: true })).absolute;
  const target = await outputPath(output, root);
  const files = await inventory(root);
  await validateResources(root, files, sourceCommit);
  const manifest = { schemaVersion: 1, scope: SCOPE, sourceCommit, buildMode: 'local-services', files };
  // The embedded copy is deliberately excluded from its own file inventory.
  await atomicWrite(resolve(root, EMBEDDED), manifest);
  await atomicWrite(target, manifest);
  return manifest;
}

export async function verifyResources({ root, sourceCommit, expected }) {
  if (!COMMIT.test(sourceCommit)) fail('Source commit must be 40 lowercase hexadecimal characters');
  root = (await checkedPath(root, { directory: true })).absolute;
  expected = (await checkedPath(expected)).absolute;
  if (within(root, expected)) fail('Expected manifest must be an external trusted input');
  for (const name of ['agenthub-desktop.exe', 'agenthub-runtime.exe']) {
    if (!(await checkedPath(resolve(root, name))).entry.size) fail('Extracted root executable is empty');
  }
  const services = resolve(root, 'local-services');
  const externalBytes = await readFile(expected);
  const manifest = JSON.parse(externalBytes.toString('utf8').replace(/^\uFEFF/, ''));
  validateManifest(manifest, sourceCommit);
  await checkedPath(resolve(services, EMBEDDED));
  const embeddedBytes = await readFile(resolve(services, EMBEDDED));
  const embedded = JSON.parse(embeddedBytes.toString('utf8').replace(/^\uFEFF/, ''));
  validateManifest(embedded, sourceCommit);
  if (!embeddedBytes.equals(externalBytes)) fail('Embedded manifest differs from trusted external manifest');
  const files = await inventory(services);
  const actual = new Map(files.map(file => [file.path, file]));
  if (actual.size !== manifest.files.length) fail('Extracted resource file set differs from expected');
  for (const file of manifest.files) {
    const found = actual.get(file.path);
    if (!found || found.sizeBytes !== file.sizeBytes || found.sha256 !== file.sha256) fail(`Extracted resource differs from expected: ${file.path}`);
  }
  await validateResources(services, files, sourceCommit);
  return { filesVerified: files.length, bytesVerified: files.reduce((sum, file) => sum + file.sizeBytes, 0), expectedManifestSha256: await fileHash(resolve(expected)) };
}

export function parseArgs(argv) {
  const operation = argv[0];
  if (!['create', 'verify'].includes(operation)) fail('Expected create or verify operation');
  const options = { operation }, allowed = new Set(['--root', '--source-commit', '--output', ...(operation === 'verify' ? ['--expected'] : [])]);
  for (let index = 1; index < argv.length; index += 2) {
    const flag = argv[index], value = argv[index + 1];
    if (!allowed.delete(flag) || !value || value.startsWith('--')) fail('Unknown, duplicate or missing command argument');
    options[flag.slice(2).replace(/-([a-z])/g, (_all, letter) => letter.toUpperCase())] = value;
  }
  for (const name of ['root', 'sourceCommit', 'output', ...(operation === 'verify' ? ['expected'] : [])]) if (!options[name]) fail(`Missing command argument: ${name}`);
  return options;
}

async function cli(argv) {
  let options;
  try {
    options = parseArgs(argv);
    const root = resolve(options.root);
    const target = await outputPath(options.output, root, options.expected);
    if (options.operation === 'create') { await createManifest(options); return; }
    const report = { schemaVersion: 1, scope: SCOPE, sourceCommit: options.sourceCommit, buildMode: 'local-services',
      evidence: 'directory-resource-files-only', packagedApplication: false, nativeWebView2: false, realServicesVerified: false,
      ...await verifyResources(options), status: 'passed' };
    await atomicWrite(target, report);
  } catch (error) {
    const report = { schemaVersion: 1, scope: SCOPE, sourceCommit: options?.sourceCommit ?? null,
      buildMode: 'local-services', status: 'failed', packagedApplication: false,
      nativeWebView2: false, realServicesVerified: false, error: error instanceof Error ? error.message : String(error) };
    if (options) {
      try { await atomicWrite(await outputPath(options.output, resolve(options.root), options.expected), report); }
      catch { /* Unsafe/unwritable output never overwrites the trusted input. */ }
    }
    console.error(JSON.stringify(report));
    process.exitCode = 1;
  }
}

if (process.argv[1] && samePath(resolve(process.argv[1]), fileURLToPath(import.meta.url))) await cli(process.argv.slice(2));
