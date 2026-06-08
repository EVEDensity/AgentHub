/**
 * Test script for Mermaid sanitizer with the user's actual failing code.
 * Run with: npx tsx test-mermaid-fix.ts
 * Output written to test-mermaid-output.txt to avoid console encoding issues.
 */

import * as fs from 'fs';
import * as path from 'path';
import { sanitizeMermaidCode } from './utils/mermaidSanitizer';

function hexdump(s: string): string {
  return Array.from(s).map(c => {
    const code = c.charCodeAt(0);
    if (code < 32 || code > 126) {
      return `[U+${code.toString(16).toUpperCase().padStart(4, '0')}]`;
    }
    return c;
  }).join('');
}

// Test 1: Single node line
const test1 = `B1[顶部：新建用户按钮]`;
const r1 = sanitizeMermaidCode(test1);

// Test 2: Single arrow line with quoted label
const test2 = `B -- 点击 "新建" --> C[UserForm Modal]`;
const r2 = sanitizeMermaidCode(test2);

// Test 3: Node with <br/>
const test3 = `B2[Ant Design Table<br/>显示用户列表]`;
const r3 = sanitizeMermaidCode(test3);

// Test 4: Full diagram
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

const r4 = sanitizeMermaidCode(userFailingCode);

// Test 5: Full diagram with trailing natural language
const userFailingCode2 = userFailingCode + `

数据流 ：
页面加载时， UserManagement 组件调用 GET /api/users 获取列表并渲染 Table。
用户点击「新建」或「编辑」，打开 UserForm Modal。`;

const r5 = sanitizeMermaidCode(userFailingCode2);

const output = [
  '=== TEST 1: single Chinese node ===',
  `IN:  ${hexdump(test1)}`,
  `OUT: ${hexdump(r1)}`,
  `OUT (raw): ${r1}`,
  '',
  '=== TEST 2: arrow with quoted label ===',
  `IN:  ${test2}`,
  `OUT: ${r2}`,
  '',
  '=== TEST 3: node with <br/> ===',
  `IN:  ${test3}`,
  `OUT: ${r3}`,
  '',
  '=== TEST 4: full diagram (no trailing natural language) ===',
  `OUT:`,
  r4,
  '',
  '=== TEST 5: full diagram with trailing natural language ===',
  `OUT:`,
  r5,
  '',
  '=== VALIDATION ===',
  `Test1: B1[顶部] → B1["顶部"] (Chinese preserved): ${r1.includes('顶部') ? 'PASS' : 'FAIL'}`,
  `Test1: B1 + brackets preserved: ${r1.includes('B1[') ? 'PASS' : 'FAIL'}`,
  `Test2: arrow with quoted label converted: ${r2.includes('&quot;') ? 'PASS' : 'FAIL'}`,
  `Test2: no bare quotes in label: ${!/--[^|]*"[^"]+"/.test(r2) ? 'PASS' : 'FAIL'}`,
  `Test3: <br/> preserved: ${r3.includes('<br/>') ? 'PASS' : 'FAIL'}`,
  `Test3: Chinese preserved: ${r3.includes('显示') ? 'PASS' : 'FAIL'}`,
  `Test4: subgraph B [..] fixed: ${!r4.includes('subgraph B [UserManagement') ? 'PASS' : 'FAIL'}`,
  `Test4: all <br/> preserved: ${(r4.match(/<br\/>/g) || []).length === 1 ? 'PASS' : `FAIL (got ${(r4.match(/<br\/>/g) || []).length})`}`,
  `Test4: all Chinese node labels preserved: ${['顶部：新建用户按钮', '搜索输入框', '分页组件', '显示用户列表', '确认对话框'].every(s => r4.includes(s)) ? 'PASS' : 'FAIL'}`,
  // Lines that still have unescaped double quotes inside `--` arrow labels
  `Test4: arrows with bare double quotes:`,
  ...r4.split('\n').filter(l => /--[^|]*"[^"]+"/.test(l) && /-->/.test(l)).map(l => `  ❌ ${l}`),
  // Lines that are fine
  `Test4: arrows with proper |"..."| form:`,
  ...r4.split('\n').filter(l => /-->\|"/.test(l)).map(l => `  ✅ ${l}`),
  `Test5: natural language after diagram is commented: ${r5.split('\n').some(l => l.trim().startsWith('%%') && l.includes('数据流')) ? 'PASS' : 'FAIL'}`,
].join('\n');

const outPath = path.join(__dirname, 'test-mermaid-output.txt');
fs.writeFileSync(outPath, output, 'utf8');
console.log('Wrote output to:', outPath);
