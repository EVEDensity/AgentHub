/**
 * Artifact Citation Parser
 *
 * Parses the `{{artifact:source_id:chunk_id}}` citation syntax used
 * throughout AgentHub to reference knowledge-base chunks, agent outputs,
 * and generated artifacts.
 *
 * Part of AgentHub V5.1 §11 — Artifact Preview & Citation System
 */

/** Parsed citation reference */
export interface ArtifactCitation {
  /** Full matched syntax, e.g. "{{artifact:doc-42:c-3}}" */
  raw: string;
  /** Source document or artifact ID */
  sourceId: string;
  /** Specific chunk ID within the source (optional) */
  chunkId?: string;
  /** Start index in the original text */
  start: number;
  /** End index in the original text */
  end: number;
}

// Regex: {{artifact:<source_id>[:<chunk_id>]}}
// Allows optional chunk_id for referencing specific sections
const CITATION_RE = /\{\{artifact:([^:}]+)(?::([^}]+))?\}\}/g;

/**
 * Extract all artifact citations from a text string.
 *
 * @example
 *   const citations = parseArtifactCitations(
 *     "@CodeGen rewrite this: {{artifact:art-7:c-3}}"
 *   );
 *   // [{ raw: "{{artifact:art-7:c-3}}", sourceId: "art-7", chunkId: "c-3", ... }]
 */
export function parseArtifactCitations(text: string): ArtifactCitation[] {
  const citations: ArtifactCitation[] = [];
  let match: RegExpExecArray | null;

  // Reset lastIndex (regex is global)
  CITATION_RE.lastIndex = 0;

  while ((match = CITATION_RE.exec(text)) !== null) {
    citations.push({
      raw: match[0],
      sourceId: match[1],
      chunkId: match[2] || undefined,
      start: match.index,
      end: match.index + match[0].length,
    });
  }

  return citations;
}

/**
 * Replace artifact citations in text with rendered HTML links.
 * Used for displaying messages with inline artifact references.
 *
 * @example
 *   renderArtifactCitations(
 *     "See {{artifact:doc-1:c-3}} for details",
 *     (sourceId, chunkId) => `<a href="/artifact/${sourceId}#${chunkId}">📄 ${sourceId}</a>`
 *   );
 */
export function renderArtifactCitations(
  text: string,
  renderLink: (sourceId: string, chunkId?: string) => string,
): string {
  return text.replace(CITATION_RE, (_full, sourceId: string, chunkId?: string) => {
    return renderLink(sourceId.trim(), chunkId?.trim());
  });
}

/**
 * Split text into segments of plain text and artifact citations.
 * Useful for React rendering with mixed text and citation components.
 */
export type TextSegment =
  | { type: 'text'; value: string; key: string }
  | { type: 'citation'; sourceId: string; chunkId?: string; key: string };

export function splitArtifactSegments(text: string): TextSegment[] {
  const segments: TextSegment[] = [];
  let lastIndex = 0;
  let citationIndex = 0;

  CITATION_RE.lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = CITATION_RE.exec(text)) !== null) {
    // Add preceding text segment
    if (match.index > lastIndex) {
      segments.push({
        type: 'text',
        value: text.slice(lastIndex, match.index),
        key: `text-${lastIndex}`,
      });
    }

    // Add citation segment
    segments.push({
      type: 'citation',
      sourceId: match[1],
      chunkId: match[2] || undefined,
      key: `citation-${citationIndex++}`,
    });

    lastIndex = match.index + match[0].length;
  }

  // Add remaining text
  if (lastIndex < text.length) {
    segments.push({
      type: 'text',
      value: text.slice(lastIndex),
      key: `text-${lastIndex}`,
    });
  }

  return segments;
}

/**
 * Create a citation string from source and chunk IDs.
 *
 * @example
 *   formatArtifactCitation("doc-42", "c-3")
 *   // "{{artifact:doc-42:c-3}}"
 */
export function formatArtifactCitation(sourceId: string, chunkId?: string): string {
  if (chunkId) {
    return `{{artifact:${sourceId}:${chunkId}}}`;
  }
  return `{{artifact:${sourceId}}}`;
}
