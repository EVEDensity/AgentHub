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
async function copyDependencyNotices(name) {
  if (visited.has(name)) return;
  visited.add(name);
  const directory = path.join(root, 'node_modules', ...name.split('/'));
  const manifest = JSON.parse(await readFile(path.join(directory, 'package.json'), 'utf8'));
  if (manifest.name !== name) throw new Error('Component notice package identity differs');
  let license;
  for (const filename of ['LICENSE', 'LICENSE.md', 'LICENSE.txt', 'license', 'license.md']) {
    try { if ((await stat(path.join(directory, filename))).isFile()) { license = filename; break; } }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  const licensePath = license ? path.join(directory, license)
    : name === 'react-remove-scroll-bar' && manifest.version === '2.3.8'
      ? path.join(root, 'licenses', 'react-remove-scroll-bar.LICENSE.txt') : null;
  if (!licensePath) throw new Error(`Component license is missing: ${name}`);
  await cp(licensePath, path.join(notices, `${name.replace(/[^a-z0-9.-]/gi, '_')}@${manifest.version}.LICENSE.txt`));
  for (const dependency of Object.keys(manifest.dependencies ?? {})) await copyDependencyNotices(dependency);
}
await copyDependencyNotices('@radix-ui/react-dialog');
await copyDependencyNotices('@fontsource/material-symbols-outlined');
await cp(path.join(root, 'licenses', 'fontsource-material-symbols-outlined.SOURCE.txt'), path.join(notices, 'fontsource-material-symbols-outlined.SOURCE.txt'));
console.log('Prepared reviewed Monaco 0.55.1 editor, workers and notices at public/monaco/0.55.1');
