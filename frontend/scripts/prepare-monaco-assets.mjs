import { cp, mkdir, readFile, readdir, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'node_modules', 'monaco-editor');
const destination = path.join(root, 'public', 'monaco', '0.55.1');
const metadata = JSON.parse(await readFile(path.join(source, 'package.json'), 'utf8'));
if (metadata.version !== '0.55.1') throw new Error('Monaco assets require the reviewed exact version 0.55.1');

for (const relative of ['min/vs/loader.js', 'min/vs/editor/editor.main.js', 'min/vs/editor/editor.main.css', 'LICENSE', 'ThirdPartyNotices.txt']) {
  if (!(await stat(path.join(source, relative))).isFile()) throw new Error(`Required Monaco asset is missing: ${relative}`);
}
const workers = await readdir(path.join(source, 'min', 'vs', 'assets'));
for (const label of ['editor', 'json', 'css', 'html', 'ts']) {
  if (!workers.some((name) => name.startsWith(`${label}.worker-`) && name.endsWith('.js'))) throw new Error(`Monaco ${label} worker is missing`);
}
await mkdir(destination, { recursive: true });
await cp(path.join(source, 'min', 'vs'), path.join(destination, 'vs'), { recursive: true, force: true });
await cp(path.join(source, 'LICENSE'), path.join(destination, 'LICENSE'));
await cp(path.join(source, 'ThirdPartyNotices.txt'), path.join(destination, 'ThirdPartyNotices.txt'));
// Include notices in standalone/Desktop public assets, not just the source tree.
const notices = path.join(destination, 'notices');
await mkdir(notices, { recursive: true });
await cp(path.join(root, 'licenses', 'shadcn-ui.LICENSE.txt'), path.join(notices, 'shadcn-ui.LICENSE.txt'));
const visited = new Set();
async function dependencyDirectory(name, from) {
  let parent = from;
  for (;;) {
    const directory = path.join(parent, 'node_modules', ...name.split('/'));
    try { if ((await stat(path.join(directory, 'package.json'))).isFile()) return directory; }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (parent === root) throw new Error(`Required component dependency is missing: ${name}`);
    parent = path.dirname(parent);
    if (path.relative(root, parent).startsWith('..')) throw new Error('Component dependency escapes the frontend tree');
  }
}
async function copyDependencyNotices(name, from = root) {
  const directory = await dependencyDirectory(name, from);
  if (visited.has(directory)) return;
  visited.add(directory);
  const manifest = JSON.parse(await readFile(path.join(directory, 'package.json'), 'utf8'));
  if (manifest.name !== name) throw new Error('Component notice package identity differs');
  if (name === '@assistant-ui/react' && manifest.version !== '0.15.29') throw new Error('Assistant UI notices require reviewed version 0.15.29');
  let license;
  for (const filename of ['LICENSE', 'LICENSE.md', 'LICENSE.txt', 'license', 'license.md']) {
    try { if ((await stat(path.join(directory, filename))).isFile()) { license = filename; break; } }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  const licensePath = license ? path.join(directory, license)
    : name === 'react-remove-scroll-bar' && manifest.version === '2.3.8'
      ? path.join(root, 'licenses', 'react-remove-scroll-bar.LICENSE.txt') : null;
  const filename = `${name.replace(/[^a-z0-9.-]/gi, '_')}@${manifest.version}`;
  if (licensePath) {
    await cp(licensePath, path.join(notices, `${filename}.LICENSE.txt`));
  } else if (name === 'use-composed-ref' && manifest.version === '1.4.0' && manifest.license === 'MIT') {
    // This archive omits the upstream text: preserve the declaration, do not invent attribution.
    await cp(path.join(root, 'licenses', 'use-composed-ref.DECLARED-MIT-NOTICE.txt'), path.join(notices, `${filename}.DECLARED-MIT-NOTICE.txt`));
    await cp(path.join(directory, 'package.json'), path.join(notices, `${filename}.package.json`));
  } else throw new Error(`Component license is missing: ${name}`);
  for (const dependency of Object.keys(manifest.dependencies ?? {})) await copyDependencyNotices(dependency, directory);
}
await copyDependencyNotices('@radix-ui/react-dialog');
await copyDependencyNotices('@fontsource/material-symbols-outlined');
await copyDependencyNotices('@assistant-ui/react');
await copyDependencyNotices('@xterm/xterm');
const xterm = path.join(root, 'node_modules', '@xterm', 'xterm');
const xtermMetadata = JSON.parse(await readFile(path.join(xterm, 'package.json'), 'utf8'));
if (xtermMetadata.version !== '6.0.0' || xtermMetadata.license !== 'MIT') throw new Error('Log assets require reviewed xterm 6.0.0');
const xtermDestination = path.join(root, 'public', 'xterm', '6.0.0');
await mkdir(xtermDestination, { recursive: true });
for (const [input, output] of [['lib/xterm.js', 'xterm.js'], ['css/xterm.css', 'xterm.css'], ['LICENSE', 'LICENSE.txt']]) {
  if (!(await stat(path.join(xterm, input))).isFile()) throw new Error(`Required log asset is missing: ${input}`);
  await cp(path.join(xterm, input), path.join(xtermDestination, output));
}
await cp(path.join(root, 'licenses', 'xterm.SOURCE.txt'), path.join(xtermDestination, 'SOURCE.txt'));
await cp(path.join(root, 'licenses', 'fontsource-material-symbols-outlined.SOURCE.txt'), path.join(notices, 'fontsource-material-symbols-outlined.SOURCE.txt'));
console.log('Prepared reviewed Monaco 0.55.1 and xterm 6.0.0 assets and notices');
