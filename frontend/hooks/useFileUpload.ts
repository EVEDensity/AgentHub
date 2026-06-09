import { useCallback } from 'react';
import type { AttachedFile } from '../types';

const CHUNK_SIZE = 512 * 1024; // 512 KB
const MAX_INLINE = 2 * 1024 * 1024; // 2 MB
const MAX_TOTAL = 50 * 1024 * 1024; // 50 MB

function detectFileCategory(name: string): string {
  const ext = (name.split('.').pop() || '').toLowerCase();
  const configs: Record<string, string[]> = {
    code: ['py', 'js', 'ts', 'jsx', 'tsx', 'java', 'go', 'rs', 'c', 'cpp', 'h', 'hpp', 'swift', 'kt', 'rb', 'php', 'sql', 'sh', 'bash', 'vue', 'svelte', 'astro'],
    document: ['txt', 'md', 'pdf', 'docx', 'rtf', 'tex', 'rst', 'org', 'log'],
    image: ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp', 'ico'],
    archive: ['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz'],
    spreadsheet: ['xlsx', 'xls', 'csv', 'tsv'],
    presentation: ['pptx', 'ppt'],
    config: ['json', 'yaml', 'yml', 'xml', 'toml', 'ini', 'cfg', 'env', 'conf', 'cnf', 'editorconfig', 'gitignore', 'dockerfile', 'makefile', 'prisma', 'graphql', 'proto'],
  };
  for (const [cat, extensions] of Object.entries(configs)) {
    if (extensions.includes(ext)) return cat;
  }
  return 'unknown';
}

function extractApiError(err: unknown): string {
  if (err && typeof err === 'object' && 'detail' in err) {
    const detail = (err as Record<string, unknown>).detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return (detail as Array<{ msg?: string }>).map((d) => d.msg || '').filter(Boolean).join('; ') || 'Validation error';
    }
  }
  return 'Upload failed';
}

export interface UseFileUploadOptions {
  authHeaders: () => Record<string, string>;
  setAttachedFiles: React.Dispatch<React.SetStateAction<AttachedFile[]>>;
  setNotice: (msg: string) => void;
}

export function useFileUpload({ authHeaders, setAttachedFiles, setNotice }: UseFileUploadOptions) {
  async function uploadFileChunked(file: File): Promise<string> {
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

    const initRes = await fetch('/api/files/upload/init', { method: 'POST', headers: authHeaders() });
    const { uploadId } = (await initRes.json()) as { uploadId: string; chunkSizeHint: number };

    for (let i = 0; i < totalChunks; i++) {
      const start = i * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, file.size);
      const chunk = file.slice(start, end);

      const formData = new FormData();
      formData.append('file', chunk, `${file.name}.chunk${i}`);
      formData.append('upload_id', uploadId);
      formData.append('chunk_index', String(i));
      formData.append('total_chunks', String(totalChunks));
      formData.append('file_name', file.name);

      const res = await fetch('/api/files/upload/chunk', {
        method: 'POST',
        headers: authHeaders(),
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(extractApiError(err) || `Chunk ${i} failed`);
      }

      setAttachedFiles((prev) => prev.map((f) =>
        f.name === file.name
          ? { ...f, uploadProgress: Math.round(((i + 1) / totalChunks) * 100), uploadStatus: 'uploading' as const }
          : f,
      ));
    }

    const completeRes = await fetch('/api/files/upload/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ upload_id: uploadId, file_name: file.name, total_chunks: totalChunks }),
    });

    if (!completeRes.ok) {
      throw new Error('Upload completion failed');
    }

    return uploadId;
  }

  // Shared file-processing helper — used by both file input and clipboard paste
  function processFiles(files: File[]): void {
    files.forEach(async (file) => {
      const ext = (file.name.split('.').pop() || '').toLowerCase();
      const category = detectFileCategory(file.name);

      if (category === 'unknown') {
        setNotice(`不支持的文件类型: ${file.name}`);
        return;
      }

      if (file.size > MAX_TOTAL) {
        setNotice(`文件 ${file.name} 超过 50MB 限制`);
        return;
      }

      const base: AttachedFile = {
        name: file.name,
        size: file.size,
        type: file.type || 'application/octet-stream',
        category: category as AttachedFile['category'],
        uploadStatus: 'pending',
      };

      const isInlineText = (category === 'code' || category === 'config' || (category === 'document' && ext !== 'pdf' && ext !== 'docx' && ext !== 'rtf'));
      const isInlineImage = category === 'image' && file.size <= MAX_INLINE;
      const canInline = (isInlineText && file.size <= MAX_INLINE) || isInlineImage;

      if (canInline) {
        const reader = new FileReader();
        reader.onload = () => {
          setAttachedFiles((prev) => [...prev, {
            ...base,
            content: reader.result as string,
            uploadStatus: 'done' as const,
            uploadProgress: 100,
          }]);
        };
        reader.onerror = () => {
          setNotice(`读取文件失败: ${file.name}`);
        };
        if (isInlineImage) {
          reader.readAsDataURL(file);
        } else {
          reader.readAsText(file);
        }
      } else {
        setAttachedFiles((prev) => [...prev, { ...base, uploadProgress: 0 }]);

        try {
          const fileId = await uploadFileChunked(file);
          setAttachedFiles((prev) => prev.map((f) =>
            f.name === file.name
              ? { ...f, fileId, uploadStatus: 'done' as const, uploadProgress: 100 }
              : f,
          ));
        } catch (err: unknown) {
          const msg = err instanceof Error ? err.message : 'Upload failed';
          setAttachedFiles((prev) => prev.map((f) =>
            f.name === file.name
              ? { ...f, uploadStatus: 'error' as const, uploadError: msg }
              : f,
          ));
          setNotice(`上传失败: ${file.name} - ${msg}`);
        }
      }
    });
  }

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const fs = e.target.files;
    if (!fs || fs.length === 0) return;
    processFiles(Array.from(fs));
    e.target.value = '';
  }, []);

  const handlePasteFiles = useCallback((files: File[]) => {
    if (files.length === 0) return;
    processFiles(files);
    setNotice(`已从剪贴板添加 ${files.length} 张图片`);
  }, []);

  const handleRemoveFile = useCallback((index: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  return { handleFileChange, handlePasteFiles, handleRemoveFile };
}
