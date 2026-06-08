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
 */
const INVISIBLE_CHARS_RE =
  /[\u200B\u200C\u200D\u200E\u200F\uFEFF\u00A0\u2060\u2061\u2062\u2063\u2064\u2028\u2029\u202A\u202B\u202C\u202D\u202E\u202F\u180E]/gu;

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

  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];

    // Skip fully empty lines (Mermaid tolerates these)
    if (!line.trim()) {
      fixed.push(line);
      continue;
    }

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

    // ── 4c. Fix subgraph names with spaces / special chars ──────────
    line = fixSubgraphName(line);

    // ── 4d. Fix node IDs: spaces / Chinese in the ID part ──────────
    line = sanitizeNodeId(line);

    // ── 4e. Quote labels containing Chinese / special chars ────────
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

  return fixed.join('\n');
}

// ── Sanitizer helpers ──────────────────────────────────────────────────

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
 * Quote subgraph names that contain spaces, Chinese, or special chars.
 *
 *   subgraph 用户登录模块        → subgraph "用户登录模块"
 *   subgraph User Login          → subgraph "User Login"
 */
function fixSubgraphName(line: string): string {
  return line.replace(
    /^(\s*subgraph\s+)([^"\n].*)$/i,
    (match: string, prefix: string, name: string) => {
      const trimmed = name.trim();
      // Already quoted — leave alone
      if (trimmed.startsWith('"') && trimmed.endsWith('"')) return match;
      if (trimmed.startsWith("'") && trimmed.endsWith("'")) return match;
      // Contains spaces, Chinese, or special chars — quote it
      if (/[\s一-鿿一-鿿]|[^\w-]/.test(trimmed)) {
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
 * Wrap labels containing Chinese characters, spaces, or special chars
 * in double quotes — Mermaid 10.x / 11.x handle quoted labels more reliably.
 *
 *   A[用户登录]     → A["用户登录"]
 *   B{条件？判断}    → B{"条件？判断"}
 *   C[Step 1: Init] → C["Step 1: Init"]
 */
function quoteLabelsWithSpecialChars(line: string): string {
  // Don't touch lines that are style/classDef/link directives
  if (/^\s*(style|classDef|class|linkStyle|click)\s/.test(line)) return line;
  // Don't touch lines that are just comments
  if (/^\s*%%/.test(line)) return line;

  // Match node definitions with unquoted labels:
  //   nodeId[LABEL] / nodeId{LABEL} / nodeId(LABEL) / nodeId((LABEL))
  // Handles single-bracket forms.  We intentionally skip already-quoted labels.
  //
  // Strategy: find bracket pairs and check if the content needs quoting.
  return line.replace(
    /(\[(\/\/|\\\\)?|\{|\(\(?)([^"\]\}\)\n].*?)(\](\/\/|\\\\)?|\}|\)\)?)/g,
    (fullMatch: string) => {
      // Extract bracket type and label
      const m = fullMatch.match(/^([\[\(\{]+)(.*?)([\]\)\}]+)$/);
      if (!m) return fullMatch;

      const [, open, label, close] = m;
      const trimmed = label.trim();

      // Already quoted — skip
      if ((trimmed.startsWith('"') && trimmed.endsWith('"')) ||
          (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
        return fullMatch;
      }

      // Check whether this label needs quoting
      const needsQuoting =
        /[一-鿿一-鿿㐀-䶿]/.test(trimmed) || // Chinese
        /[：；，。！？、@#￥%…&*（）【】《》「」『』]/.test(trimmed) || // CJK punctuation
        (trimmed.includes(' ') && trimmed.length > 2) || // multi-word
        trimmed.includes('<') || // potential HTML
        trimmed.includes('"') || // embedded double quotes
        trimmed.includes("'") || // embedded single quotes
        /[<>]/.test(trimmed);    // angle brackets

      if (!needsQuoting) return fullMatch;

      // Escape existing quotes
      const escaped = trimmed.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
      return `${open}"${escaped}"${close}`;
    }
  );
}

/**
 * Fix unescaped double quotes inside Mermaid node labels.
 *
 *   A["他说"你好""]  →  A["他说&quot;你好&quot;"]
 */
function fixUnescapedQuotesInLabels(line: string): string {
  // Find quoted labels and fix nested quotes
  return line.replace(
    /(\[[^\]]*?"[^\]]*?"[^\]]*\])/g,
    (match: string) => {
      return match.replace(
        /\[(.*)\]/g,
        (_inner: string, label: string) => {
          const fixed = label.replace(/"/g, '&quot;');
          return `[${fixed}]`;
        }
      );
    }
  );
}

/**
 * Replace bare HTML fragments inside Mermaid labels.
 *
 *   A[click <br/> here] → A[click &lt;br/&gt; here]
 */
function fixHtmlInLabels(line: string): string {
  // Replace obvious HTML tags inside labels
  return line.replace(
    /<(\/?\w+)[^>]*>/g,
    (_match: string, tag: string) => {
      // If it's already escaped, leave alone
      return `&lt;${tag}&gt;`;
    }
  );
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
 * Level-2 repair: force-quote every node label regardless of content.
 * This solves edge cases where special chars slip through other filters.
 */
function forceQuoteAllLabels(code: string): string {
  const lines = code.split('\n');
  const result: string[] = [];

  for (const line of lines) {
    // Skip directives, comments, and subgraph declarations
    if (/^\s*(style|classDef|class|linkStyle|click|subgraph|end|%%)\s/i.test(line)) {
      result.push(line);
      continue;
    }

    // Force quote all bracket labels
    const fixed = line.replace(
      /(\[[^"\]]*?\]|\{[^"}]*?\}|\(\([^")]*?\)\)|\([^")]*?\))/g,
      (match: string) => {
        // Extract bracket type and content
        if (match.startsWith('((')) {
          const inner = match.slice(2, -2);
          if (inner.startsWith('"') || !/[一-鿿\s'"<>]/.test(inner)) return match;
          return `(("${inner.replace(/"/g, '&quot;')}"))`;
        }
        if (match.startsWith('[')) {
          const inner = match.slice(1, -1);
          if (inner.startsWith('"') || !/[一-鿿\s'"<>]/.test(inner)) return match;
          return `["${inner.replace(/"/g, '&quot;')}"]`;
        }
        if (match.startsWith('{')) {
          const inner = match.slice(1, -1);
          if (inner.startsWith('"') || !/[一-鿿\s'"<>]/.test(inner)) return match;
          return `{"${inner.replace(/"/g, '&quot;')}"}`;
        }
        if (match.startsWith('(')) {
          const inner = match.slice(1, -1);
          if (inner.startsWith('"') || !/[一-鿿\s'"<>]/.test(inner)) return match;
          return `("${inner.replace(/"/g, '&quot;')}")`;
        }
        return match;
      }
    );
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
