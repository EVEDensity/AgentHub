/**
 * AI PR Agent — responds to @claude / @cursor mentions in PR comments.
 *
 * Two modes:
 *   review  — reads PR diff, posts an AI code review as a PR review comment.
 *   fix     — reads PR diff + user description, edits files, commits & pushes.
 *
 * Designed to run inside GitHub Actions with zero external npm dependencies.
 * Relies on Node.js built-in `fetch` (Node ≥18) and the pre-installed
 * `@actions/core` + `@actions/github` packages on GHA runners.
 */

import * as core from '@actions/core';
import * as github from '@actions/github';
import { execSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';

// ── Types ────────────────────────────────────────────────────────────────

interface ClaudeMessage {
  role: 'user' | 'assistant';
  content: string;
}

interface ClaudeResponse {
  id: string;
  content: Array<{ type: 'text'; text: string }>;
  stop_reason: 'end_turn' | 'max_tokens' | 'stop_sequence';
  usage: { input_tokens: number; output_tokens: number };
}

interface ParsedComment {
  /** Which bot was mentioned: 'claude' or 'cursor' */
  bot: 'claude' | 'cursor';
  /** 'review' or 'fix' */
  mode: 'review' | 'fix';
  /** Everything after "review" or "fix" keyword — the user's natural-language request */
  task: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────

/** Make an authenticated GitHub API request. */
async function ghApi<T = unknown>(
  path: string,
  opts: RequestInit & { accept?: string } = {},
): Promise<T> {
  const token = process.env.GITHUB_TOKEN || '';
  const url = path.startsWith('http') ? path : `https://api.github.com${path}`;
  const res = await fetch(url, {
    ...opts,
    headers: {
      Authorization: `Bearer ${token}`,
      'User-Agent': 'AgentHub-AI-Code-Agent/1.0',
      Accept: opts.accept ?? 'application/vnd.github+json',
      ...opts.headers,
    },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`GitHub API ${res.status} ${path}: ${body.slice(0, 500)}`);
  }
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) return res.json() as T;
  return res.text() as unknown as T;
}

/** Call Claude API — returns the text response. */
async function claudeCall(
  system: string,
  messages: ClaudeMessage[],
  maxTokens = 4000,
  model = 'claude-sonnet-5-20251001',
): Promise<string> {
  const apiKey = process.env.ANTHROPIC_API_KEY || '';
  if (!apiKey) throw new Error('ANTHROPIC_API_KEY not set in environment');

  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({ model, system, messages, max_tokens: maxTokens }),
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Claude API ${res.status}: ${body.slice(0, 500)}`);
  }

  const data: ClaudeResponse = await res.json();
  const text = data.content.filter((c) => c.type === 'text').map((c) => c.text).join('\n');
  core.info(`Claude usage: ${data.usage.input_tokens} in / ${data.usage.output_tokens} out`);
  return text;
}

/** Parse the comment body to extract bot / mode / task. */
function parseComment(body: string): ParsedComment | null {
  const trimmed = body.trim();

  // Determine which bot
  const hasClaude = /@claude\b/i.test(trimmed);
  const hasCursor = /@cursor\b/i.test(trimmed);
  if (!hasClaude && !hasCursor) return null;
  const bot = hasCursor ? 'cursor' : 'claude'; // cursor is an alias for claude

  // Determine mode — first keyword after @mention wins
  const afterMention = trimmed.replace(/@(claude|cursor)\b/i, '').trim();

  let mode: 'review' | 'fix';
  let task: string;

  if (/\breview\b/i.test(afterMention)) {
    mode = 'review';
    task = afterMention.replace(/\breview\b/i, '').trim();
  } else if (/\bfix\b/i.test(afterMention)) {
    mode = 'fix';
    task = afterMention.replace(/\bfix\b/i, '').trim();
  } else {
    // Without an explicit keyword, default to review
    mode = 'review';
    task = afterMention;
  }

  return { bot, mode, task: task || 'General review' };
}

/** Check whether a GitHub user is a repo collaborator (write access). */
async function isCollaborator(owner: string, repo: string, username: string): Promise<boolean> {
  try {
    await ghApi(`/repos/${owner}/${repo}/collaborators/${username}`);
    return true; // 204 No Content = collaborator
  } catch {
    return false;
  }
}

/** Post a PR review with comments. */
async function postReview(
  owner: string,
  repo: string,
  prNumber: number,
  body: string,
  event: 'COMMENT' | 'APPROVE' | 'REQUEST_CHANGES' = 'COMMENT',
): Promise<void> {
  await ghApi(`/repos/${owner}/${repo}/pulls/${prNumber}/reviews`, {
    method: 'POST',
    body: JSON.stringify({ body, event }),
  });
}

/** Post a simple issue comment (reply). */
async function postComment(owner: string, repo: string, prNumber: number, body: string): Promise<void> {
  await ghApi(`/repos/${owner}/${repo}/issues/${prNumber}/comments`, {
    method: 'POST',
    body: JSON.stringify({ body }),
  });
}

/** React to the triggering comment with a 🚀 emoji to show we're working on it. */
async function addReaction(commentId: number): Promise<void> {
  const { owner, repo } = github.context.repo;
  try {
    await ghApi(`/repos/${owner}/${repo}/issues/comments/${commentId}/reactions`, {
      method: 'POST',
      body: JSON.stringify({ content: 'rocket' }),
    });
  } catch {
    // Reaction failures are cosmetic — don't block.
  }
}

// ── Mode: Review ──────────────────────────────────────────────────────────

async function handleReview(
  prDiff: string,
  task: string,
  bot: string,
  owner: string,
  repo: string,
  prNumber: number,
): Promise<void> {
  core.info(`Review mode — task: "${task}"`);

  const system = `You are a senior code reviewer for the AgentHub project.
Analyze the provided PR diff and give constructive, actionable feedback.

Output format — for each finding:
### 🔴/🟡/🟢 [Severity] — Short title
- **Problem:** what's wrong
- **Why:** why it matters (correctness, security, performance, maintainability)
- **Suggestion:** concrete fix (code snippet if applicable)
- **File:** path:line

Prioritize: correctness bugs > security > performance > style.
If the diff looks good, say so with specific praise.
Respond in Chinese.`;

  const messages: ClaudeMessage[] = [
    { role: 'user', content: `Task: ${task || 'General review of this PR'}\n\nPR Diff:\n\`\`\`diff\n${prDiff.slice(0, 50000)}\n\`\`\`` },
  ];

  const review = await claudeCall(system, messages, 4000);

  const header = `## 🤖 ${bot === 'cursor' ? 'Cursor' : 'Claude'} Code Review\n\n> **触发者：** @${github.context.actor}\n> **请求：** ${task || '通用审查'}\n\n---\n\n`;
  await postReview(owner, repo, prNumber, header + review);
  core.info('Review posted successfully');
}

// ── Mode: Fix ─────────────────────────────────────────────────────────────

async function handleFix(
  prDiff: string,
  task: string,
  bot: string,
  owner: string,
  repo: string,
  prNumber: number,
): Promise<void> {
  core.info(`Fix mode — task: "${task}"`);

  // First, get the list of changed files to understand the codebase context.
  const prData = await ghApi<Record<string, unknown>>(
    `/repos/${owner}/${repo}/pulls/${prNumber}`,
  );
  const changedFiles = (prData as { changed_files?: number }).changed_files || 0;

  const system = `You are a software engineer fixing code in the AgentHub project.
Read the PR diff and user request. Output the COMPLETE modified content for each file you change.

Use this exact format for each file:
\`\`\`
=== FILE: path/to/file.ts
<complete file content with your changes applied>
=== END
\`\`\`

Rules:
- Output the FULL file, not a diff. Every line must be present.
- Only include files you actually modified.
- Follow the existing code style (indentation, naming, patterns).
- If you can't fix something, explain why in a comment after the files.
- Do NOT include files that need no changes.`;

  const messages: ClaudeMessage[] = [
    {
      role: 'user',
      content: [
        `User request: ${task}`,
        `Changed files in PR: ${changedFiles}`,
        '',
        `PR Diff:`,
        '```diff',
        prDiff.slice(0, 40000),
        '```',
        '',
        'Apply the fix and output the modified files in the specified format.',
      ].join('\n'),
    },
  ];

  // Fix mode uses a more capable model.
  const response = await claudeCall(system, messages, 8000, 'claude-opus-4-8-20251001');

  // ── Parse file blocks from Claude's response ──
  const fileRegex = /=== FILE:\s*(.+?)\s*\n([\s\S]*?)=== END/g;
  const files: Array<{ path: string; content: string }> = [];
  let match: RegExpExecArray | null;
  while ((match = fileRegex.exec(response)) !== null) {
    const filePath = match[1].trim();
    const content = match[2].replace(/\n=== END\n?$/, '').trimEnd() + '\n';
    files.push({ path: filePath, content });
    core.info(`  Parsed file: ${filePath} (${content.length} bytes)`);
  }

  if (files.length === 0) {
    core.warning('No file blocks found in Claude response');
    await postComment(
      owner, repo, prNumber,
      `## 🤖 ${bot === 'cursor' ? 'Cursor' : 'Claude'} — 无法修复\n\nClaude 未返回可解析的文件修改。\n\n<details><summary>原始响应</summary>\n\n${response.slice(0, 1000)}\n</details>`,
    );
    return;
  }

  // ── Write files & commit ──
  for (const f of files) {
    writeFileSync(f.path, f.content, 'utf-8');
    core.info(`  Wrote ${f.path}`);
  }

  // Git operations
  const fileList = files.map((f) => f.path).join(' ');
  execSync(`git config user.name "AgentHub AI [bot]"`, { stdio: 'inherit' });
  execSync(`git config user.email "ai-bot@agenthub.dev"`, { stdio: 'inherit' });
  execSync(`git add ${fileList}`, { stdio: 'inherit' });

  const status = execSync('git diff --cached --stat', { encoding: 'utf-8' });
  core.info(`Staged changes:\n${status}`);

  const commitMsg = `[AI Agent] ${task}\n\nTriggered by @${github.context.actor} via ${bot} fix.`;
  execSync(`git commit -m "${commitMsg.replace(/"/g, '\\"')}"`, { stdio: 'inherit' });
  execSync('git push', { stdio: 'inherit' });

  core.info('Fix committed and pushed successfully');

  // ── Post summary comment ──
  const nonFileText = response
    .replace(/=== FILE:[\s\S]*?=== END/g, '')
    .trim();
  const summary = [
    `## 🤖 ${bot === 'cursor' ? 'Cursor' : 'Claude'} — 已修复`,
    '',
    `**修改文件：**`,
    ...files.map((f) => `- \`${f.path}\``),
    '',
    nonFileText ? `**备注：** ${nonFileText}` : '',
    '',
    `> 已通过 \`[AI Agent]\` commit 推送到此分支。`,
  ].join('\n');
  await postComment(owner, repo, prNumber, summary);
}

// ── Main ───────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  try {
    // 1. Parse environment & event
    const eventPayload = JSON.parse(process.env.EVENT_PAYLOAD || '{}');
    const commentBody: string = eventPayload?.comment?.body || '';
    const commentId: number = eventPayload?.comment?.id || 0;
    const commentUser: string = eventPayload?.comment?.user?.login || '';
    const prNumber: number = eventPayload?.issue?.number || 0;

    core.info(`Comment by @${commentUser} on PR #${prNumber}`);
    core.info(`Body (first 200 chars): ${commentBody.slice(0, 200)}`);

    if (!commentBody || !prNumber) {
      core.setFailed('Missing comment body or PR number');
      return;
    }

    // 2. Parse the comment
    const parsed = parseComment(commentBody);
    if (!parsed) {
      core.info('No @claude or @cursor mention found — skipping');
      return;
    }

    const { bot, mode, task } = parsed;
    const { owner, repo } = github.context.repo;
    core.info(`Bot: ${bot} | Mode: ${mode} | Task: "${task}"`);

    // 3. Permission check — only collaborators can invoke the AI agent
    const allowed = await isCollaborator(owner, repo, commentUser);
    if (!allowed) {
      await postComment(owner, repo, prNumber, `## ⚠️ 权限不足\n\n@${commentUser}，只有仓库协作者才能调用 AI Code Agent。`);
      core.warning(`@${commentUser} is not a collaborator — request denied`);
      return;
    }

    // 4. Ack — react to the triggering comment
    await addReaction(commentId);

    // 5. Fetch the PR diff
    core.info('Fetching PR diff...');
    const prDiff: string = await ghApi(`/repos/${owner}/${repo}/pulls/${prNumber}`, {
      accept: 'application/vnd.github.v3.diff',
    });
    core.info(`PR diff size: ${prDiff.length} chars`);

    if (!prDiff.trim()) {
      await postComment(owner, repo, prNumber, `## ℹ️ 无变更\n\n此 PR 没有代码变更，无需 ${mode === 'review' ? '审查' : '修复'}。`);
      return;
    }

    // 6. Dispatch
    if (mode === 'review') {
      await handleReview(prDiff, task, bot, owner, repo, prNumber);
    } else {
      await handleFix(prDiff, task, bot, owner, repo, prNumber);
    }

    core.info(`AI agent completed — ${mode} mode`);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    core.error(message);
    // Try to post a failure comment
    try {
      const { owner, repo } = github.context.repo;
      const eventPayload = JSON.parse(process.env.EVENT_PAYLOAD || '{}');
      const prNumber: number = eventPayload?.issue?.number || 0;
      if (prNumber) {
        await postComment(owner, repo, prNumber, `## ❌ AI Agent 错误\n\n\`\`\`\n${message}\n\`\`\`\n\n请检查 workflow logs。`);
      }
    } catch {
      // Can't even post the error — give up.
    }
    core.setFailed(message);
  }
}

main();
