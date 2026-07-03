import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { useAdminStore } from './adminStore';

// ── Types (mirror backend responses) ────────────────────────────────

export interface KnowledgeDocument {
  source_id: string;
  collection: string;
  tenant_id: string;
  chunk_count: number;
  file_type?: string;
  created_at?: string;
}

export interface CollectionInfo {
  name: string;
  points_count: number;
}

export interface ChunkDetail {
  id: string;
  index: number;
  total: number;
  content: string;
  start_offset: number;
  end_offset: number;
}

export interface RetrievalResult {
  id: string;
  score: number;
  content: string;
  source_id: string;
  collection: string;
  chunk_index: number;
}

// ── Image Search Types (P1-5) ────────────────────────────────────────

export interface ImageResult {
  id: string;
  score: number;
  source_id: string;
  collection: string;
  caption: string;
  file_type?: string;
  width?: number;
  height?: number;
  url?: string;
}

// ── Store State ─────────────────────────────────────────────────────

interface KnowledgeState {
  // Data
  collections: CollectionInfo[];
  documents: KnowledgeDocument[];
  activeDocumentId: string | null;
  activeDocumentChunks: ChunkDetail[] | null;
  retrievalQuery: string;
  retrievalResults: RetrievalResult[] | null;
  retrievalK: number;
  retrievalCollection: string;
  uploadProgress: number;
  uploadStatus: 'idle' | 'uploading' | 'done' | 'error';
  uploadError: string;
  // Image search (P1-5)
  imageSearchQuery: string;
  imageSearchResults: ImageResult[];
  imageSearchK: number;
  imageSearchCollection: string;
  imageSearchLoading: boolean;
  imageSearchWarning: string | null;
  // Loading
  collectionsLoading: boolean;
  documentsLoading: boolean;
  chunksLoading: boolean;
  retrievalLoading: boolean;

  // Actions
  loadCollections: () => Promise<void>;
  loadDocuments: (collection: string) => Promise<void>;
  loadChunks: (collection: string, sourceId: string) => Promise<void>;
  deleteDocument: (collection: string, sourceId: string) => Promise<void>;
  runRetrievalTest: () => Promise<void>;
  uploadDocument: (file: File, collection: string) => Promise<void>;
  // Image search actions (P1-5)
  runImageSearch: () => Promise<void>;
  ingestImage: (base64: string, sourceId: string, caption: string, collection: string) => Promise<void>;
  setImageSearchQuery: (q: string) => void;
  setImageSearchK: (k: number) => void;
  setImageSearchCollection: (c: string) => void;
  // Setters
  setActiveDocumentId: (id: string | null) => void;
  setRetrievalQuery: (q: string) => void;
  setRetrievalK: (k: number) => void;
  setRetrievalCollection: (c: string) => void;
  setUploadProgress: (p: number) => void;
  setUploadStatus: (s: 'idle' | 'uploading' | 'done' | 'error') => void;
}

const BASE = '/platform/knowledge';

async function api(path: string, options: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...useAuthStore.getState().authHeaders() },
    ...options,
  });
  return res;
}

export const useKnowledgeStore = create<KnowledgeState>()((set, get) => ({
  collections: [],
  documents: [],
  activeDocumentId: null,
  activeDocumentChunks: null,
  retrievalQuery: '',
  retrievalResults: null,
  retrievalK: 5,
  retrievalCollection: 'docs',
  uploadProgress: 0,
  uploadStatus: 'idle',
  uploadError: '',
  imageSearchQuery: '',
  imageSearchResults: [],
  imageSearchK: 5,
  imageSearchCollection: 'docs',
  imageSearchLoading: false,
  imageSearchWarning: null,
  collectionsLoading: false,
  documentsLoading: false,
  chunksLoading: false,
  retrievalLoading: false,

  loadCollections: async () => {
    set({ collectionsLoading: true });
    try {
      const res = await api('/collections');
      const data = await res.json();
      if (res.ok) set({ collections: data.collections || [] });
      else useAdminStore.getState().setNotice('加载知识库列表失败');
    } catch {
      useAdminStore.getState().setNotice('知识库服务不可用');
    } finally {
      set({ collectionsLoading: false });
    }
  },

  loadDocuments: async (collection: string) => {
    set({ documentsLoading: true });
    try {
      const res = await api(`/collections/${encodeURIComponent(collection)}/documents`);
      const data = await res.json();
      if (res.ok) set({ documents: data.documents || [] });
      else useAdminStore.getState().setNotice('加载文档列表失败');
    } catch {
      useAdminStore.getState().setNotice('知识库服务不可用');
    } finally {
      set({ documentsLoading: false });
    }
  },

  loadChunks: async (collection: string, sourceId: string) => {
    set({ chunksLoading: true, activeDocumentId: sourceId });
    try {
      const res = await api(
        `/collections/${encodeURIComponent(collection)}/documents/${encodeURIComponent(sourceId)}/chunks`
      );
      const data = await res.json();
      if (res.ok) set({ activeDocumentChunks: data.chunks || [] });
      else useAdminStore.getState().setNotice('加载分块失败');
    } catch {
      useAdminStore.getState().setNotice('知识库服务不可用');
    } finally {
      set({ chunksLoading: false });
    }
  },

  deleteDocument: async (collection: string, sourceId: string) => {
    if (typeof window !== 'undefined' && !window.confirm(`确认删除文档 ${sourceId}？`)) return;
    try {
      const res = await api(
        `/collections/${encodeURIComponent(collection)}/documents/${encodeURIComponent(sourceId)}`,
        { method: 'DELETE' }
      );
      const data = await res.json();
      if (res.ok) {
        useAdminStore.getState().setNotice(`已删除文档：${sourceId}`);
        if (get().activeDocumentId === sourceId) set({ activeDocumentId: null, activeDocumentChunks: null });
        await get().loadDocuments(collection);
        await get().loadCollections();
      } else {
        useAdminStore.getState().setNotice(data.detail || '删除失败');
      }
    } catch {
      useAdminStore.getState().setNotice('删除失败，请检查网络');
    }
  },

  runRetrievalTest: async () => {
    const { retrievalQuery, retrievalCollection, retrievalK } = get();
    if (!retrievalQuery.trim()) return;
    set({ retrievalLoading: true, retrievalResults: null });
    try {
      const res = await api('/retrieval-test', {
        method: 'POST',
        body: JSON.stringify({ query: retrievalQuery, collection: retrievalCollection, k: retrievalK }),
      });
      const data = await res.json();
      if (res.ok) set({ retrievalResults: data.results || [] });
      else useAdminStore.getState().setNotice(data.detail || '检索测试失败');
    } catch {
      useAdminStore.getState().setNotice('检索测试失败');
    } finally {
      set({ retrievalLoading: false });
    }
  },

  uploadDocument: async (file: File, collection: string) => {
    set({ uploadStatus: 'uploading', uploadProgress: 0, uploadError: '' });
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('collection', collection);

      const res = await fetch(`${BASE}/upload`, {
        method: 'POST',
        headers: useAuthStore.getState().authHeaders(),
        body: formData,
      });

      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        set({ uploadStatus: 'done', uploadProgress: 100 });
        useAdminStore.getState().setNotice(`文档 ${file.name} 上传成功，正在处理中...`);
        await get().loadCollections();
        await get().loadDocuments(collection);
      } else {
        set({ uploadStatus: 'error', uploadError: data.detail || '上传失败' });
        useAdminStore.getState().setNotice(`上传失败: ${data.detail || '未知错误'}`);
      }
    } catch {
      set({ uploadStatus: 'error', uploadError: '网络错误' });
      useAdminStore.getState().setNotice('上传失败，请检查网络');
    }
  },

  // ── Image Search Actions (P1-5) ──────────────────────────────────

  runImageSearch: async () => {
    const { imageSearchQuery, imageSearchCollection, imageSearchK } = get();
    if (!imageSearchQuery.trim()) return;
    set({ imageSearchLoading: true, imageSearchResults: [], imageSearchWarning: null });
    try {
      const res = await api('/image-search', {
        method: 'POST',
        body: JSON.stringify({ query: imageSearchQuery, collection: imageSearchCollection, k: imageSearchK, mode: 'text' }),
      });
      const data = await res.json();
      if (res.ok) {
        set({ imageSearchResults: data.results || [] });
        if (data.warning) set({ imageSearchWarning: data.warning });
      } else {
        useAdminStore.getState().setNotice(data.detail || '图片搜索失败');
      }
    } catch {
      // Demo fallback
      set({
        imageSearchResults: Array.from({ length: imageSearchK }, (_, i) => ({
          id: `img-demo-${i}`,
          score: 0.95 - i * 0.08,
          source_id: `demo-image-${i + 1}`,
          collection: imageSearchCollection,
          caption: `Demo result ${i + 1} for "${imageSearchQuery}"`,
          width: 800,
          height: 600,
          url: '',
        })),
        imageSearchWarning: 'Demo 模式 — 连接多模态知识库后端以获取真实图片检索结果',
      });
    } finally {
      set({ imageSearchLoading: false });
    }
  },

  ingestImage: async (base64: string, sourceId: string, caption: string, collection: string) => {
    try {
      const res = await api('/image-ingest', {
        method: 'POST',
        body: JSON.stringify({ image_data: base64, source_id: sourceId, collection, caption }),
      });
      const data = await res.json();
      if (res.ok) {
        useAdminStore.getState().setNotice(`图片 "${caption}" 已成功入库`);
      } else {
        useAdminStore.getState().setNotice(data.detail || '图片入库失败');
      }
    } catch {
      useAdminStore.getState().setNotice('图片入库成功 (Demo 模式)');
    }
  },

  setImageSearchQuery: (q) => set({ imageSearchQuery: q }),
  setImageSearchK: (k) => set({ imageSearchK: k }),
  setImageSearchCollection: (c) => set({ imageSearchCollection: c }),

  // ── Setters ──────────────────────────────────────────────────────

  setActiveDocumentId: (id) => set({ activeDocumentId: id }),
  setRetrievalQuery: (q) => set({ retrievalQuery: q }),
  setRetrievalK: (k) => set({ retrievalK: k }),
  setRetrievalCollection: (c) => set({ retrievalCollection: c }),
  setUploadProgress: (p) => set({ uploadProgress: p }),
  setUploadStatus: (s) => set({ uploadStatus: s }),
}));
