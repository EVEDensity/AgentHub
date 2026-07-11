// ── Workflow Variable Engine ───────────────────────────────────────────
// Resolves {{node_id.output}} and {{node_id.field}} references against
// a variable scope populated during workflow execution.
//
// Usage:
//   const engine = new VariableEngine(scope);
//   const result = engine.resolve("Summarize: {{codegen.output}}");
//   // → "Summarize: {resolved value}"

import type { VariableReference, WorkflowVariableScope } from '../../types';

// Regex to capture {{nodeId.field}} with optional nested paths
const VAR_RE = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_-]*)\.([a-zA-Z_][a-zA-Z0-9_.\[\]]*)\s*\}\}/g;

/**
 * Parse all variable references from a template string.
 */
export function extractVariables(template: string): VariableReference[] {
  const refs: VariableReference[] = [];
  const seen = new Set<string>();

  for (const match of template.matchAll(VAR_RE)) {
    const raw = match[0];
    if (seen.has(raw)) continue;
    seen.add(raw);

    refs.push({
      raw,
      nodeId: match[1],
      field: match[2],
      isResolved: false,
    });
  }

  return refs;
}

/**
 * Resolve a dot-path from an object, e.g. "result.code" → obj.result.code.
 * Supports array indices like "items[0].name".
 */
function getByPath(obj: unknown, path: string): unknown {
  if (obj === null || obj === undefined) return undefined;

  const segments = path
    .replace(/\[(\d+)\]/g, '.$1') // items[0] → items.0
    .split('.')
    .filter(Boolean);

  let current: unknown = obj;
  for (const seg of segments) {
    if (current === null || current === undefined) return undefined;
    if (typeof current === 'object') {
      current = (current as Record<string, unknown>)[seg];
    } else {
      return undefined;
    }
  }
  return current;
}

/**
 * Stringify a resolved value for interpolation into text.
 */
export function stringifyValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export class VariableEngine {
  private scope: WorkflowVariableScope;

  constructor(scope: WorkflowVariableScope) {
    this.scope = scope;
  }

  /**
   * Update the variable scope (e.g., after a node completes).
   */
  setScope(scope: WorkflowVariableScope): void {
    this.scope = scope;
  }

  /**
   * Set a single node's output into scope.
   */
  setNodeOutput(nodeId: string, output: unknown): void {
    this.scope.nodeOutputs[nodeId] = output;
  }

  /**
   * Get the available variable keys for UI pickers.
   * Returns e.g. ["codegen.output", "codegen.result", "orchestrator.output"]
   */
  getAvailableVariables(): string[] {
    const vars: string[] = [];
    for (const [nodeId, output] of Object.entries(this.scope.nodeOutputs)) {
      vars.push(`${nodeId}.output`);
      if (typeof output === 'object' && output !== null) {
        // Extract top-level keys for richer autocomplete
        for (const key of Object.keys(output as Record<string, unknown>)) {
          vars.push(`${nodeId}.${key}`);
        }
      }
    }
    return vars;
  }

  /**
   * Get available node IDs (for variable picker grouping).
   */
  getAvailableNodes(): Array<{ nodeId: string; preview: string }> {
    return Object.entries(this.scope.nodeOutputs).map(([nodeId, output]) => ({
      nodeId,
      preview: typeof output === 'string'
        ? output.slice(0, 60)
        : (typeof output === 'object' && output !== null
            ? JSON.stringify(output).slice(0, 60)
            : String(output).slice(0, 60)),
    }));
  }

  /**
   * Check if a specific variable is available in scope.
   */
  isAvailable(nodeId: string, field: string): boolean {
    return getByPath(this.scope.nodeOutputs[nodeId], field) !== undefined;
  }

  /**
   * Resolve a single {{nodeId.field}} reference to its string value.
   */
  resolveReference(raw: string): string {
    const match = raw.match(/^\{\{\s*([a-zA-Z_][a-zA-Z0-9_-]*)\.([a-zA-Z_][a-zA-Z0-9_.\[\]]*)\s*\}\}$/);
    if (!match) return raw;

    const [, nodeId, field] = match;
    const value = getByPath(this.scope.nodeOutputs[nodeId], field);
    return stringifyValue(value);
  }

  /**
   * Resolve all {{...}} references in a template string.
   * Unresolved references are left as-is (shown as raw {{...}}).
   */
  resolve(template: string): string {
    if (!template) return template;
    return template.replace(VAR_RE, (match) => {
      const resolved = this.resolveReference(match);
      // If resolved is empty string and the raw value isn't intentionally empty,
      // leave the placeholder so users see it's unresolved.
      return resolved !== '' ? resolved : match;
    });
  }

  /**
   * Resolve with error reporting — returns both the resolved string
   * and a list of any unresolved references.
   */
  resolveWithReport(template: string): {
    result: string;
    unresolved: string[];
    resolved: string[];
  } {
    const unresolved: string[] = [];
    const resolved: string[] = [];

    const result = template.replace(VAR_RE, (match) => {
      const value = this.resolveReference(match);
      if (value === '' && match !== '{{trigger.input}}') {
        unresolved.push(match);
        return match;
      }
      resolved.push(match);
      return value;
    });

    return { result, unresolved, resolved };
  }

  /**
   * Resolve all variables in an object (recursively walks strings).
   */
  resolveObject(obj: Record<string, unknown>): Record<string, unknown> {
    const result: Record<string, unknown> = { ...obj };
    for (const key of Object.keys(result)) {
      const val = result[key];
      if (typeof val === 'string') {
        result[key] = this.resolve(val);
      } else if (typeof val === 'object' && val !== null) {
        result[key] = this.resolveObject(val as Record<string, unknown>);
      }
    }
    return result;
  }
}

/**
 * Build a variable scope from a list of node execution results.
 */
export function buildScopeFromExecution(
  nodeResults: Array<{ nodeId: string; output?: unknown }>,
  triggerInput?: string
): WorkflowVariableScope {
  const nodeOutputs: Record<string, unknown> = {};
  for (const nr of nodeResults) {
    if (nr.output !== undefined) {
      nodeOutputs[nr.nodeId] = nr.output;
    }
  }
  return {
    nodeOutputs,
    triggerInput,
    workflowParams: {},
  };
}
