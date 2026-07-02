/**
 * Mermaid code sanitizer — fixes common LLM-generated syntax errors
 * before passing code to the Mermaid renderer.
 *
 * LLMs frequently produce subtly broken Mermaid syntax.  These are the
 * most common failure patterns observed with Mermaid 10.x / 11.x:
 *
 *   1. Zero-width / invisible Unicode characters (U+200B, U+FEFF, etc.)
 *   2. Node IDs with spaces, Chinese, or special characters
 *   3. Deprecated `graph` keyword (should be `flowchart` in ≥10.x)
 *   4. Labels containing unescaped quotes or HTML fragments
 *   5. subgraph names with spaces / Chinese — must be quoted
 *   6. Arrow syntax errors: missing spaces around `|`, double colons
 *   7. Empty lines inside subgraphs that confuse the parser
 *   8. Markdown artifacts leaking into Mermaid code blocks
 */

// ── Constants ──────────────────────────────────────────────────────────

/** Characters that are safe in an unquoted Mermaid node ID (letter / digit / underscore). */
const SAFE_ID_RE = /^[A-Za-z0-9_]+$/;

/**
 * Invisible / zero-width Unicode characters that LLMs sometimes inject.
 * Covered: U+200B-200F, U+FEFF, U+00A0, U+2060-2064, U+2028-202E, U+180E
 * Uses \\uXXXX escapes — literal invisible chars in source break TypeScript.
 *
 * IMPORTANT: JavaScript's \\uXXXX escape is exactly 4 hex digits.  Writing
 * 5+ digits like \\uE01EF is a SYNTAX BUG: the parser reads \\uE01E (a
 * single char) and treats the next char `F` as part of the character
 * class, expanding the range to almost every printable character.  Never
 * use 5+ hex digits in \\u escapes — use \\u{XXXXX} (with the `u` flag)
 * or simply drop the range.
 */
const INVISIBLE_CHARS_RE =
  /[\u200B\u200C\u200D\u200E\u200F\uFEFF\u00A0\u2060\u2061\u2062\u2063\u2064\u2028\u2029\u202A\u202B\u202C\u202D\u202E\u202F\u180E\u034F\u061C\u115F\u1160\u17B4\u17B5\u180B\u180C\u180D\uFE00\uFE01\uFE02\uFE03\uFE04\uFE05\uFE06\uFE07\uFE08\uFE09\uFE0A\uFE0B\uFE0C\uFE0D\uFE0E\uFE0F]/gu;

/** Valid Mermaid diagram-type starters (first line first word). */
const VALID_STARTERS = new Set([
  'flowchart', 'graph', 'sequencediagram', 'classdiagram',
  'statediagram', 'erdiagram', 'journey', 'gantt', 'pie',
  'gitgraph', 'mindmap', 'timeline', 'sankey', 'block',
  'quadrantchart', 'xychart', 'requirementdiagram',
  'c4context', 'c4container', 'c4component',
  'architecture', 'kanban', 'packet', 'radar',
]);

// ── Public API ─────────────────────────────────────────────────────────

/**
 * Sanitize a raw Mermaid code string from an LLM.
 * Returns the sanitized code, or the original if nothing needed fixing.
 */
export function sanitizeMermaidCode(raw: string): string {
  if (!raw || !raw.trim()) return raw;

  let code = raw;

  // ── 0. Strip invisible / zero-width characters ─────────────────────
  // LLMs sometimes inject U+200B (zero-width space), U+FEFF (BOM), etc.
  // These are invisible to humans but break Mermaid's parser.
  code = code.replace(INVISIBLE_CHARS_RE, '');

  // ── 1. Replace deprecated `graph` with `flowchart` ──────────────────
  // Mermaid 10.x prefers `flowchart`; `graph` still works in some versions
  // but can cause issues with certain renderers and syntax combinations.
  code = code.replace(
    /^(graph\s+(TB|TD|BT|RL|LR))\b/gm,
    (_match: string, _full: string, direction: string) =>
      `flowchart ${direction}`
  );

  // ── 2. Normalize line endings ──────────────────────────────────────
  code = code.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

  // ── 3. Strip markdown artifact lines inside code blocks ───────────
  // Sometimes markdown processors leave artifacts like ``` inside mermaid
  code = code.replace(/^```[\s\w]*$/gm, '');

  // ── 4. Process line by line ────────────────────────────────────────
  const lines = code.split('\n');
  const fixed: string[] = [];
  let subgraphDepth = 0;
  // Debug trace buffer
  const dbg: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];
    const origLine = line;
    dbg.push(`#${i} IN: ${JSON.stringify(line)}`);

    // Skip fully empty lines (Mermaid tolerates these)
    if (!line.trim()) {
      dbg.push(`#${i} SKIP_EMPTY`);
      fixed.push(line);
      continue;
    }

    // ── 4pre. Detect and rewrite natural-language paragraphs ──────
    // LLM output sometimes appends explanatory text after the diagram
    // body — e.g. "数据流 ：\n  页面加载时...".  These lines lack a
    // `%%` comment prefix and crash Mermaid 11.x.  Detect them and
    // convert to comments.
    if (isNaturalLanguageLine(line)) {
      dbg.push(`#${i} NATLANG`);
      fixed.push(`%% ${line.trim()}`);
      continue;
    }
    dbg.push(`#${i} after_natural: ${JSON.stringify(line)}`);

    // ── 4a. Detect & strip line-number prefixes added by LLMs ───────
    // e.g. "1. A[Start] --> B[End]" → "A[Start] --> B[End]"
    line = stripLineNumberPrefix(line);

    // ── 4b. Track subgraph depth for unclosed subgraph detection ───
    const trimmedLower = line.trim().toLowerCase();
    if (/^subgraph\s/.test(trimmedLower)) {
      subgraphDepth++;
    } else if (trimmedLower === 'end' && subgraphDepth > 0) {
      subgraphDepth--;
    }

    // ── 4c. Fix subgraph name: subgraph B [Label] is illegal ──────
    // Must run BEFORE fixSubgraphName which expects the simple `subgraph X` form
    line = fixSubgraphBracketName(line);

    // ── 4c2. Quote subgraph names with spaces / Chinese / special chars ─
    line = fixSubgraphName(line);

    // ── 4d. Fix node IDs: spaces / Chinese in the ID part ──────────
    line = sanitizeNodeId(line);

    // ── 4e1. Fix arrow labels with embedded double quotes ─────────
    // `B -- 点击 "新建" --> C`  →  `B -->|点击 "新建"| C`
    // Mermaid 11.x doesn't tolerate unescaped " inside arrow labels.
    line = fixArrowLabelWithQuotes(line);

    // ── 4e2. Quote labels containing Chinese / special chars ────────
    line = quoteLabelsWithSpecialChars(line);

    // ── 4f. Fix unescaped quotes inside node labels ────────────────
    line = fixUnescapedQuotesInLabels(line);

    // ── 4g. Fix HTML fragments in labels ────────────────────────────
    line = fixHtmlInLabels(line);

    // ── 4h. Fix arrow syntax ────────────────────────────────────────
    line = fixArrowSyntax(line);

    // ── 4i. Fix special characters in labels (ampersands, etc.) ────
    line = fixSpecialCharsInLabels(line);

    fixed.push(line);
  }

  // ── 5. Ensure subgraphs are closed ─────────────────────────────────
  while (subgraphDepth > 0) {
    fixed.push('end');
    subgraphDepth--;
  }

  // ── 6. Remove leading/trailing empty lines ──────────────────────────
  while (fixed.length > 0 && !fixed[0].trim()) fixed.shift();
  while (fixed.length > 0 && !fixed[fixed.length - 1].trim()) fixed.pop();

  // Debug: append trace on the global object so test scripts can read it
  (globalThis as any).__MERMAID_DEBUG__ = dbg.join('\n');

  return fixed.join('\n');
}

// ── Sanitizer helpers ──────────────────────────────────────────────────

/**
 * Heuristic: does this line look like a Mermaid syntax line, or is it
 * free-form natural-language text (which would crash Mermaid 11.x if
 * left unprefixed)?
 *
 * Returns true (= "treat as natural language") when the line:
 *   - has no Mermaid syntax markers (no arrows, no brackets, no
 *     subgraph/end/style keywords), AND
 *   - contains Chinese characters, OR
 *   - is a long free-form line with multiple spaces and no `[`/`{`/`(`.
 *
 * Comment lines (`%%`) and lines starting with Mermaid keywords are
 * always treated as syntax.
 */
function isNaturalLanguageLine(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed) return false;
  if (trimmed.startsWith('%%')) return false;

  // ── 1. Always-syntax line patterns ─────────────────────────────
  // If the line has any of these markers, trust that it IS Mermaid syntax.
  const SYNTAX_MARKERS = [
    /^\s*(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie|journey|gitgraph|mindmap|timeline|sankey|quadrantChart|xychart|requirementDiagram|c4Context|c4Container|c4Component|architecture|kanban|packet|radar|block)\b/i,
    /^\s*(subgraph|end|style|classDef|class|linkStyle|click)\b/i,
    /-->|==>|-\.->|-\.\.->|---|--x|==x|->>|--/,
    /\[[^\]]*\]/,                  // node[label]
    /\{[^}]*\}/,                   // node{label}
    /\([^)]*\)/,                   // node(label) or node((label))
    /^\s*\|/,                       // table row
    /:::/,                          // class assignment
    /^\s*%/,                        // directive line
  ];
  if (SYNTAX_MARKERS.some((re) => re.test(trimmed))) {
    return false;
  }

  // ── 2. Natural-language indicators ─────────────────────────────
  // Chinese characters present
  if (/[一-鿿㐀-䶿]/.test(trimmed)) {
    return true;
  }
  // Long line with no syntax markers and no brackets (e.g. "Page loads
  // with state initialized by GET /api/users...")
  if (trimmed.length > 60 && !/[\[\{\(]/.test(trimmed)) {
    return true;
  }
  // Multiple spaces + ends with period/comma (natural-language sentence)
  if (/\s{2,}/.test(trimmed) && /[。.,，;；:：]$/.test(trimmed)) {
    return true;
  }

  return false;
}

/**
 * Strip leading line numbers that LLMs sometimes prepend.
 *
 * Matches:
 *   "1. A[Start]"    → "A[Start]"
 *   "12. node1"       → "node1"
 *   "1) start"        → "start"
 *
 * Only strips when the line otherwise looks like Mermaid syntax
 * (has arrows, node defs, etc.) to avoid breaking labels that
 * legitimately start with a number-dot pattern.
 */
function stripLineNumberPrefix(line: string): string {
  return line.replace(
    /^(\s*)\d{1,3}[\.\)、．]\s+(?=[A-Za-z_][A-Za-z0-9_]*\s*[\[\(\{])/,
    '$1'
  );
}

/**
 * Fix the illegal `subgraph <id> [label]` syntax.  Mermaid only allows
 *   - `subgraph <id>`  (id is the displayed name)
 *   - `subgraph "<label>"`  (quoted label is the displayed name)
 * LLM-generated code frequently uses `subgraph B [UserManagement Page]`
 * which combines both forms and crashes Mermaid 11.x.  Convert to
 *   - `subgraph B["UserManagement Page"]`  (preserves the label visually)
 */
function fixSubgraphBracketName(line: string): string {
  return line.replace(
    /^(\s*subgraph\s+)([A-Za-z_][A-Za-z0-9_]*)\s+(\[[^\]]*\])\s*$/i,
    (_m, prefix: string, id: string, bracketExpr: string) => {
      const label = bracketExpr.slice(1, -1).trim();
      if (!label) return `${prefix}${id}`;
      return `${prefix}${id}["${label.replace(/"/g, '&quot;')}"]`;
    }
  );
}

/**
 * Quote subgraph names that contain spaces, Chinese, or special chars.
 *
 *   subgraph 用户登录模块        → subgraph "用户登录模块"
 *   subgraph User Login          → subgraph "User Login"
 *
 * NOTE: If `name` already contains a `[…]` (from fixSubgraphBracketName)
 * or `{…}` (rhombus/hexagon subgraph), the bracketed form is its own
 * quoted label — don't add another set of quotes.
 */
function fixSubgraphName(line: string): string {
  return line.replace(
    /^(\s*subgraph\s+)([^"\n].*)$/i,
    (match: string, prefix: string, name: string) => {
      const trimmed = name.trim();
      // Already quoted — leave alone
      if (trimmed.startsWith('"') && trimmed.endsWith('"')) return match;
      if (trimmed.startsWith("'") && trimmed.endsWith("'")) return match;
      // Already has a bracketed label (e.g. `B["..."]` from fixSubgraphBracketName)
      if (/^[\w_-]+\s*[\[\{]/.test(trimmed)) return match;
      // Contains spaces, Chinese, or special chars — quote it
      if (/[\s一-鿿]|[^\w-]/.test(trimmed)) {
        return `${prefix}"${trimmed}"`;
      }
      return match;
    }
  );
}

/**
 * Replace invalid characters in Mermaid node IDs.
 *
 * A Mermaid node ID may only contain letters, digits, and underscores.
 * This function keeps the original label but rewrites the ID part.
 *
 *   "用户 输入[用户输入]"        → "user_input["用户输入"]"
 *   "my-node! [My Node]"         → "my_node["My Node"]"
 */
function sanitizeNodeId(line: string): string {
  // Match patterns like:  <ws><id><optional_ws><bracket_style><...>
  // where bracket_style is [, {, (, ((, [/ etc.
  const nodePattern = /^(\s*)([^\[\(\{\n\s]+)(\s*)([\[\(\{])(.*)/;

  const match = line.match(nodePattern);
  if (!match) return line;

  const [, ws, rawId, idWs, bracketOpen, rest] = match;

  // If the raw ID is safe, skip
  if (SAFE_ID_RE.test(rawId)) return line;

  // Build a safe ID: replace invalid chars with underscore, collapse runs
  const safeId = rawId
    .replace(/[^A-Za-z0-9_]/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '')
    || 'node'; // fallback if all chars were invalid

  return `${ws}${safeId}${idWs}${bracketOpen}${rest}`;
}

/**
 * Characters/patterns that force a label to be double-quoted even if
 * the label otherwise looks "clean".  Mermaid 11.x is strict about
 * these inside unquoted bracket labels.
 */
const FORCE_QUOTE_PATTERNS: Array<{ re: RegExp; desc: string }> = [
  { re: /[一-鿿㐀-䶿]/, desc: 'CJK' },
  { re: /[：；，。！？、【】《》「」『』]/, desc: 'CJK punctuation' },
  { re: /[:;]/, desc: 'colon/semicolon' },           // e.g. "Step 1: Init"
  { re: /[\(\)]/, desc: 'parentheses' },              // e.g. "Call func()"
  { re: /^[^"]*\[[^\]]*\]/, desc: 'nested brackets' }, // e.g. "items[0]"
  { re: /\{[^}]*\}/, desc: 'curly braces' },          // e.g. "{key: val}"
  { re: /\d+\.\s/, desc: 'numbered prefix' },         // e.g. "1. First step"
  { re: /[#$%]/, desc: 'special char' },              // e.g. "$100", "#1"
  { re: /@/, desc: 'at sign' },
  { re: /<[a-zA-Z]/, desc: 'HTML-like' },            // e.g. "<div>"
  { re: /&(?!amp;|lt;|gt;|quot;|#39;|#\d+;|#x[\da-fA-F]+;)/, desc: 'bare ampersand' },
];

/** Returns true when a label SHOULD be double-quoted for Mermaid 11.x. */
function labelNeedsQuoting(label: string): boolean {
  const trimmed = label.trim();
  if (!trimmed) return false;
  // Already quoted — nothing to do
  if ((trimmed.startsWith('"') && trimmed.endsWith('"')) ||
      (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
    return false;
  }
  // Multi-word English (space + length > 2 chars)
  if (trimmed.includes(' ') && trimmed.length > 2) return true;
  // Check all force-quote patterns
  return FORCE_QUOTE_PATTERNS.some((p) => p.re.test(trimmed));
}

/**
 * Wrap labels containing special characters in double quotes.
 *
 * Mermaid 11.x is substantially stricter than 10.x about unquoted label
 * content.  This function detects labels that will cause parse failures
 * and proactively quotes them.
 *
 *   A[用户登录]       → A["用户登录"]
 *   B{条件？判断}      → B{"条件？判断"}
 *   C[Step 1: Init]   → C["Step 1: Init"]    (colon → quote)
 *   D[Cost: $100]     → D["Cost: $100"]       ($ → quote)
 *   E[items[0]]       → E["items[0]"]         (nested brackets → quote)
 */
function quoteLabelsWithSpecialChars(line: string): string {
  // Don't touch lines that are style/classDef/link directives
  if (/^\s*(style|classDef|class|linkStyle|click)\s/.test(line)) return line;
  // Don't touch subgraph declarations — `fixSubgraphBracketName` already
  // produced `subgraph B["..."]`, and re-quoting breaks the syntax.
  if (/^\s*subgraph\s/.test(line)) return line;
  // Don't touch lines that are just comments
  if (/^\s*%%/.test(line)) return line;

  // Match Mermaid bracket labels: nodeId[...] or nodeId{...} or nodeId(...) or nodeId((...))
  // The approach: for each line, locate bracket-like constructs that aren't
  // arrow syntax or subgraph declarations, and quote the label if needed.
  //
  // Simpler, more robust regex than before: matches the node-definition
  // bracket pairs one at a time.
  const bracketLabelRe = /([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*?\]|\{[^}]*?\}|\(\([^)]*?\)\)|\([^)]*?\))/g;

  return line.replace(bracketLabelRe, (fullMatch: string, nodeId: string, bracketExpr: string) => {
    // Extract the opening/closing brackets and the label inside
    const openClose = bracketExpr.charAt(0);
    let closeStr: string;
    let inner: string;

    if (openClose === '(' && bracketExpr.startsWith('((')) {
      inner = bracketExpr.slice(2, -2);
      closeStr = '))';
    } else if (openClose === '[') {
      inner = bracketExpr.slice(1, -1);
      closeStr = ']';
    } else if (openClose === '{') {
      inner = bracketExpr.slice(1, -1);
      closeStr = '}';
    } else if (openClose === '(') {
      inner = bracketExpr.slice(1, -1);
      closeStr = ')';
    } else {
      return fullMatch; // unrecognized — leave alone
    }

    // Check if quoting is needed
    if (!labelNeedsQuoting(inner)) return fullMatch;

    // Escape any existing double-quotes in the label
    const escaped = inner.replace(/"/g, '&quot;');
    return `${nodeId}${openClose === '(' && closeStr === '))' ? '((' : openClose}"${escaped}"${closeStr}`;
  });
}

/**
 * Fix unescaped double quotes inside Mermaid node labels.
 *
 * Mermaid uses " as the label delimiter.  If the label text itself
 * contains unescaped ", the parser loses track of where the label ends.
 *
 *   A["他说"你好""]  →  A["他说&quot;你好&quot;"]
 *
 * Strategy: find quoted labels (label starts and ends with ") and
 * escape any " found between the delimiters.
 */
function fixUnescapedQuotesInLabels(line: string): string {
  // 11.15.0 对所有括号形式都强制要求标签内引号转义
  // 覆盖 [..]、{..}、((..)) 三种形式
  return line.replace(
    /(\[[^\]]*?"[^\]]*?"[^\]]*\]|\{[^}]*?"[^}]*?"[^}]*\}|\(\([^)]*?"[^)]*?"[^)]*\)\))/g,
    (bracketExpr: string) => {
      let openCh: string;
      let closeCh: string;
      if (bracketExpr.startsWith('((')) {
        // ((..)) 形式：裁掉内层括号
        const inner = bracketExpr.slice(2, -2);
        const fixed = inner.replace(/"([^"]*?)"/g, (_q: string, quotedText: string) => {
          return `"${quotedText.replace(/"/g, '&quot;')}"`;
        });
        return `((${fixed}))`;
      } else if (bracketExpr.startsWith('[')) {
        openCh = '[';
        closeCh = ']';
      } else if (bracketExpr.startsWith('{')) {
        openCh = '{';
        closeCh = '}';
      } else {
        return bracketExpr;
      }
      const inner = bracketExpr.slice(1, -1);
      const fixed = inner.replace(/"([^"]*?)"/g, (_q: string, quotedText: string) => {
        return `"${quotedText.replace(/"/g, '&quot;')}"`;
      });
      return `${openCh}${fixed}${closeCh}`;
    }
  );
}

/**
 * Replace bare HTML fragments inside Mermaid labels.
 *
 * Mermaid 11.x allows the following HTML tags inside quoted labels:
 *   - `<br>` / `<br/>` / `<br />`  (line break)
 *   - `<i>...</i>`  `<b>...</b>`  `<em>...</em>`  `<strong>...</strong>`
 *   - `<u>...</u>`
 *   - `<code>...</code>` `<s>...</s>` `<sub>...</sub>` `<sup>...</sup>`
 *   - `<small>...</small>` `<mark>...</mark>` `<ins>...</ins>` `<del>...</del>`
 *
 * Other tags MUST be HTML-encoded to avoid parse failures.
 *
 *   A[click <br/> here]              → A["click <br/> here"]
 *   A[<b>important</b> text]         → A["<b>important</b> text"]
 *   A[random <div>block</div>]       → A["random &lt;div&gt;block&lt;/div&gt;"]
 */
const ALLOWED_HTML_TAGS = new Set([
  'br', 'i', 'b', 'em', 'strong', 'u', 'code', 's', 'sub', 'sup',
  'small', 'mark', 'ins', 'del', 'span', 'p',
]);

function fixHtmlInLabels(line: string): string {
  return line.replace(
    /<(\/?)([A-Za-z][A-Za-z0-9]*)([^>]*)>/g,
    (_match: string, slash: string, tag: string, attrs: string) => {
      const lowerTag = tag.toLowerCase();
      if (ALLOWED_HTML_TAGS.has(lowerTag)) {
        // <br/> must always be self-closed.  Strip any extra slash from
        // attrs and emit the canonical `<br/>` form.
        if (lowerTag === 'br' && !slash) {
          return `<${tag}/>`;
        }
        return `<${slash}${tag}${attrs}>`;
      }
      // Unknown tag — HTML-encode it
      return `&lt;${slash}${tag}${attrs}&gt;`;
    }
  );
}

/**
 * Fix arrow labels that contain unescaped double quotes.
 *
 * Mermaid 11.x crashes on:
 *   B -- 点击 "新建" --> C
 *   B -- 点击 "新建" --> C[UserForm Modal]
 *   B -. 点击 "新建" .-> C{Yes}
 *
 * Mermaid 11.x accepts:
 *   B -->|"点击 &quot;新建&quot;"| C
 *   B -- "点击 \"新建\"" --> C     (less common, may also fail)
 *   B --|"点击 \"新建\""|--> C
 *
 * Strategy:
 *   1. Strip a trailing target (id, [label], {label}, (label), ((label))).
 *   2. If the remaining text contains a "-->" or "-- " arrow with a
 *      quote-bearing label, rewrite to the `--|"..."|` form.
 */
function fixArrowLabelWithQuotes(line: string): string {
  // Match a trailing target: either a bracketed label
  //   [..]   {..}   (..)   ((..))
  // or a bare Mermaid node ID (alphanumeric/underscore).  This second
  // case is necessary for lines like `A -- "text" --> B`.
  const targetRe = /^(.*?)\s*(\[[^\]]*\]|\{[^}]*\}|\([^)]*\)|\(\([^)]*\)\)|[A-Za-z_][A-Za-z0-9_]*)\s*$/;
  const m = line.match(targetRe);
  if (!m) return line;

  // Make sure the "target" we stripped is actually at the end of the line,
  // and that there's at least one arrow `--` in the head.  A line like
  // `B["foo"]` should not be treated as `B + target "foo"`.
  const [, head, target] = m;
  if (!/--/.test(head)) return line;
  // Guard: if head has no arrow, this is a node definition, not an arrow.
  if (!/(?:--+)|(?:-\.)/.test(head)) return line;

  // ── Pattern A: head ends with `--` (no `>` tail) ────────────
  // e.g. "B -- 点击 \"新建\" --"
  let a1 = head.match(/^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s+--\s+([\s\S]+?)\s+--$/);
  if (a1) {
    const [, ws, srcId, label] = a1;
    if (label.includes('"')) {
      const escaped = label.replace(/"/g, '&quot;');
      return `${ws}${srcId} -->|"${escaped}"| ${target}`;
    }
    return line;
  }

  // ── Pattern B: head ends with `-->` (with optional trailing ID) ──
  // e.g. "B -- 点击 \"新建\" -->" or "B -- 点击 \"新建\" --> C"
  // Capture the optional trailing ID as `srcTail` so we can preserve it
  // in the output (it's NOT the target — that's already stripped).
  let a2 = head.match(/^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s+--\s+([\s\S]+?)\s+-->(?:\s+([A-Za-z_][A-Za-z0-9_]*))?$/);
  if (a2) {
    const [, ws, srcId, label, srcTail] = a2;
    if (label.includes('"')) {
      const escaped = label.replace(/"/g, '&quot;');
      const tailStr = srcTail ? ` ${srcTail}` : '';
      return `${ws}${srcId} -->|"${escaped}"|${tailStr} ${target}`;
    }
    return line;
  }

  // ── Pattern C: dotted arrow `-.`  /  `.->` ──────────────────
  let a3 = head.match(/^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s+-\.\s+([\s\S]+?)\s+\.->(?:\s+([A-Za-z_][A-Za-z0-9_]*))?$/);
  if (a3) {
    const [, ws, srcId, label, srcTail] = a3;
    if (label.includes('"')) {
      const escaped = label.replace(/"/g, '&quot;');
      const tailStr = srcTail ? ` ${srcTail}` : '';
      return `${ws}${srcId} -.->|"${escaped}"|${tailStr} ${target}`;
    }
    return line;
  }

  return line;
}

/**
 * Fix common arrow syntax errors.
 *
 *   A-->|label|>B    →  A -->|label| B     (missing spaces + extra >)
 *   A--label-->B     →  A -- label --> B    (missing spaces around label)
 *   A==>B            →  A ==> B             (no spaces — valid but we normalize)
 *   A-->B-->C        →  leave alone         (chained arrows are valid in 10.x)
 */
function fixArrowSyntax(line: string): string {
  // Fix |> at end of arrow label (common LLM mistake)
  // -->|text|> should be -->|text|
  line = line.replace(/\|>(\s*[A-Za-z_])/g, '| $1');

  // Fix -->|label|B (missing space before target node)
  line = line.replace(
    /(-->|--\s*>>?|==>|-\.-\.?>\|)(\|[^|]+\|)([A-Za-z_])/g,
    '$1$2 $3'
  );

  // Normalize: arrow without space before node
  line = line.replace(
    /(-->|--x|==>|-.->|-\.->)([A-Za-z_])/g,
    '$1 $2'
  );

  return line;
}

/**
 * Fix special characters that can break Mermaid parsing.
 *
 * - Bare `&` not already an HTML entity → `&amp;`
 * - Semicolons used as Chinese colon replacement → keep (Mermaid 11 handles them)
 */
function fixSpecialCharsInLabels(line: string): string {
  // Don't process directive lines
  if (/^\s*(style|classDef|class|linkStyle|click)\s/.test(line)) return line;

  // Replace bare & that aren't already HTML entities
  line = line.replace(
    /&(?!amp;|lt;|gt;|quot;|#39;|#\d+;|#x[0-9a-fA-F]+;)/g,
    '&amp;'
  );

  return line;
}

// ── Validation ─────────────────────────────────────────────────────────

/**
 * Quick check: does this code look like valid Mermaid?
 * Returns null if OK, or an error message string if issues detected.
 */
export function validateMermaidCode(code: string): string | null {
  if (!code || !code.trim()) {
    return '代码为空';
  }

  const firstLine = code.trim().split('\n')[0]?.trim() || '';
  const firstWord = (firstLine.split(/\s+/)[0] || '').toLowerCase();

  const hasValidStarter = VALID_STARTERS.has(firstWord);

  if (!hasValidStarter) {
    // Check if it's a valid but less common starter
    if (/^[a-z][a-zA-Z]+Diagram$/i.test(firstWord)) {
      return null; // e.g. "entityRelationshipDiagram" etc.
    }
    return `无效的图表类型: "${firstWord}"。期望: flowchart, sequenceDiagram, classDiagram 等`;
  }

  // Check for obvious parse-blockers
  if (code.includes('```mermaid') || code.includes('```')) {
    return '代码中包含 Markdown 代码块标记，请移除 ``` 后重试';
  }

  return null; // looks OK
}

// ── Progressive repair ─────────────────────────────────────────────────

/**
 * Attempt progressive repair of Mermaid code.
 *
 * Tries increasingly aggressive fixes:
 *   1. Standard sanitization
 *   2. Force-quote all node labels
 *   3. Strip all non-essential syntax (bare minimum)
 *
 * Returns the best attempt, or the original if nothing changed.
 */
export function repairMermaidCode(raw: string): { code: string; repaired: boolean } {
  if (!raw || !raw.trim()) return { code: raw, repaired: false };

  // Level 1: Standard sanitization
  const level1 = sanitizeMermaidCode(raw);
  if (level1 !== raw.trim()) {
    return { code: level1, repaired: true };
  }

  // Level 2: Aggressive label quoting — force-quote ALL labels
  const level2 = forceQuoteAllLabels(raw);
  if (level2 !== raw.trim()) {
    return { code: sanitizeMermaidCode(level2), repaired: true };
  }

  // Level 3: Bare minimum — strip to just the diagram type + nodes + arrows
  const level3 = stripToMinimal(raw);
  if (level3 !== raw.trim()) {
    return { code: level3, repaired: true };
  }

  return { code: raw, repaired: false };
}

/**
 * Level-2 repair: force-quote every node label that contains anything
 * beyond simple ASCII alphanumerics.  Uses the same detection logic as
 * quoteLabelsWithSpecialChars for consistency.
 */
function forceQuoteAllLabels(code: string): string {
  const lines = code.split('\n');
  const result: string[] = [];

  for (const line of lines) {
    // Skip directives, comments, subgraph declarations, and end
    if (/^\s*(style|classDef|class|linkStyle|click|subgraph\s|end\b|%%)\s/i.test(line.trim())) {
      result.push(line);
      continue;
    }

    // Match nodeId[BracketExpr] patterns
    const bracketLabelRe = /([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*?\]|\{[^}]*?\}|\(\([^)]*?\)\)|\([^)]*?\))/g;

    const fixed = line.replace(bracketLabelRe, (_full: string, nodeId: string, bracketExpr: string) => {
      const openCh = bracketExpr.charAt(0);
      let inner: string;
      let openStr: string;
      let closeStr: string;

      if (openCh === '(' && bracketExpr.startsWith('((')) {
        inner = bracketExpr.slice(2, -2);
        openStr = '((';
        closeStr = '))';
      } else if (openCh === '[') {
        inner = bracketExpr.slice(1, -1);
        openStr = '[';
        closeStr = ']';
      } else if (openCh === '{') {
        inner = bracketExpr.slice(1, -1);
        openStr = '{';
        closeStr = '}';
      } else if (openCh === '(') {
        inner = bracketExpr.slice(1, -1);
        openStr = '(';
        closeStr = ')';
      } else {
        return _full;
      }

      // Already quoted — leave alone
      const trimmed = inner.trim();
      if ((trimmed.startsWith('"') && trimmed.endsWith('"')) ||
          (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
        return _full;
      }

      // Quote if label has anything beyond simple alphanumerics + basic punctuation
      if (!labelNeedsQuoting(inner) && /^[A-Za-z0-9_\s.\-+|/\\]+$/.test(inner)) {
        return _full; // safe ASCII-only label
      }

      const escaped = trimmed.replace(/"/g, '&quot;');
      return `${nodeId}${openStr}"${escaped}"${closeStr}`;
    });
    result.push(fixed);
  }

  return result.join('\n');
}

/**
 * Level-3 repair: strip to minimal valid Mermaid.
 * Keeps only the diagram declaration and node/arrow definitions.
 */
function stripToMinimal(code: string): string {
  const lines = code.split('\n');
  const result: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      result.push(line);
      continue;
    }

    // Keep the diagram type declaration
    if (result.length === 0 && /^(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie|journey|gitgraph|mindmap|timeline|sankey)\b/i.test(trimmed)) {
      result.push(line);
      continue;
    }

    // Keep lines with arrows or node definitions
    if (/-->|---|==>|->>|-.->|-\.->|-\.\.->|--x|==x/.test(line)) {
      result.push(line);
      continue;
    }

    // Keep node definitions
    if (/[A-Za-z_][A-Za-z0-9_]*\s*[\[\{\(]/.test(line)) {
      result.push(line);
      continue;
    }

    // Keep subgraph/end
    if (/^\s*(subgraph|end)\b/i.test(trimmed)) {
      result.push(line);
      continue;
    }

    // Keep style/class directives
    if (/^\s*(style|classDef|class)\s/i.test(trimmed)) {
      result.push(line);
      continue;
    }

    // Drop everything else (comments stay)
    if (/^\s*%%/.test(trimmed)) {
      result.push(line);
      continue;
    }
  }

  return result.join('\n');
}
