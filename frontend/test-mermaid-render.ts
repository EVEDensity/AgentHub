/**
 * End-to-end render test: feed sanitized Mermaid code into a real Mermaid
 * 11.4.1 parser (via JSDOM) and verify it renders without errors.
 */
import * as fs from 'fs';
import * as path from 'path';
import { JSDOM } from 'jsdom';
import { sanitizeMermaidCode } from './utils/mermaidSanitizer';

// Set up DOM globals BEFORE importing mermaid.  Node 22+ makes some of these
// read-only, so we use defineProperty with `configurable: true`.
const dom = new JSDOM('<!DOCTYPE html><html><body><div id="d"></div></body></html>', {
  pretendToBeVisual: true,
});
function setGlobal(name: string, value: any) {
  try {
    (globalThis as any)[name] = value;
  } catch {
    Object.defineProperty(globalThis, name, { value, writable: true, configurable: true });
  }
}
setGlobal('window', dom.window);
setGlobal('document', dom.window.document);
setGlobal('navigator', dom.window.navigator);
setGlobal('HTMLElement', dom.window.HTMLElement);
setGlobal('HTMLAnchorElement', dom.window.HTMLAnchorElement);
setGlobal('SVGElement', dom.window.SVGElement);
setGlobal('Element', dom.window.Element);
setGlobal('Node', dom.window.Node);
setGlobal('DocumentFragment', dom.window.DocumentFragment);
setGlobal('getComputedStyle', dom.window.getComputedStyle.bind(dom.window));
setGlobal('requestAnimationFrame', (cb: any) => setTimeout(cb, 0));
setGlobal('cancelAnimationFrame', (id: any) => clearTimeout(id));
// jsdom doesn't implement layout, so we stub getBoundingClientRect.
dom.window.SVGElement.prototype.getBoundingClientRect = function () {
  return { x: 0, y: 0, width: 0, height: 0, top: 0, right: 0, bottom: 0, left: 0 } as any;
};
dom.window.HTMLElement.prototype.getBoundingClientRect = function () {
  return { x: 0, y: 0, width: 0, height: 0, top: 0, right: 0, bottom: 0, left: 0 } as any;
};

import mermaid from 'mermaid';

mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' });

const userFailingCode = `flowchart TD
    A[App] --> B[UserManagement Page]

    subgraph B [UserManagement Page]
        B1[顶部：新建用户按钮]
        B2[Ant Design Table<br/>显示用户列表]
        B3[搜索输入框]
        B4[分页组件]
    end

    B -- 点击 "新建" --> C[UserForm Modal]
    B2 -- 点击 "编辑" --> C[UserForm Modal]
    B2 -- 点击 "删除" --> D[确认对话框]

    C -- 提交表单 --> E{API Call}
    D -- 确认删除 --> E

    E -->|成功| F[刷新用户列表]
    E -->|失败| G[显示错误提示]`;

const cases = [
  { name: 'Raw user input (expected to fail)', code: userFailingCode },
  { name: 'Sanitized', code: sanitizeMermaidCode(userFailingCode) },
  { name: 'Subgraph with Chinese', code: 'flowchart TD\n    subgraph 用户模块\n        A[你好] --> B[世界]\n    end' },
  { name: 'Subgraph with Chinese sanitized', code: sanitizeMermaidCode('flowchart TD\n    subgraph 用户模块\n        A[你好] --> B[世界]\n    end') },
  { name: 'Arrow with quotes raw', code: 'flowchart LR\n    A -- 他说 "hi" --> B' },
  { name: 'Arrow with quotes sanitized', code: sanitizeMermaidCode('flowchart LR\n    A -- 他说 "hi" --> B') },
  { name: 'Arrow with quotes + bare target', code: 'flowchart LR\n    A -- "click" --> B' },
  { name: 'Arrow with quotes + bare target sanitized', code: sanitizeMermaidCode('flowchart LR\n    A -- "click" --> B') },
  { name: 'Natural language after diagram raw', code: 'flowchart TD\n    A --> B\n\n数据流说明：这是文字' },
  { name: 'Natural language after diagram sanitized', code: sanitizeMermaidCode('flowchart TD\n    A --> B\n\n数据流说明：这是文字') },
];

async function main() {
  const lines: string[] = [];
  lines.push('=== Mermaid 11.4.1 End-to-End Render Test ===\n');
  for (const c of cases) {
    try {
      const result = await mermaid.parse(c.code);
      lines.push(`✅ ${c.name}`);
      lines.push('   parsed cleanly');
    } catch (e: any) {
      const msg = (e?.message || String(e)).split('\n').slice(0, 2).join(' | ');
      lines.push(`❌ ${c.name}`);
      lines.push(`   ${msg}`);
    }
  }
  const out = lines.join('\n');
  console.log(out);
  fs.writeFileSync(path.join(__dirname, 'test-mermaid-render.txt'), out, 'utf8');
}

main().catch(e => {
  console.error('Fatal:', e);
  process.exit(1);
});
