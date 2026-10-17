import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { copyFile, mkdir, mkdtemp, readFile, realpath, rm, symlink, unlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, parse, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import test from 'node:test';

const execute = promisify(execFile);
const scripts = fileURLToPath(new URL('../local-services/', import.meta.url));
const windowsOnly = { skip: process.platform !== 'win32' && 'Actual Windows PowerShell failure paths' };
const environmentNames = ['API_BACKEND', 'API_BACKEND_URL', 'MISSION_CONTROL_URL', 'GO_GATEWAY_URL', 'GOCACHE', 'NEXT_DIST_DIR', 'PYTHONPATH'];
const inheritedValues = Object.fromEntries(environmentNames.map((name, index) => [name, index === 4 ? null : `inherited ${name} value ${index}`]));

async function subprocess(command, args, options = {}) {
  try {
    return { ...await execute(command, args, { timeout: 30_000, windowsHide: true, ...options }), code: 0 };
  } catch (error) {
    if (error.killed || error.signal || typeof error.code !== 'number') throw error;
    return { stdout: error.stdout, stderr: error.stderr, code: error.code };
  }
}

async function fixture(t) {
  const owner = await mkdtemp(join(tmpdir(), 'agenthub-local-build-test-'));
  const junctions = [];
  t.after(async () => {
    // Delete only this freshly allocated temporary root; unlink junctions first.
    assert.equal(dirname(resolve(owner)), resolve(tmpdir()));
    assert.ok(owner.split(sep).at(-1).startsWith('agenthub-local-build-test-'));
    for (const path of junctions) await unlink(path).catch(error => { if (error.code !== 'ENOENT') throw error; });
    await rm(owner, { recursive: true, force: true });
  });
  const root = join(owner, 'checkout with spaces');
  const tools = join(owner, 'failing tools');
  const caller = join(owner, 'caller with spaces');
  const source = join(root, 'desktop', 'local-services');
  const stage = join(root, 'desktop', 'src-tauri', 'local-services');
  await Promise.all([mkdir(source, { recursive: true }), mkdir(tools), mkdir(caller), mkdir(join(root, 'frontend', 'public'), { recursive: true })]);
  for (const name of ['build-windows.ps1', 'build-mission-control.ps1']) await copyFile(join(scripts, name), join(source, name));
  await Promise.all([
    writeFile(join(root, '.gitignore'), '.tmp/\ndesktop/src-tauri/local-services/\nfrontend/.next/\nfrontend/public/monaco/\nfrontend/public/xterm/\n'),
    writeFile(join(root, 'mission_control_entrypoint.py'), '# Committed test source; never executed.\n'),
    writeFile(join(root, 'frontend', 'source-sentinel.txt'), 'SOURCE MUST SURVIVE'),
    writeFile(join(root, 'frontend', 'public', 'tracked.txt'), 'TRACKED PUBLIC SOURCE'),
    // Import is only a dependency probe. Every actual freeze fails, without an executable.
    writeFile(join(tools, 'python.cmd'), '@echo off\r\n>>"%AGENTHUB_BUILD_TEST_LOG%" echo python:%*\r\nif "%~1"=="-c" exit /b 0\r\nexit /b 37\r\n'),
    writeFile(join(tools, 'npm.cmd'), '@echo off\r\n>>"%AGENTHUB_BUILD_TEST_LOG%" echo UNEXPECTED_NPM:%*\r\nexit /b 83\r\n'),
    writeFile(join(tools, 'go.cmd'), '@echo off\r\n>>"%AGENTHUB_BUILD_TEST_LOG%" echo UNEXPECTED_GO:%*\r\nexit /b 84\r\n'),
  ]);
  for (const args of [
    ['init', '-q'], ['config', 'gc.auto', '0'], ['config', 'maintenance.auto', 'false'],
    ['config', 'core.autocrlf', 'false'], ['add', '.'],
    ['-c', 'user.name=Owned Build Test', '-c', 'user.email=owned-build@example.invalid', 'commit', '-qm', 'Committed failure-path fixture'],
  ]) {
    const result = await subprocess('git.exe', args, { cwd: root });
    assert.equal(result.code, 0, result.stderr);
  }
  await mkdir(stage, { recursive: true });
  await Promise.all([
    writeFile(join(stage, 'agenthub-mission-control.exe'), 'STALE EXECUTABLE MUST NOT BE REUSED'),
    writeFile(join(stage, 'old-stage-marker.txt'), 'PREVIOUS STAGE'),
    writeFile(join(stage, 'stack-manifest.json'), '{"stale":true}'),
  ]);
  const harness = join(owner, 'invoke failure.ps1');
  await writeFile(harness, String.raw`$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Set-Location -LiteralPath $env:AGENTHUB_BUILD_TEST_CALLER
$values = $env:AGENTHUB_BUILD_TEST_VALUES | ConvertFrom-Json
$before = [ordered]@{}
foreach ($property in $values.PSObject.Properties) {
    [Environment]::SetEnvironmentVariable($property.Name, $property.Value, 'Process')
    $before[$property.Name] = [Environment]::GetEnvironmentVariable($property.Name, 'Process')
}
$beforeLocation = (Get-Location).Path
$failure = $null
try {
    & $env:AGENTHUB_BUILD_TEST_SCRIPT -OutputDirectory $env:AGENTHUB_BUILD_TEST_OUTPUT -PythonExecutable $env:AGENTHUB_BUILD_TEST_PYTHON
} catch { $failure = $_.Exception.Message }
$after = [ordered]@{}
foreach ($property in $values.PSObject.Properties) { $after[$property.Name] = [Environment]::GetEnvironmentVariable($property.Name, 'Process') }
$result = [ordered]@{failed=($null -ne $failure);error=$failure;before=$before;after=$after;locationBefore=$beforeLocation;locationAfter=(Get-Location).Path;nativeExit=$LASTEXITCODE}
[Console]::Out.WriteLine('BUILD_TEST_RESULT=' + ($result | ConvertTo-Json -Depth 5 -Compress))
if ($null -ne $failure) { exit 1 }
exit 0
`);
  async function invoke(name = 'build-windows.ps1', output = stage) {
    const pathKey = Object.keys(process.env).find(name => name.toLowerCase() === 'path') || 'PATH';
    const env = { ...process.env, [pathKey]: [tools, dirname(process.execPath), process.env[pathKey]].join(';'),
      AGENTHUB_BUILD_TEST_SCRIPT: join(source, name), AGENTHUB_BUILD_TEST_OUTPUT: output,
      AGENTHUB_BUILD_TEST_PYTHON: join(tools, 'python.cmd'), AGENTHUB_BUILD_TEST_CALLER: caller,
      AGENTHUB_BUILD_TEST_LOG: join(owner, 'calls.log'), AGENTHUB_BUILD_TEST_VALUES: JSON.stringify(inheritedValues) };
    const child = await subprocess('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', harness], { cwd: caller, env });
    const line = child.stdout.split(/\r?\n/).find(value => value.startsWith('BUILD_TEST_RESULT='));
    assert.ok(line, `PowerShell did not report its actual result: ${child.stdout}\n${child.stderr}`);
    const report = JSON.parse(line.slice('BUILD_TEST_RESULT='.length));
    assert.equal(child.code, 1, child.stdout + child.stderr);
    assert.equal(report.failed, true);
    assert.deepEqual(report.before, inheritedValues);
    assert.deepEqual(report.after, report.before, 'Every inherited environment value, including unset GOCACHE, must be restored');
    assert.equal(report.locationAfter, report.locationBefore);
    return report;
  }
  async function calls() { return readFile(join(owner, 'calls.log'), 'utf8').catch(error => { if (error.code === 'ENOENT') return ''; throw error; }); }
  async function unchangedStage() {
    assert.equal(await readFile(join(stage, 'old-stage-marker.txt'), 'utf8'), 'PREVIOUS STAGE');
    assert.equal(await readFile(join(stage, 'agenthub-mission-control.exe'), 'utf8'), 'STALE EXECUTABLE MUST NOT BE REUSED');
    assert.equal(await readFile(join(root, 'frontend', 'source-sentinel.txt'), 'utf8'), 'SOURCE MUST SURVIVE');
  }
  async function junction(path) {
    const outside = join(owner, `outside-${junctions.length}`);
    await mkdir(outside);
    await writeFile(join(outside, 'sentinel.txt'), 'OUTSIDE SOURCE MUST SURVIVE');
    await mkdir(dirname(path), { recursive: true });
    await symlink(outside, path, 'junction');
    junctions.push(path);
    return async () => assert.equal(await readFile(join(outside, 'sentinel.txt'), 'utf8'), 'OUTSIDE SOURCE MUST SURVIVE');
  }
  return { owner, root, stage, invoke, calls, unchangedStage, junction };
}

test('dirty committed source fails before cleaning an existing stage', windowsOnly, async t => {
  const f = await fixture(t);
  await writeFile(join(f.root, 'mission_control_entrypoint.py'), '# Uncommitted source change\n');
  const result = await f.invoke();
  assert.match(result.error, /clean committed source checkout/);
  await f.unchangedStage();
  assert.equal(await f.calls(), '');
});

for (const script of ['build-windows.ps1', 'build-mission-control.ps1']) {
  test(`${script} rejects filesystem/repository/source roots without cleanup`, windowsOnly, async t => {
    const f = await fixture(t);
    for (const output of [parse(f.root).root, f.root, dirname(f.root), join(f.root, 'frontend'), join(f.root, '.tmp')]) {
      const result = await f.invoke(script, output);
      assert.match(result.error, /OutputDirectory cannot be|Repository output must be/);
      await f.unchangedStage();
    }
    assert.equal(await f.calls(), '');
  });

  test(`${script} never reuses an old executable after a real failing freeze invocation`, windowsOnly, async t => {
    const f = await fixture(t);
    const result = await f.invoke(script);
    assert.match(result.error, /Mission Control freeze failed/);
    assert.equal(result.nativeExit, 37);
    const calls = await f.calls();
    assert.equal(calls.split(/\r?\n/).filter(value => value.startsWith('python:')).length, 2, calls);
    assert.match(calls, /python:-c .*import PyInstaller/);
    assert.match(calls, /python:-m PyInstaller .*--onefile/);
    assert.doesNotMatch(calls, /UNEXPECTED_NPM|UNEXPECTED_GO/);
    await assert.rejects(readFile(join(f.stage, 'agenthub-mission-control.exe')), { code: 'ENOENT' });
    if (script === 'build-windows.ps1') {
      await assert.rejects(readFile(join(f.stage, 'stack-manifest.json')), { code: 'ENOENT' });
      await assert.rejects(readFile(join(f.stage, 'old-stage-marker.txt')), { code: 'ENOENT' });
    }
  });
}

test('a junction inside an old stage is rejected before recursive cleanup', windowsOnly, async t => {
  const f = await fixture(t);
  const outsideUnchanged = await f.junction(join(f.stage, 'linked-output'));
  const result = await f.invoke();
  assert.match(result.error, /reparse point/);
  await f.unchangedStage();
  await outsideUnchanged();
  assert.equal(await f.calls(), '');
});

test('an output ancestor junction is rejected without touching its target', windowsOnly, async t => {
  const f = await fixture(t);
  const linked = join(f.root, '.tmp', 'linked-stage-parent');
  const outsideUnchanged = await f.junction(linked);
  for (const script of ['build-windows.ps1', 'build-mission-control.ps1']) {
    const result = await f.invoke(script, join(linked, 'stage'));
    assert.match(result.error, /reparse point/);
  }
  await f.unchangedStage();
  await outsideUnchanged();
  assert.equal(await f.calls(), '');
});

for (const asset of ['monaco/0.55.1', 'xterm/6.0.0']) {
  test(`generated public source ${asset} is checked before stage cleanup or a compiler`, windowsOnly, async t => {
    const f = await fixture(t);
    const outsideUnchanged = await f.junction(join(f.root, 'frontend', 'public', asset));
    const result = await f.invoke();
    assert.match(result.error, /reparse point/);
    await f.unchangedStage();
    await outsideUnchanged();
    assert.equal(await f.calls(), '');
  });
}

test('simulated freeze fixture plus actual npm failure restores the Next build environment', windowsOnly, async t => {
  const f = await fixture(t);
  // This committed, temporary freeze fixture exists only to reach the real
  // build-windows Next failure branch. It is never package/compiler evidence.
  await writeFile(join(f.root, 'desktop', 'local-services', 'build-mission-control.ps1'), String.raw`param([string]$OutputDirectory, [string]$PythonExecutable)
[IO.Directory]::CreateDirectory($OutputDirectory) | Out-Null
[IO.File]::WriteAllBytes((Join-Path $OutputDirectory 'agenthub-mission-control.exe'), [byte[]]@(0x4d, 0x5a))
$global:LASTEXITCODE = 0
`);
  const tools = join(f.owner, 'failing tools');
  await writeFile(join(tools, 'record-next-env.ps1'), String.raw`$values = [ordered]@{}
foreach ($name in @('API_BACKEND', 'API_BACKEND_URL', 'MISSION_CONTROL_URL', 'GO_GATEWAY_URL', 'GOCACHE', 'NEXT_DIST_DIR', 'PYTHONPATH')) {
    $values[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$values['location'] = (Get-Location).Path
[IO.File]::WriteAllText(($env:AGENTHUB_BUILD_TEST_LOG + '.npm-env.json'), ($values | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
`);
  await writeFile(join(tools, 'npm.cmd'), '@echo off\r\n>>"%AGENTHUB_BUILD_TEST_LOG%" echo npm:actual-failure-38\r\npowershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0record-next-env.ps1"\r\nexit /b 38\r\n');
  for (const args of [['add', 'desktop/local-services/build-mission-control.ps1'], ['-c', 'user.name=Owned Build Test', '-c', 'user.email=owned-build@example.invalid', 'commit', '-qm', 'Explicit simulated freeze fixture for npm failure']]) {
    const commit = await subprocess('git.exe', args, { cwd: f.root });
    assert.equal(commit.code, 0, commit.stderr);
  }
  const result = await f.invoke();
  assert.match(result.error, /Next.js production build failed/);
  assert.equal(result.nativeExit, 38);
  const npmEnvironment = JSON.parse(await readFile(join(f.owner, 'calls.log.npm-env.json'), 'utf8'));
  const canonicalRoot = await realpath(f.root);
  assert.deepEqual(npmEnvironment, {
    API_BACKEND: 'legacy', API_BACKEND_URL: 'http://127.0.0.1:28000',
    MISSION_CONTROL_URL: 'http://127.0.0.1:28000', GO_GATEWAY_URL: 'http://127.0.0.1:28001',
    GOCACHE: null, NEXT_DIST_DIR: '.next', PYTHONPATH: canonicalRoot,
    location: join(canonicalRoot, 'frontend'),
  });
  assert.equal((await f.calls()).trim(), 'npm:actual-failure-38', 'No Python or Go compiler must execute in this fixture');
  assert.deepEqual(await readFile(join(f.stage, 'agenthub-mission-control.exe')), Buffer.from([0x4d, 0x5a]));
  await assert.rejects(readFile(join(f.stage, 'old-stage-marker.txt')), { code: 'ENOENT' });
  await assert.rejects(readFile(join(f.stage, 'stack-manifest.json')), { code: 'ENOENT' });
});

test('actual Git attributes keep four tiny LF asset/schema fixtures clean on overwrite and detect each changed byte', windowsOnly, async t => {
  const f = await fixture(t);
  // Tiny text fixtures exercise checkout/copy semantics; these are not real
  // PDF/schema contents, npm installation, or evidence that a package was built.
  await copyFile(join(scripts, '..', '..', '.gitattributes'), join(f.root, '.gitattributes'));
  const upstream = join(f.root, '.tmp', 'upstream-mirror-fixtures');
  await mkdir(upstream, { recursive: true });
  const paths = ['frontend/public/pdf.min.mjs', 'frontend/public/pdf.worker.min.mjs',
    'desktop/src-tauri/gen/schemas/desktop-schema.json', 'desktop/src-tauri/gen/schemas/windows-schema.json'];
  for (const path of paths) {
    const bytes = Buffer.from(`{"tinyLfFixture":"${path}"}\n`);
    await mkdir(dirname(join(upstream, path)), { recursive: true });
    await mkdir(dirname(join(f.root, path)), { recursive: true });
    await writeFile(join(upstream, path), bytes);
    await copyFile(join(upstream, path), join(f.root, path));
  }
  for (const args of [
    ['config', 'core.autocrlf', 'true'], ['config', 'core.filemode', 'false'],
    ['add', '.gitattributes', ...paths],
    ['-c', 'user.name=Owned Build Test', '-c', 'user.email=owned-build@example.invalid', 'commit', '-qm', 'Tiny LF mirror attribute fixtures'],
  ]) {
    const git = await subprocess('git.exe', args, { cwd: f.root });
    assert.equal(git.code, 0, git.stderr);
  }
  for (const path of paths) await unlink(join(f.root, path));
  const checkout = await subprocess('git.exe', ['checkout', 'HEAD', '--', ...paths], { cwd: f.root });
  assert.equal(checkout.code, 0, checkout.stderr);
  for (const path of paths) {
    const target = join(f.root, path);
    const bytes = await readFile(target);
    assert.equal(bytes.includes(Buffer.from('\r\n')), false, 'Git checkout must retain upstream LF bytes with core.autocrlf=true');
    assert.deepEqual(bytes, await readFile(join(upstream, path)));
    await copyFile(join(upstream, path), target);
  }
  // This is intentionally the first status after replacement; a later diff or
  // status refresh must not be needed to make the clean-source guard pass.
  const clean = await subprocess('git.exe', ['status', '--porcelain', '--untracked-files=normal'], { cwd: f.root });
  assert.equal(clean.code, 0, clean.stderr);
  assert.equal(clean.stdout, '');
  for (const path of paths) {
    const changed = join(f.root, path);
    const bytes = await readFile(changed);
    bytes[0] ^= 1;
    await writeFile(changed, bytes);
    const dirty = await subprocess('git.exe', ['status', '--porcelain', '--untracked-files=normal'], { cwd: f.root });
    assert.equal(dirty.code, 0, dirty.stderr);
    assert.equal(dirty.stdout.trimEnd(), ` M ${path}`);
    await copyFile(join(upstream, path), changed);
  }
  const restored = await subprocess('git.exe', ['status', '--porcelain', '--untracked-files=normal'], { cwd: f.root });
  assert.equal(restored.code, 0, restored.stderr);
  assert.equal(restored.stdout, '');
  assert.equal(await f.calls(), '');
});
