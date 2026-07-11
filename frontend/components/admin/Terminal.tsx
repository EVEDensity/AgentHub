'use client';

import React, { useEffect, useState, useCallback, useRef, type JSX, type KeyboardEvent } from 'react';

/* ──────────────────────────────────────────────────────────────────────
   Terminal — Warm Industrial Shell Emulator
   Muted ANSI palette optimized for dark backgrounds.
   Typography: monospace stack with JetBrains Mono preference.
   ─────────────────────────────────────────────────────────────────── */

// ── Muted ANSI palette (desaturated ~18% from xterm defaults) ────────
const FG: Record<number, string> = {
  30: '#B8BEC8', 31: '#D48585', 32: '#8CB598', 33: '#C8B878',
  34: '#8AACCC', 35: '#B898C8', 36: '#5AADBE', 37: '#D8DCE2',
  90: '#6E6E6E', 91: '#D49898', 92: '#9CBE9C', 93: '#CCC088',
  94: '#9AB8D8', 95: '#C4A8D4', 96: '#6EBECE', 97: '#E8ECF0',
};

const BG: Record<number, string> = {
  40: '#161A1F', 41: '#342020', 42: '#1A3020', 43: '#302A1A',
  44: '#182230', 45: '#281C30', 46: '#142A2A', 47: '#202228',
};

interface Segment {
  t: string; fg?: string; bg?: string; b?: boolean; d?: boolean;
  i?: boolean; u?: boolean; bl?: boolean;
}

/* ── ANSI SGR parser ─────────────────────────────────────────────── */
function parse(raw: string): Segment[][] {
  return raw.split('\n').map((line) => {
    const out: Segment[] = [];
    const re = /\x1b\[([\d;]*)m/g;
    let idx = 0, cur: Segment = { t: '' }, m: RegExpExecArray | null;
    while ((m = re.exec(line)) !== null) {
      if (m.index > idx) { out.push({ ...cur, t: line.slice(idx, m.index) }); cur = { ...cur, t: '' }; }
      for (const c of (m[1] || '0').split(';').map(Number)) {
        if (c === 0) cur = { t: '' };
        else if (c === 1) cur.b = true; else if (c === 2) cur.d = true;
        else if (c === 3) cur.i = true; else if (c === 4) cur.u = true; else if (c === 5) cur.bl = true;
        else if (c >= 30 && c <= 37) cur.fg = FG[c];
        else if (c >= 40 && c <= 47) cur.bg = BG[c];
        else if (c >= 90 && c <= 97) cur.fg = FG[c];
      }
      idx = re.lastIndex;
    }
    if (idx < line.length) { cur.t = line.slice(idx); out.push({ ...cur }); }
    else if (out.length === 0 && line === '') out.push({ t: '' });
    return out.length > 0 ? out : [{ t: line }];
  });
}

function Span({ s }: { s: Segment }): JSX.Element {
  const st: React.CSSProperties = {};
  if (s.fg) st.color = s.fg;
  if (s.bg) st.backgroundColor = s.bg;
  if (s.b) st.fontWeight = 600;
  if (s.d) st.opacity = 0.55;
  if (s.i) st.fontStyle = 'italic';
  if (s.u) st.textDecoration = 'underline';
  if (s.bl) st.animation = 'term-blink 1s step-end infinite';
  return <span style={st}>{s.t}</span>;
}

// ── ANSI helpers ────────────────────────────────────────────────────
const R = (s: string) => `\x1b[31m${s}\x1b[0m`;
const G = (s: string) => `\x1b[32m${s}\x1b[0m`;
const Y = (s: string) => `\x1b[33m${s}\x1b[0m`;
const B = (s: string) => `\x1b[34m${s}\x1b[0m`;
const M = (s: string) => `\x1b[35m${s}\x1b[0m`;
const C = (s: string) => `\x1b[36m${s}\x1b[0m`;
const D = (s: string) => `\x1b[2m${s}\x1b[0m`;
const BD = (s: string) => `\x1b[1m${s}\x1b[0m`;

// ── Command implementations ─────────────────────────────────────────

function h(): string {
  return [
    '', BD(C('AgentHub Terminal')) + D(' — v2.4.1'), '',
    BD('Commands:'), '',
    `  ${C('help')}        ${D('Show this help')}`,
    `  ${C('status')}      ${D('System health & metrics')}`,
    `  ${C('agents')}      ${D('List registered agents')}`,
    `  ${C('ls')}          ${D('List workspace files')}`,
    `  ${C('cat')} <f>     ${D('Print file contents')}`,
    `  ${C('whoami')}      ${D('Current user')}`,
    `  ${C('date')}        ${D('Current date/time')}`,
    `  ${C('uptime')}      ${D('System uptime & load')}`,
    `  ${C('ps')}          ${D('Process listing')}`,
    `  ${C('top')}         ${D('Resource snapshot')}`,
    `  ${C('neofetch')}    ${D('System info banner')}`,
    `  ${C('curl')} <url>  ${D('HTTP request (dry-run)')}`,
    `  ${C('npm')} <cmd>   ${D('Package manager (dry-run)')}`,
    `  ${C('git')} <cmd>   ${D('Version control (dry-run)')}`,
    `  ${C('env')}         ${D('Environment variables')}`,
    `  ${C('echo')} <...>  ${D('Print arguments')}`,
    `  ${C('df')}          ${D('Disk usage')}`,
    `  ${C('free')}        ${D('Memory usage')}`,
    `  ${C('ping')} <h>    ${D('Network test (dry-run)')}`,
    `  ${C('clear')}       ${D('Clear screen')}`,
    '', D('Keys: ↑↓ history | Ctrl+R search | Tab complete | Ctrl+L clear'), '',
  ].join('\n');
}

function status(): string {
  const t = new Date().toISOString().replace('T', ' ').slice(0, 19);
  return [
    '', BD(C('● AgentHub Status')), '',
    `  ${BD('API')}       ${G('● OK')}    ${D('RPM 124 | p95 218ms | err 0.03%')}`,
    `  ${BD('Database')}  ${G('● OK')}    ${D('PG16 | pool 18/50 | lat 1.2ms')}`,
    `  ${BD('Redis')}     ${G('● OK')}    ${D('v7.2 | 142M/512M | hit 98.7%')}`,
    `  ${BD('MCP')}       ${G('● OK')}    ${D('8 servers | 23 conns')}`,
    `  ${BD('Agents')}    ${G('● OK')}    ${D('7 registered | 2 active')}`,
    `  ${BD('Tokens')}    ${D('12.4K / 200K today | 842K / 5M month')}`,
    `  ${BD('Uptime')}    ${D('since ' + t)}`, '',
  ].join('\n');
}

function agents(): string {
  return [
    '', BD(C('Registered Agents')), '',
    `  ${BD('ID')}            ${BD('State')}     ${BD('Model')}               ${BD('Ver')}     ${BD('Risk')}`,
    `  ${'─'.repeat(66)}`,
    `  ${C('Orchestrator')}   ${G('● active')}    claude-opus-4-8            v2.4.1   ${Y('L2')}`,
    `  ${C('Architect')}      ${G('● active')}    claude-sonnet-5            v1.8.0   ${D('L1')}`,
    `  ${C('CodeGen')}        ${G('● active')}    claude-sonnet-5            v3.2.1   ${Y('L2')}`,
    `  ${C('Review')}         ${D('◐ idle')}      claude-haiku-4-5           v2.1.0   ${D('L1')}`,
    `  ${C('Test')}           ${D('◐ idle')}      claude-haiku-4-5           v1.5.3   ${D('L1')}`,
    `  ${C('Deploy')}         ${Y('○ sleep')}     claude-sonnet-5            v2.0.2   ${R('L3')}`,
    `  ${C('Security')}       ${G('● active')}    claude-opus-4-8            v1.2.0   ${R('L3')}`,
    '', D('7 agents | 4 active | 2 idle | 1 sleeping'), '',
  ].join('\n');
}

function ls(): string {
  return [
    '', BD(C('/workspace')), '',
    `  ${B('drwxr-xr-x')}  ${D('08-12 14:22')}  ${B('📁')} ${BD('src/')}`,
    `  ${B('drwxr-xr-x')}  ${D('08-12 10:05')}  ${B('📁')} ${BD('config/')}`,
    `  ${B('drwxr-xr-x')}  ${D('08-11 18:30')}  ${B('📁')} ${BD('deploy/')}`,
    `  ${B('drwxr-xr-x')}  ${D('08-10 09:15')}  ${B('📁')} ${BD('frontend/')}`,
    `  ${B('drwxr-xr-x')}  ${D('07-28 16:42')}  ${B('📁')} ${BD('data/')}`,
    `  ${G('-rw-r--r--')}  ${D('08-12 14:20')}  ${G('📄')} index.ts         ${D('2.4K')}`,
    `  ${G('-rw-r--r--')}  ${D('08-12 11:08')}  ${G('📄')} package.json     ${D('1.8K')}`,
    `  ${G('-rw-r--r--')}  ${D('08-11 20:15')}  ${G('📄')} tsconfig.json    ${D('0.6K')}`,
    `  ${G('-rw-r--r--')}  ${D('08-10 08:55')}  ${G('📄')} Dockerfile       ${D('1.2K')}`,
    `  ${G('-rw-r--r--')}  ${D('08-09 22:10')}  ${G('📄')} README.md        ${D('8.7K')}`,
    '',
  ].join('\n');
}

function cat(args: string[]): string {
  const f = args[0] || 'README.md';
  const m: Record<string, string> = {
    'package.json': ['{', ...['name','version','description','private','scripts','dependencies'].map(k => `  "${k}": ...`), '}'].join('\n'),
    'tsconfig.json': ['{', '  "compilerOptions": {', ...['target','module','jsx','strict','baseUrl','paths'].map(k => `    "${k}": ...`), '  }', '}'].join('\n'),
    'README.md': ['# AgentHub', '', 'Multi-agent orchestration platform.', '', '## Quick Start', '', '```', 'npm install && npm run dev', '```'].join('\n'),
    'Dockerfile': ['FROM node:20-alpine AS builder', 'WORKDIR /app', 'COPY package*.json ./', 'RUN npm ci', 'COPY . .', 'RUN npm run build', '', 'FROM node:20-alpine', 'WORKDIR /app', 'COPY --from=builder /app/.next ./.next', 'EXPOSE 3000', 'CMD ["npm","start"]'].join('\n'),
  };
  if (m[f]) return `\n${D('── ' + f + ' ──')}\n${m[f]}\n${D('─'.repeat(40))}\n`;
  return `\n${R('cat: ' + f + ': No such file')}\n`;
}

function whoami(): string { return `\n${C('admin')}${D('@')}${BD('agenthub')}\n`; }
function dateCmd(): string { return `\n${G(new Date().toString())}\n`; }
function uptime(): string {
  return ['', `  ${BD('Up')}       ${G('14d 6h 32m')}`, `  ${BD('Since')}     ${D('2026-06-23 02:15:42 CST')}`, `  ${BD('User')}      ${C('admin')}  ${D('(5h 12m)')}`, `  ${BD('Load')}      ${D('0.42 / 0.38 / 0.31')}`, ''].join('\n');
}
function ps(): string {
  return ['', `  ${BD('PID')}    ${BD('%CPU')}  ${BD('%MEM')}  ${BD('TIME')}      ${BD('CMD')}`, `  ${'─'.repeat(55)}`,
    `  ${C('1242')}   ${D('2.4')}   ${D('1.8')}   ${D('04:32')}  ${G('agenthub-server')}`,
    `  ${C('1245')}   ${D('0.8')}   ${D('0.6')}   ${D('01:15')}  ${G('mcp-gateway')}`,
    `  ${C('1252')}   ${D('1.2')}   ${D('0.9')}   ${D('02:18')}  ${G('agent-codegen')}`,
    `  ${C('1256')}   ${D('0.1')}   ${D('0.2')}   ${D('00:08')}  ${G('mem-engine')}`,
    `  ${C('1271')}   ${D('0.0')}   ${D('0.1')}   ${D('00:00')}  ${Y('terminal')}`,
    '',
  ].join('\n');
}
function top(): string {
  return ['', BD(C('top')) + D(' — up 14d 6h, load: 0.42 0.38 0.31'), '',
    `  ${BD('Tasks')} 127 tot, ${G('2 run')}, ${D('118 sleep')}, ${Y('0 stop')}, ${R('0 zombie')}`,
    `  ${BD('CPU')}   ${G('12%')} us, ${D('3%')} sy, ${D('0%')} ni, ${G('82%')} idle, ${D('2%')} wa`,
    `  ${BD('Mem')}   ${G('3.2G')} tot, ${Y('1.8G')} used, ${G('1.4G')} free, ${D('142M')} buf`,
    '',
    `  ${BD('PID')}   ${BD('USER')}     ${BD('%CPU')} ${BD('%MEM')} ${BD('CMD')}`,
    `  ${C('1242')}  agenthub   ${G('8.4')} ${D('1.8')} node agenthub-server`,
    `  ${C('1252')}  agenthub   ${G('2.1')} ${D('0.9')} node agent-codegen`,
    `  ${C('1245')}  agenthub   ${D('1.2')} ${D('0.6')} node mcp-gateway`,
    '',
  ].join('\n');
}
function neofetch(): string {
  return ['',
    `      ${C('▄▄▄▄▄▄▄▄▄▄▄')}    ${BD(C('admin'))}${D('@')}${BD(C('AgentHub'))}`,
    `     ${C('▐░░░░░░░░░░░▌')}   ${D('────────────────')}`,
    `    ${C('▐░▌')}${D('▀▀▀▀▀▀▀▀▀')}${C('▐░▌')}  ${BD('OS')}      AgentHub v2.4.1`,
    `   ${C('▐░▌')}${D('▀▀▀')}${C('▄▄▄')}${D('▀▀▀')}${C('▐░▌')}  ${BD('Host')}    Docker (alpine)`,
    `    ${C('▐░▌')}${D('▀▀▀▀▀▀▀')}${C('▐░▌')}  ${BD('Kernel')}  Linux 6.1 x86_64`,
    `     ${C('▐░▌')}${D('▀▀▀▀▀')}${C('▐░▌')}  ${BD('Shell')}   AgentHub Terminal`,
    `      ${C('▐░▌')}${D('▀▀▀')}${C('▐░▌')}  ${BD('CPU')}     AMD EPYC 4vCPU`,
    `       ${C('▐░▌')}${D('▀')}${C('▐░▌')}  ${BD('Mem')}     1.8G / 3.2G`,
    `        ${C('▐░▌▐░▌')}  ${BD('Theme')}   Warm Industrial`,
    `         ${C('▐░▌')}`, '',
    `   ${D('●')}${R('●')}${Y('●')}${G('●')}${C('●')}${B('●')}${M('●')}   ${D('dim red yellow green cyan blue magenta')}`,
    '',
  ].join('\n');
}
function curl(args: string[]): string {
  const u = args[0] || 'http://localhost:3000/api/health';
  return ['', D('> GET ' + u), '', `HTTP/1.1 ${G('200 OK')}`, 'Content-Type: application/json',
    `X-Request-Id: ${D('req_' + Math.random().toString(36).slice(2, 10))}`,
    `X-Response-Time: ${D((Math.random() * 80 + 15).toFixed(1) + 'ms')}`, '',
    '{', `  "status": ${G('"ok"')},`, `  "version": "2.4.1",`, `  "uptime": ${D('"14d 6h"')},`, `  "agents": 4`, '}', '',
  ].join('\n');
}
function npm(args: string[]): string {
  const s = args[0] || 'ls';
  if (s === 'ls' || s === 'list') return ['', D('agenthub@2.4.1'), ...[...'next react react-dom zustand tailwindcss typescript'.split(' ')].map(p => `├── ${G(p)}`).join('\n'), D('142 packages | 0 vulns'), ''].join('\n');
  if (s === 'i' || s === 'install') return ['', D('npm install'), '', `${G('✓')} ${D('added 142 packages in 12.4s')}`, '', D('0 vulnerabilities'), ''].join('\n');
  if (s === 'run') return ['', D('npm run ' + (args[1] || 'dev')), '', `${G('✓')} ${D('Ready — http://localhost:3000')}`, ''].join('\n');
  return `\n${Y('npm ' + s + ': unknown')}\n`;
}
function git(args: string[]): string {
  const s = args[0] || 'status';
  if (s === 'status') return ['', BD('On branch ') + C('main'), BD('Up to date with ') + D('origin/main'), '',
    D('Modified:'), `  ${G('M')} frontend/components/admin/CommandPalette.tsx`, `  ${G('M')} frontend/styles/globals.css`, '',
    D('Untracked:'), `  ${R('?')} frontend/components/admin/Terminal.tsx`, '', D('no changes added'), '',
  ].join('\n');
  if (s === 'log') return ['', `${Y('commit')} ${C('a284a84')} ${D('(HEAD)')}`, D('优化UI'), '',
    `${Y('commit')} ${C('cff1c75')}`, D('readme 启动修改'), '',
  ].join('\n');
  return `\n${Y("git: '" + s + "' unknown")}\n`;
}
function env(): string {
  return ['', `  ${C('NODE_ENV')}        = ${G('production')}`, `  ${C('PORT')}            = ${D('3000')}`,
    `  ${C('DATABASE_URL')}    = ${D('postgresql://db:5432/agenthub')}`,
    `  ${C('ANTHROPIC_KEY')}   = ${R('sk-****')}${D('...8a2f')}`,
    `  ${C('LOG_LEVEL')}       = ${D('info')}`, '',
  ].join('\n');
}
function echo(args: string[]): string { return `\n${args.join(' ') || 'Hello, AgentHub!'}\n`; }
function df(): string {
  return ['', `  ${BD('FS')}          ${BD('Size')}   ${BD('Used')}   ${BD('Avail')}  ${BD('Use')}  ${BD('Mount')}`, `  ${'─'.repeat(52)}`,
    `  /dev/sda1     ${D('50G')}   ${Y('18G')}   ${G('32G')}   ${Y('36%')}  ${D('/')}`,
    `  /dev/sdb1     ${D('200G')}  ${D('84G')}   ${G('116G')}  ${D('42%')}  ${D('/data')}`, '',
  ].join('\n');
}
function free(): string {
  return ['', `            ${BD('total')}     ${BD('used')}     ${BD('free')}    ${BD('shared')}   ${BD('buf/cache')}  ${BD('avail')}`,
    `  ${BD('Mem')}      ${D('3.2G')}     ${Y('1.8G')}     ${G('1.4G')}     ${D('142M')}     ${D('512M')}      ${G('2.1G')}`,
    `  ${BD('Swap')}     ${D('2.0G')}     ${D('0B')}       ${G('2.0G')}`, '',
  ].join('\n');
}
function ping(args: string[]): string {
  const h = args[0] || 'localhost';
  return ['', BD('PING ' + h) + D(' (127.0.0.1): 56 bytes'),
    ...[...Array(4)].map((_, i) => `64 bytes from 127.0.0.1: icmp_seq=${i} ttl=64 time=${G((Math.random() * 0.1 + 0.04).toFixed(2) + ' ms')}`).join('\n'),
    '', `--- ${h} ping stats ---`, `4 sent, ${G('4 recv')}, ${D('0% loss')}, time 3004ms`,
    `rtt min/avg/max = ${G('0.04/0.08/0.12 ms')}`, '',
  ].join('\n');
}

const CMDS: Record<string, (a: string[]) => string> = {
  help: h, status, agents, ls, cat, whoami, date: dateCmd, uptime, ps, top, neofetch, curl, npm, git, env, echo, df, free, ping,
};
const CMD_NAMES = Object.keys(CMDS);

// ── Component ────────────────────────────────────────────────────────

interface Line { id: number; type: 'in' | 'out'; text: string; }

export default function Terminal(): JSX.Element {
  const [lines, setLines] = useState<Line[]>(() => [{
    id: 0, type: 'out', text: [
      '', BD(C('╔══════════════════════════════════╗')),
      BD(C('║')) + BD('  AgentHub Terminal v2.4.1    ') + BD(C('║')),
      BD(C('║')) + D('  Warm Industrial Shell       ') + BD(C('║')),
      BD(C('╚══════════════════════════════════╝')),
      '', D('Type ') + C('help') + D(' to begin · ↑↓ history · Ctrl+R search · Tab complete'), '',
    ].join('\n'),
  }]);
  const [input, setInput] = useState('');
  const [history, setHistory] = useState<string[]>([]);
  const [hi, setHi] = useState(-1);
  const [searchMode, setSearchMode] = useState(false);
  const [sq, setSq] = useState('');
  const [sr, setSr] = useState(-1);
  const [sug, setSug] = useState('');

  const inpRef = useRef<HTMLInputElement>(null);
  const outRef = useRef<HTMLDivElement>(null);
  const idRef = useRef(1);

  useEffect(() => { if (outRef.current) outRef.current.scrollTop = outRef.current.scrollHeight; }, [lines]);

  useEffect(() => {
    if (!input.trim()) { setSug(''); return; }
    const cs = CMD_NAMES.filter(c => c.startsWith(input.trim().split(/\s+/)[0].toLowerCase()));
    setSug(cs.length === 1 && cs[0] !== input.trim().split(/\s+/)[0] ? cs[0] : '');
  }, [input]);

  const exec = useCallback((raw: string) => {
    const t = raw.trim(); if (!t) return;
    setLines(p => [...p, { id: idRef.current++, type: 'in', text: t }]);
    setHistory(p => { const n = [...p, t]; return n.length > 200 ? n.slice(-200) : n; });
    setHi(-1);
    if (t === 'clear') { setLines([]); return; }
    const parts = t.split(/\s+/), cmd = parts[0].toLowerCase(), args = parts.slice(1);
    const fn = CMDS[cmd];
    setLines(p => [...p, { id: idRef.current++, type: 'out', text: fn ? fn(args) : `\n${R('zsh: command not found: ' + cmd)}\n${D('Type help for available commands')}\n` }]);
  }, []);

  const handleKeyDown = useCallback((e: KeyboardEvent<HTMLInputElement>) => {
    if (e.ctrlKey && e.key === 'r') { e.preventDefault(); setSearchMode(true); setSq(''); setSr(-1); return; }
    if (e.ctrlKey && e.key === 'l') { e.preventDefault(); setLines([]); return; }
    if (e.key === 'Tab') { e.preventDefault(); if (sug) { setInput(sug + ' '); setSug(''); } return; }
  }, [sug]);

  const searchKeyDown = useCallback((e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') { e.preventDefault(); setSearchMode(false); setSq(''); setSr(-1); setTimeout(() => inpRef.current?.focus(), 0); return; }
    if (e.key === 'Enter') { e.preventDefault(); if (sr >= 0) { const m = [...history].reverse().filter(h => h.toLowerCase().includes(sq.toLowerCase()))[sr]; if (m) { setInput(m); setSearchMode(false); setSq(''); setSr(-1); setTimeout(() => inpRef.current?.focus(), 0); } } return; }
    if (e.key === 'ArrowUp' || (e.ctrlKey && e.key === 'p')) { e.preventDefault(); const ms = [...history].reverse().filter(h => h.toLowerCase().includes(sq.toLowerCase())); setSr(p => Math.min(p + 1, ms.length - 1)); return; }
    if (e.key === 'ArrowDown' || (e.ctrlKey && e.key === 'n')) { e.preventDefault(); setSr(p => Math.max(p - 1, -1)); return; }
  }, [history, sq, sr]);

  const sm = searchMode ? [...history].reverse().filter(h => h.toLowerCase().includes(sq.toLowerCase())) : [];

  const inputKeyDown = useCallback((e: KeyboardEvent<HTMLInputElement>) => {
    handleKeyDown(e); if (e.defaultPrevented) return;
    if (e.key === 'Enter') { e.preventDefault(); exec(input); setInput(''); setSug(''); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); if (history.length > 0) { const ni = hi === -1 ? history.length - 1 : Math.max(hi - 1, 0); setHi(ni); setInput(history[ni]); } }
    else if (e.key === 'ArrowDown') { e.preventDefault(); if (hi >= 0) { const ni = hi + 1; if (ni >= history.length) { setHi(-1); setInput(''); } else { setHi(ni); setInput(history[ni]); } } }
  }, [handleKeyDown, exec, input, history, hi]);

  return (
    <div className="term" onClick={() => inpRef.current?.focus()}>
      {/* Title bar */}
      <div className="term-bar">
        <span className="term-dot term-dot-red" />
        <span className="term-dot term-dot-amber" />
        <span className="term-dot term-dot-green" />
        <span className="term-bar-title">Terminal · admin@agenthub</span>
      </div>

      {/* Output */}
      <div className="term-body" ref={outRef}>
        {lines.map(l => (
          <div key={l.id}>
            {l.type === 'in' ? (
              <div className="term-line-in">
                <span className="term-prompt">admin@agenthub:~$</span>
                <span className="term-cmd-text"> {l.text}</span>
              </div>
            ) : (
              <div className="term-line-out">
                {parse(l.text).map((segs, i) => (
                  <div key={i} style={{ lineHeight: 1.6, minHeight: (!segs.length || (segs.length === 1 && segs[0].t === '')) ? '1.6em' : undefined }}>
                    {(!segs.length || (segs.length === 1 && segs[0].t === '')) ? ' ' : segs.map((s, j) => <Span key={j} s={s} />)}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}

        {/* Input row */}
        <div className="term-line-in term-line-active">
          <span className="term-prompt">admin@agenthub:~$</span>
          <span className="term-input-wrap">
            <input ref={inpRef} className="term-input" type="text" value={input}
              onChange={e => { setInput(e.target.value); setSug(''); }}
              onKeyDown={inputKeyDown} autoFocus spellCheck={false} autoComplete="off" aria-label="Terminal input" />
            {sug && <span className="term-ghost" aria-hidden="true">{input}{sug.slice(input.length)}</span>}
          </span>
          <span className="term-cursor" />
        </div>
      </div>

      {/* Search overlay */}
      {searchMode && (
        <div className="term-search">
          <div className="term-search-bar">
            <span className="term-search-icon">⌕</span>
            <span className="term-search-label">(reverse-i-search)`</span>
            <input className="term-search-inp" type="text" value={sq}
              onChange={e => { setSq(e.target.value); setSr(0); }}
              onKeyDown={searchKeyDown} autoFocus spellCheck={false} />
            <span className="term-search-label">': </span>
            {sr >= 0 && sm[sr] && <span className="term-search-hit">{sm[sr]}</span>}
            {sm.length === 0 && sq && <span className="term-search-miss">(no match)</span>}
          </div>
        </div>
      )}
    </div>
  );
}
