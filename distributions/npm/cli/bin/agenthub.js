#!/usr/bin/env node
/**
 * @agenthub/cli launcher.
 *
 * Zero-runtime-dependency wrapper: resolves the platform binary package
 * (installed via optionalDependencies) and execs it, forwarding argv,
 * stdio, and the exit code. Node itself is the only host requirement
 * (guaranteed: npm installed this package).
 */

'use strict';

const { spawn } = require('node:child_process');

// platform-arch -> platform package name. Extend as new targets are
// frozen (the build is Windows x64 first; see scripts/build-cli-windows.ps1).
const SUPPORTED = {
  'win32-x64': '@agenthub/cli-win32-x64',
};

function fail(message, code) {
  process.stderr.write(`agenthub: ${message}\n`);
  process.exit(code);
}

const key = `${process.platform}-${process.arch}`;
const packageName = SUPPORTED[key];
if (!packageName) {
  const supported = Object.keys(SUPPORTED).join(', ');
  fail(`no prebuilt binary for ${key} (supported: ${supported})`, 127);
}

let binaryPath;
try {
  binaryPath = require.resolve(`${packageName}/agenthub.exe`);
} catch (error) {
  fail(
    `platform package ${packageName} did not install ` +
      `(this can happen when npm skips optional dependencies; ` +
      `retry with: npm i -g @agenthub/cli --force)`,
    127
  );
}

const child = spawn(binaryPath, process.argv.slice(2), { stdio: 'inherit' });
child.on('error', (error) => {
  fail(`failed to launch ${binaryPath}: ${error.message}`, 127);
});
child.on('exit', (code, signal) => {
  if (signal) {
    // Propagate the same signal so shells/CI see an interruption.
    process.kill(process.pid, signal);
  } else {
    process.exit(code === null ? 1 : code);
  }
});
