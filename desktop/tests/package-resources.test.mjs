import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cp, link, mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import test from 'node:test';
import { createManifest, parseArgs, verifyResources } from '../package-resource-manifest.mjs';

const execute = promisify(execFile);
const desktop = fileURLToPath(new URL('../', import.meta.url));
const frontend = resolve(desktop, '../frontend');
const runner = join(desktop, 'package-resource-manifest.mjs');
const wrapper = join(desktop, 'packaged-resources-smoke.ps1');
const commit = 'a'.repeat(40);
const monaco = 'frontend/public/monaco/0.55.1/';
const xterm = 'frontend/public/xterm/6.0.0/';
const digest = value => createHash('sha256').update(value).digest('hex');
const encode = value => `${JSON.stringify(value, null, 2)}\n`;
const rewrites = [
  ...['missions', 'chat', 'sessions', 'access'].map(name => ({ source: `/api/v1/${name}/:path*`, destination: `http://127.0.0.1:28000/api/v1/${name}/:path*` })),
  { source: '/api/admin/tools', destination: 'http://127.0.0.1:28001/api/admin/tools' },
  { source: '/api/admin/tools/:path*', destination: 'http://127.0.0.1:28001/api/admin/tools/:path*' },
  { source: '/api/:path*', destination: 'http://127.0.0.1:28000/api/:path*' },
  { source: '/platform/:path*', destination: 'http://127.0.0.1:28001/:path*' },
];
// Real Next standalone config shape: phases, not an invented flat originalRewrites.
const phases = rules => ({ beforeFiles: [], afterFiles: structuredClone(rules), fallback: [] });
const config = () => ({ output: 'standalone', distDir: './.next', _originalRewrites: phases(rewrites) });
const serverSource = value => `// Synthetic package fixture; never an executable acceptance claim.\nconst nextConfig = ${JSON.stringify(value)}\nprocess.env.__NEXT_PRIVATE_STANDALONE_CONFIG = JSON.stringify(nextConfig)\n`;

async function put(root, path, value) {
  const target = join(root, path);
  await mkdir(dirname(target), { recursive: true });
  await writeFile(target, value);
}
async function readJson(path) { return JSON.parse((await readFile(path, 'utf8')).replace(/^\uFEFF/, '')); }
async function editJson(root, path, edit) { const value = await readJson(join(root, path)); edit(value); await put(root, path, encode(value)); }
async function ownedDirectory(t) {
  const root = await mkdtemp(join(tmpdir(), 'agenthub-package-resource-test-'));
  t.after(async () => {
    assert.equal(dirname(resolve(root)), resolve(tmpdir()));
    assert.ok(root.split(sep).at(-1).startsWith('agenthub-package-resource-test-'));
    await rm(root, { recursive: true, force: true });
  });
  return root;
}

async function populate(services) {
  // Executables and Next chunks are explicitly synthetic. Real reviewed notices,
  // iframe sources and a locked package WOFF2 protect resource-style validation.
  for (const path of ['agenthub-mission-control.exe', 'agenthub-gateway.exe', 'agenthub-mcp-gateway.exe', 'frontend/node.exe']) {
    await put(services, path, 'MZsynthetic-resource-fixture-not-an-executable');
  }
  await put(services, 'stack-manifest.json', encode({ schemaVersion: 1, version: '0.0.0-synthetic-fixture', commit,
    generatedAt: '2026-10-18T00:00:00.000Z', services: ['agenthub-mission-control.exe', 'agenthub-gateway.exe', 'agenthub-mcp-gateway.exe', 'frontend/server.js'] }));
  await put(services, 'frontend/server.js', serverSource(config()));
  await put(services, 'frontend/.next/BUILD_ID', 'synthetic-resource-fixture');
  await put(services, 'frontend/.next/required-server-files.json', encode({ version: 1, config: config() }));
  await put(services, 'frontend/.next/routes-manifest.json', encode({ version: 3, rewrites: phases(rewrites.map(rule => ({ ...rule, regex: '^/fixture(?:/)?$' }))) }));
  await put(services, 'frontend/.next/static/chunks/main-fixture.js', '(self.webpackChunk_N_E=self.webpackChunk_N_E||[]).push([[1],{}]);');
  await put(services, 'frontend/.next/static/css/font-fixture.css', '@font-face{font-family:fixture;src:url(../media/material.woff2) format("woff2")}');
  await put(services, 'frontend/.next/static/media/material.woff2', await readFile(join(frontend, 'node_modules/@fontsource/material-symbols-outlined/files/material-symbols-outlined-latin-400-normal.woff2')));
  for (const path of ['vs/loader.js', 'vs/editor/editor.main.js', 'vs/editor/editor.main.css', ...['editor', 'json', 'css', 'html', 'ts'].map(label => `vs/assets/${label}.worker-fixture.js`)]) {
    await put(services, `${monaco}${path}`, path.endsWith('.css') ? '.fixture{color:navy}' : '/* Synthetic resource fixture. */');
  }
  for (const path of ['xterm.js', 'xterm.css']) await put(services, `${xterm}${path}`, '/* Synthetic resource fixture. */');
  const copied = [
    `${monaco}LICENSE`, `${monaco}ThirdPartyNotices.txt`,
    ...['_assistant-ui_react@0.15.29.LICENSE.txt', '_radix-ui_react-dialog@1.3.1.LICENSE.txt',
      '_fontsource_material-symbols-outlined@5.3.9.LICENSE.txt', 'shadcn-ui.LICENSE.txt', '_xterm_xterm@6.0.0.LICENSE.txt',
      'fontsource-material-symbols-outlined.SOURCE.txt', 'use-composed-ref@1.4.0.DECLARED-MIT-NOTICE.txt',
      'use-composed-ref@1.4.0.package.json'].map(name => `${monaco}notices/${name}`),
    `${xterm}LICENSE.txt`, `${xterm}SOURCE.txt`,
    ...['code', 'log'].flatMap(kind => ['html', 'js', 'css'].map(extension => `frontend/public/artifact-${kind}-frame.${extension}`)),
  ];
  for (const path of copied) await put(services, path, await readFile(join(frontend, path.slice('frontend/'.length))));
}

async function fixture(t, { seal = true } = {}) {
  const root = await ownedDirectory(t);
  const staging = join(root, 'staging'), application = join(root, 'extracted-app'), expected = join(root, 'trusted.json');
  await populate(staging);
  let manifest;
  if (seal) {
    manifest = await createManifest({ root: staging, sourceCommit: commit, output: expected });
    await cp(staging, join(application, 'local-services'), { recursive: true });
    for (const name of ['agenthub-desktop.exe', 'agenthub-runtime.exe']) await put(application, name, `synthetic-${name}-never-executed`);
  }
  return { root, staging, application, services: join(application, 'local-services'), expected, manifest };
}
const verify = fixture => verifyResources({ root: fixture.application, sourceCommit: commit, expected: fixture.expected });
async function cli(args) {
  try { return { code: 0, ...await execute(process.execPath, [runner, ...args], { windowsHide: true, timeout: 30000 }) }; }
  catch (error) { return { code: error.code, stdout: error.stdout, stderr: error.stderr }; }
}

test('synthetic directory fixture produces identical manifests and verifies only extracted resource bytes', async t => {
  const f = await fixture(t);
  assert.deepEqual(Object.keys(f.manifest).sort(), ['buildMode', 'files', 'schemaVersion', 'scope', 'sourceCommit']);
  assert.equal(f.manifest.scope, 'desktop-package-resources');
  assert.ok(f.manifest.files.every(file => file.path !== 'package-resources.json'));
  assert.deepEqual(await readFile(f.expected), await readFile(join(f.staging, 'package-resources.json')));
  await put(f.staging, 'unrelated-source-change.txt', 'Source is not extracted proof.');
  const result = await verify(f);
  assert.equal(result.filesVerified, f.manifest.files.length);
  assert.equal(result.expectedManifestSha256, digest(await readFile(f.expected)));
  const report = join(f.root, 'report.json');
  assert.equal((await cli(['verify', '--root', f.application, '--expected', f.expected, '--source-commit', commit, '--output', report])).code, 0);
  assert.deepEqual(await readJson(report), { schemaVersion: 1, scope: 'desktop-package-resources', sourceCommit: commit, buildMode: 'local-services',
    evidence: 'directory-resource-files-only', packagedApplication: false, nativeWebView2: false, realServicesVerified: false, ...result, status: 'passed' });
});

test('resource fixture can carry actual distributed Monaco, xterm, iframe and WOFF2 bytes', async t => {
  const f = await fixture(t, { seal: false });
  await cp(join(frontend, 'public/monaco/0.55.1'), join(f.staging, monaco), { recursive: true });
  await cp(join(frontend, 'public/xterm/6.0.0'), join(f.staging, xterm), { recursive: true });
  const manifest = await createManifest({ root: f.staging, sourceCommit: commit, output: f.expected });
  assert.equal(manifest.files.find(file => file.path === `${monaco}vs/editor/editor.main.js`).sha256,
    digest(await readFile(join(frontend, 'public/monaco/0.55.1/vs/editor/editor.main.js'))));
  assert.equal(manifest.files.find(file => file.path.endsWith('material.woff2')).sizeBytes, 323332);
});

test('missing, extra and same-size tampered extracted files fail; source files cannot repair them', async t => {
  for (const mutation of ['missing', 'extra', 'tampered']) await t.test(mutation, async t => {
    const f = await fixture(t);
    const path = join(f.services, 'frontend/.next/BUILD_ID');
    if (mutation === 'missing') await rm(path);
    else if (mutation === 'extra') await put(f.services, 'unexpected.txt', 'extra');
    else { const before = await readFile(path); before[0] ^= 1; await writeFile(path, before); }
    await assert.rejects(verify(f), /file set|differs from expected/);
  });
});

test('embedded manifest, external commit and exact external bytes remain independently trusted', async t => {
  for (const mutation of ['stale-commit', 'embedded-format', 'embedded-entry', 'expected-inside-root']) await t.test(mutation, async t => {
    const f = await fixture(t);
    if (mutation === 'stale-commit') await assert.rejects(verifyResources({ root: f.application, sourceCommit: 'b'.repeat(40), expected: f.expected }), /identity or commit/);
    else if (mutation === 'expected-inside-root') await assert.rejects(verifyResources({ root: f.application, sourceCommit: commit, expected: join(f.services, 'package-resources.json') }), /external trusted/);
    else {
      if (mutation === 'embedded-format') await writeFile(join(f.services, 'package-resources.json'), JSON.stringify(f.manifest));
      else await editJson(f.services, 'package-resources.json', value => { value.files[0].sizeBytes++; });
      await assert.rejects(verify(f), /Embedded manifest differs/);
    }
  });
});

test('manifest rejects traversal, case collision, self-inventory and extra shape fields', async t => {
  for (const mutation of ['traversal', 'backslash', 'case-collision', 'self', 'extra-field']) await t.test(mutation, async t => {
    const f = await fixture(t);
    const value = structuredClone(f.manifest);
    if (mutation === 'extra-field') value.syntheticSuccess = true;
    else if (mutation === 'case-collision') value.files.push({ ...value.files[0], path: value.files[0].path.toUpperCase() });
    else value.files[0].path = { traversal: '../outside', backslash: 'frontend\\outside', self: 'Package-Resources.json' }[mutation];
    await writeFile(f.expected, encode(value));
    await assert.rejects(verify(f), /invalid|canonical|unexpected/);
  });
});

test('create rejects a junction and hard link without following outside resources', async t => {
  const f = await fixture(t, { seal: false });
  const outside = join(f.root, 'outside');
  await mkdir(outside);
  await put(outside, 'private.txt', 'OUTSIDE-RESOURCE');
  await symlink(outside, join(f.staging, 'junction'), process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(createManifest({ root: f.staging, sourceCommit: commit, output: f.expected }), /links|junctions/);
  await rm(join(f.staging, 'junction'));
  await link(join(outside, 'private.txt'), join(f.staging, 'hard-link.txt'));
  await assert.rejects(createManifest({ root: f.staging, sourceCommit: commit, output: f.expected }), /Hard-linked/);
  assert.equal(await readFile(join(outside, 'private.txt'), 'utf8'), 'OUTSIDE-RESOURCE');
});

test('actual Next phase shape and every runtime rewrite source must agree with the local ports and order', async t => {
  for (const mutation of ['gateway-port', 'order', 'beforeFiles', 'fallback', 'conditional', 'runtime-only', 'runtime-env', 'distDir', 'routes']) await t.test(mutation, async t => {
    const f = await fixture(t, { seal: false });
    const path = 'frontend/.next/required-server-files.json';
    if (mutation === 'runtime-only') { const value = config(); value._originalRewrites.afterFiles[0].destination = 'http://evil.invalid/'; await put(f.staging, 'frontend/server.js', serverSource(value)); }
    else if (mutation === 'runtime-env') await put(f.staging, 'frontend/server.js', serverSource(config()).replace('JSON.stringify(nextConfig)', "'{}'"));
    else if (mutation === 'routes') await editJson(f.staging, 'frontend/.next/routes-manifest.json', value => { value.rewrites.afterFiles.pop(); });
    else await editJson(f.staging, path, value => {
      const phases = value.config._originalRewrites;
      if (mutation === 'gateway-port') phases.afterFiles[4].destination = 'http://127.0.0.1:28000/api/admin/tools';
      if (mutation === 'order') [phases.afterFiles[0], phases.afterFiles[6]] = [phases.afterFiles[6], phases.afterFiles[0]];
      if (mutation === 'beforeFiles' || mutation === 'fallback') phases[mutation].push(rewrites[0]);
      if (mutation === 'conditional') phases.afterFiles[0].has = [{ type: 'header', key: 'x-bypass' }];
      if (mutation === 'distDir') value.config.distDir = '../outside';
    });
    await assert.rejects(createManifest({ root: f.staging, sourceCommit: commit, output: f.expected }), /rewrites|rewrite|configuration|standalone/);
  });
});

test('Next routes flat compiled representation is supported alongside real phase config', async t => {
  const f = await fixture(t, { seal: false });
  await editJson(f.staging, 'frontend/.next/routes-manifest.json', value => { value.rewrites = value.rewrites.afterFiles; });
  await createManifest({ root: f.staging, sourceCommit: commit, output: f.expected });
});

test('supervisor stack metadata must retain the current commit and exact four entrypoints', async t => {
  for (const mutation of ['missing', 'schema', 'version', 'commit', 'services']) await t.test(mutation, async t => {
    const f = await fixture(t, { seal: false });
    if (mutation === 'missing') await rm(join(f.staging, 'stack-manifest.json'));
    else await editJson(f.staging, 'stack-manifest.json', value => {
      if (mutation === 'schema') value.schemaVersion = 2;
      if (mutation === 'version') value.version = ' ';
      if (mutation === 'commit') value.commit = 'b'.repeat(40);
      if (mutation === 'services') value.services.reverse();
    });
    await assert.rejects(createManifest({ root: f.staging, sourceCommit: commit, output: f.expected }), /Required|Supervisor stack/);
  });
});

test('required runtime assets, actual license text and declared-notice distinction are enforced', async t => {
  for (const mutation of ['missing-iframe', 'missing-worker', 'empty-xterm', 'fake-license', 'missing-notice', 'false-full-license', 'bad-exe']) await t.test(mutation, async t => {
    const f = await fixture(t, { seal: false });
    if (mutation === 'missing-iframe') await rm(join(f.staging, 'frontend/public/artifact-code-frame.html'));
    if (mutation === 'missing-worker') await rm(join(f.staging, `${monaco}vs/assets/ts.worker-fixture.js`));
    if (mutation === 'empty-xterm') await put(f.staging, `${xterm}xterm.js`, '');
    if (mutation === 'fake-license') await put(f.staging, `${monaco}notices/shadcn-ui.LICENSE.txt`, 'MIT');
    if (mutation === 'missing-notice') await rm(join(f.staging, `${monaco}notices/use-composed-ref@1.4.0.DECLARED-MIT-NOTICE.txt`));
    if (mutation === 'false-full-license') await put(f.staging, `${monaco}notices/use-composed-ref@1.4.0.LICENSE.txt`, 'MIT');
    if (mutation === 'bad-exe') await put(f.staging, 'frontend/node.exe', 'not-an-executable');
    await assert.rejects(createManifest({ root: f.staging, sourceCommit: commit, output: f.expected }), /Required|license|notice|MZ/);
  });
});

test('Next CSS must reference a real valid local WOFF2; orphan fonts and external URLs cannot pass', async t => {
  for (const mutation of ['missing-reference', 'orphan', 'external', 'invalid-font', 'invalid-chunk', 'empty-css']) await t.test(mutation, async t => {
    const f = await fixture(t, { seal: false });
    const css = 'frontend/.next/static/css/font-fixture.css';
    if (mutation === 'missing-reference') await put(f.staging, css, '@font-face{src:url(../media/missing.woff2)}');
    if (mutation === 'orphan') await put(f.staging, css, 'body{color:black}');
    if (mutation === 'external') await put(f.staging, css, '@font-face{src:url(https://fonts.invalid/font.woff2)}');
    if (mutation === 'invalid-font') await put(f.staging, 'frontend/.next/static/media/material.woff2', 'wOF2invalid');
    if (mutation === 'invalid-chunk') await put(f.staging, 'frontend/.next/static/chunks/main-fixture.js', 'function {');
    if (mutation === 'empty-css') await put(f.staging, css, '');
    await assert.rejects(createManifest({ root: f.staging, sourceCommit: commit, output: f.expected }), /CSS|WOFF2|Unexpected/);
  });
});

test('CLI rejects unknown and unsafe output arguments without replacing trusted input', async t => {
  assert.throws(() => parseArgs(['verify', '--root', 'a', '--root', 'b']), /duplicate/);
  assert.throws(() => parseArgs(['create', '--fixture', 'true']), /Unknown/);
  assert.throws(() => parseArgs(['create', '--root']), /missing/);
  const f = await fixture(t);
  const before = await readFile(f.expected);
  const result = await cli(['verify', '--root', f.application, '--expected', f.expected, '--source-commit', commit, '--output', f.expected]);
  assert.notEqual(result.code, 0);
  assert.deepEqual(await readFile(f.expected), before);
  await assert.rejects(createManifest({ root: f.staging, sourceCommit: commit, output: join(f.staging, 'bad.json') }), /external/);
});

test('CLI failure replaces a stale passed report and reports no native or service success', async t => {
  const f = await fixture(t);
  const output = join(f.root, 'report.json');
  await writeFile(output, encode({ status: 'passed', stale: true }));
  await rm(join(f.services, 'frontend/public/artifact-log-frame.html'));
  const result = await cli(['verify', '--root', f.application, '--expected', f.expected, '--source-commit', commit, '--output', output]);
  assert.notEqual(result.code, 0);
  const report = await readJson(output);
  assert.equal(report.status, 'failed');
  assert.equal(report.sourceCommit, commit);
  assert.equal(report.packagedApplication, false);
  assert.equal(report.nativeWebView2, false);
  assert.equal(report.realServicesVerified, false);
  assert.equal(report.stale, undefined);
  assert.equal(report.filesVerified, undefined);
});

const windows = process.platform === 'win32';
async function powershell(script, args = []) {
  // Core PowerShell's inherited module path can shadow Windows PowerShell's
  // built-in Utility module. Use the actual Windows host's normal defaults.
  const env = { ...process.env };
  for (const name of Object.keys(env)) if (name.toLowerCase() === 'psmodulepath') delete env[name];
  try { return { code: 0, ...await execute('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script, ...args], { windowsHide: true, timeout: 60000, env }) }; }
  catch (error) { return { code: error.code, stdout: error.stdout, stderr: error.stderr }; }
}
async function archive(f, badEntries) {
  const script = join(f.root, 'make-owned-fixture-zip.ps1'), zip = join(f.root, 'synthetic-package.zip');
  await writeFile(script, `param([string]$Source,[string]$Target,[string]$Entries)\n$ErrorActionPreference='Stop'\nAdd-Type -AssemblyName System.IO.Compression\nAdd-Type -AssemblyName System.IO.Compression.FileSystem\nif ($Entries) {\n  $zip=[IO.Compression.ZipFile]::Open($Target,[IO.Compression.ZipArchiveMode]::Create)\n  try { foreach($name in (Get-Content -LiteralPath $Entries -Raw | ConvertFrom-Json)) { $entry=$zip.CreateEntry($name); $writer=[IO.StreamWriter]::new($entry.Open()); try {$writer.Write('synthetic')} finally {$writer.Dispose()} } } finally {$zip.Dispose()}\n} else {\n  $next=Get-Item -LiteralPath (Join-Path $Source 'local-services/frontend/.next') -Force\n  $next.Attributes=$next.Attributes -bor [IO.FileAttributes]::Hidden\n  [IO.Compression.ZipFile]::CreateFromDirectory($Source,$Target,[IO.Compression.CompressionLevel]::Optimal,$false)\n}\n`);
  const args = ['-Source', f.application, '-Target', zip];
  if (badEntries) { const entries = join(f.root, 'entries.json'); await writeFile(entries, encode(badEntries)); args.push('-Entries', entries); }
  const result = await powershell(script, args);
  assert.equal(result.code, 0, result.stderr);
  return zip;
}
async function runWrapper(f, zip, appHash) {
  const output = join(f.root, 'wrapper-output');
  const result = await powershell(wrapper, ['-ArtifactPath', zip, '-Kind', 'Portable', '-ExpectedManifest', f.expected,
    '-SourceCommit', commit, '-ExpectedApplicationSha256', appHash ?? digest(await readFile(join(f.application, 'agenthub-desktop.exe'))),
    '-ExpectedSidecarSha256', digest(await readFile(join(f.application, 'agenthub-runtime.exe'))), '-OutputDirectory', output]);
  const names = await readdir(output);
  assert.equal(names.length, 1);
  const reportDirectory = join(output, names[0]);
  return { ...result, report: await readJson(join(reportDirectory, 'report.json')), reportDirectory };
}

test('Windows real ZIP extraction preserves hidden .next in a clearly synthetic package fixture', { skip: !windows }, async t => {
  const f = await fixture(t), zip = await archive(f);
  const result = await runWrapper(f, zip);
  assert.equal(result.code, 0, result.stderr);
  assert.equal(result.report.status, 'passed');
  assert.equal(result.report.sourceCommit, commit);
  assert.equal(result.report.packagedApplication, true);
  assert.equal(result.report.nativeWebView2, false);
  assert.equal(result.report.realServicesVerified, false);
  assert.equal(result.report.artifact.sha256, digest(await readFile(zip)));
  assert.equal(result.report.resourceReport.status, 'passed');
  assert.equal(result.report.resourceReport.packagedApplication, false);
  assert.equal(result.report.resourceReport.filesVerified, f.manifest.files.length);
});

test('Windows ZIP wrapper rejects unsafe paths and duplicate names before any resource success', { skip: !windows }, async t => {
  for (const entries of [['../outside-owned-test.txt'], ['safe.txt', 'SAFE.txt']]) await t.test(entries[0], async t => {
    const f = await fixture(t), zip = await archive(f, entries);
    const result = await runWrapper(f, zip);
    assert.notEqual(result.code, 0);
    assert.equal(result.report.status, 'failed');
    assert.match(result.report.error, /Unsafe ZIP path|Duplicate ZIP entry/);
    assert.equal(result.report.resourceReport, null);
  });
});

test('Windows ZIP wrapper checks actual root executable hash and extracted resources', { skip: !windows }, async t => {
  for (const mutation of ['root-exe-mismatch', 'missing-resource']) await t.test(mutation, async t => {
    const f = await fixture(t);
    if (mutation === 'missing-resource') await rm(join(f.services, 'frontend/public/artifact-log-frame.html'));
    const zip = await archive(f), result = await runWrapper(f, zip, mutation === 'root-exe-mismatch' ? '0'.repeat(64) : undefined);
    assert.notEqual(result.code, 0);
    assert.equal(result.report.status, 'failed');
    assert.match(result.report.error, /differs from this build|resource verification failed/);
    assert.equal(result.report.resourceReport, null);
    if (mutation === 'missing-resource') {
      const child = await readJson(join(result.reportDirectory, 'resources.json'));
      assert.equal(child.status, 'failed');
      assert.equal(child.sourceCommit, commit);
      assert.equal(child.nativeWebView2, false);
      assert.match(child.error, /file set/);
    }
  });
});
