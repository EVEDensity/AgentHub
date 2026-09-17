import { normalizeReferences } from './references';
import type { AttachedFile, AttachmentMeta, FileReference } from '../types';

export interface OutgoingMessageDraft {
  aiContent: string;
  displayContent: string;
  attachments: AttachmentMeta[];
}

interface BuildOutgoingMessageDraftInput {
  text: string;
  files: AttachedFile[];
  references: FileReference[];
}

export function buildOutgoingMessageDraft({
  text,
  files,
  references,
}: BuildOutgoingMessageDraftInput): OutgoingMessageDraft {
  const trimmedText = text.trim();
  const attachments: AttachmentMeta[] = files.map((file) => ({
    name: file.name,
    size: file.size,
    type: file.type,
    category: file.category,
    fileId: file.fileId,
  }));

  let aiContent = trimmedText;
  if (files.length > 0) {
    const fileBlocks = files.map((file) => {
      const ext = file.name.split('.').pop()?.toLowerCase() || '';
      // MM-2 / ADR-0105: images are NEVER spliced into the body as base64
      // fences — they ride the structured attachments payload to the
      // backend, which converts them into vision-capable content parts.
      const isImage = file.type.startsWith('image/')
        || /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(file.name);
      if (isImage) {
        return `[Attached Image: ${file.name}]`;
      }
      if (file.fileId && !file.content) {
        return `[Attached File: ${file.name} (fileId: ${file.fileId})]`;
      }
      return `[Attached File: ${file.name}]\n\`\`\`${ext}\n${file.content || ''}\n\`\`\``;
    }).join('\n\n');
    aiContent = trimmedText ? `${trimmedText}\n\n---\n${fileBlocks}` : fileBlocks;
  }

  if (references.length > 0) {
    const normalized = normalizeReferences(references);
    const refBlocks = normalized.map((ref) => {
      const parts: string[] = [`[Referenced File: ${ref.path}]`];
      if (ref.lineStart) {
        const lineRange = ref.lineEnd && ref.lineEnd !== ref.lineStart
          ? `Lines ${ref.lineStart}-${ref.lineEnd}`
          : `Line ${ref.lineStart}`;
        parts.push(`(${lineRange})`);
      }
      if (ref.quote) {
        parts.push(`\n\`\`\`\n${ref.quote}\n\`\`\``);
      }
      return parts.join('');
    });
    const refContent = refBlocks.join('\n\n');
    aiContent = aiContent ? `${aiContent}\n\n---\n${refContent}` : refContent;
  }

  const displayContent = trimmedText || (files.length > 0 ? `发送了 ${files.length} 个文件` : '');

  return {
    aiContent,
    displayContent,
    attachments,
  };
}
