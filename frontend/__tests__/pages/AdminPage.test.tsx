import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import React from 'react';

// ── All mock setup uses vi.hoisted() for vitest's hoist mechanism ──────

const { createMockStore, mockAuthState, mockAdminState, mockAgentState, mockMemoryStoreState, mockUserMgmtState, mockWorkflowState } = vi.hoisted(() => {
  const createMockStore = (defaultState: Record<string, unknown>) => {
    return vi.fn((selector?: (s: unknown) => unknown) => {
      if (typeof selector === 'function') return selector(defaultState);
      return defaultState;
    });
  };

  const mockVoid = vi.fn();

  const mockAuthState = {
    user: { id: '1', name: 'Admin', role: 'admin' },
    setUser: mockVoid,
    setToken: mockVoid,
    authHeaders: vi.fn(() => ({ Authorization: 'Bearer test' })),
    fmtErr: vi.fn((d: unknown, f: string) => (typeof d === 'string' ? d : f)),
  };

  const mockAdminState = {
    activeMenu: '服务商',
    notice: '',
    setActiveMenu: mockVoid,
    setNotice: mockVoid,
  };

  const mockAgentState = {
    agents: [], agentTests: {}, adapterOptions: [],
    selectedAdapterInfo: null, editSelectedAdapterInfo: null,
    defaultChatAgent: 'Orchestrator', isCreatingAgent: false,
    showLocalAgentModal: false, editingAgentId: null,
    newAgent: {
      agentId: '', domain: '', adapterType: 'deepseek', baseModelName: '',
      rankLevel: 'L1', dutyNote: '', displayName: '', avatarUrl: '',
      capabilityTags: [], baseUrl: '', apiKey: '',
      systemPrompt: '', userPrompt: '', assistantPrompt: '',
      promptVariables: {},
      publicConfig: { enabled: false, welcomeMessage: '', placeholder: '', themeColor: '#6366f1', logoUrl: '', suggestedQuestions: [] },
    },
    editAgent: {
      agentId: '', domain: '', adapterType: 'deepseek', baseModelName: '',
      rankLevel: 'L1', dutyNote: '', displayName: '', avatarUrl: '',
      capabilityTags: [], baseUrl: '', apiKey: '',
      systemPrompt: '', userPrompt: '', assistantPrompt: '',
      promptVariables: {},
      publicConfig: { enabled: false, welcomeMessage: '', placeholder: '', themeColor: '#6366f1', logoUrl: '', suggestedQuestions: [] },
    },
    fetchAdapters: mockVoid, refresh: mockVoid, createAgent: mockVoid,
    testAgent: mockVoid, removeAgent: mockVoid, startEditAgent: mockVoid,
    cancelEditAgent: mockVoid, saveAgentEdit: mockVoid,
    handleSetDefaultChatAgent: mockVoid, handleAdapterChange: mockVoid,
    setNewAgent: mockVoid, setEditAgent: mockVoid,
    setSelectedAdapterInfo: mockVoid, setEditSelectedAdapterInfo: mockVoid,
    setIsCreatingAgent: mockVoid, setShowLocalAgentModal: mockVoid,
    setEditingAgentId: mockVoid,
  };

  const mockMemoryStoreState = {
    init: mockVoid, loadMemoryFiles: mockVoid, loadMemoryDetail: mockVoid,
    saveMemoryDetail: mockVoid, setMemoryKeyword: mockVoid,
    setActiveMemoryFile: mockVoid, setMemoryBodyDraft: mockVoid,
    setMemoryDirty: mockVoid, setMemoryPreview: mockVoid,
    setMemorySubTab: mockVoid, setShowTrash: mockVoid,
    setShowDeleteConfirm: mockVoid, setPendingDeleteFile: mockVoid,
    setConsolidationDryRun: mockVoid, setMemorySearchQuery: mockVoid,
    setMemorySearchResults: mockVoid, handleExportMemory: mockVoid,
    handleImportMemory: mockVoid, confirmDeleteMemory: mockVoid,
    handleDeleteMemory: mockVoid, loadTrash: mockVoid,
    handleRecoverFromTrash: mockVoid, handlePurgeFromTrash: mockVoid,
    loadSessionSummaries: mockVoid, loadSessionDetail: mockVoid,
    loadGlobalSummary: mockVoid, refreshGlobalSummary: mockVoid,
    runConsolidation: mockVoid, runMemorySearch: mockVoid,
    getFilteredMemoryFiles: vi.fn(() => []),
    loadSessionMemoryList: mockVoid, loadSessionMemoryConversation: mockVoid,
    consolidateSessionMemory: mockVoid, createMemorySession: mockVoid,
    updateSessionTopic: mockVoid,
    memoryLoading: false, memoryError: null, memoryKeyword: '',
    memoryFiles: [], activeMemoryFile: null, memoryDetail: null,
    memoryBodyDraft: '', memoryDirty: false, memoryPreview: null,
    memorySubTab: 'files', sessionList: [], sessionsLoading: false,
    activeSessionId: null, activeSessionSummary: null,
    globalSummary: null, globalSummaryLoading: false,
    consolidationLoading: false, consolidationResult: null,
    consolidationError: null, consolidationDryRun: false,
    memorySearchQuery: '', memorySearchResults: null,
    memorySearchLoading: false, showTrash: false, trashItems: [],
    trashLoading: false, showDeleteConfirm: false, pendingDeleteFile: null,
    sessionMemoryList: [], sessionMemoryLoading: false,
    activeSessionMemoryId: null, sessionMemoryConversation: [],
    sessionMemoryConversationLoading: false,
  };

  const mockUserMgmtState = {
    ...mockMemoryStoreState,
    tokenData: null, tokenLoading: false, tokenError: null,
    profileBio: '', profileEditingField: null, profileFieldDraft: '',
    profileLocation: '', profileEmail: '', profileOrg: '',
    profileAvatarUrl: '', profileUploading: false,
    userList: [], userListLoading: false, userListError: null,
    newUserName: '', newUserPassword: '', newUserRole: 'user',
    creatingUser: false,
    setProfileEditingField: mockVoid, setProfileFieldDraft: mockVoid,
    setNewUserName: mockVoid, setNewUserPassword: mockVoid,
    setNewUserRole: mockVoid,
    handleStartEditField: mockVoid, handleSaveField: mockVoid,
    handleCancelEditField: mockVoid, handleUploadProfileAvatar: mockVoid,
    handleCreateUser: mockVoid, handleChangeUserRole: mockVoid,
    handleDeleteUser: mockVoid, loadTokenUsage: mockVoid,
  };

  const mockWorkflowState = {
    loading: false, error: null, workflows: [],
    loadWorkflows: mockVoid, deleteWorkflow: mockVoid,
    setDefault: mockVoid, toggleActive: mockVoid,
  };

  return { createMockStore, mockAuthState, mockAdminState, mockAgentState, mockMemoryStoreState, mockUserMgmtState, mockWorkflowState };
});

// ── vi.mock calls (hoisted above imports) ──────────────────────────────

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  useSearchParams: () => ({ get: vi.fn(() => null), has: vi.fn(() => false), toString: vi.fn(() => '') }),
  usePathname: () => '/admin',
  useParams: () => ({}),
}));

vi.mock('next/dynamic', () => ({
  default: (_importFn: () => Promise<unknown>, _opts?: unknown) => {
    // Always return a simple placeholder — the real modules are too heavy for smoke tests.
    const Placeholder = () => React.createElement('div', { 'data-testid': 'dynamic-module' }, 'Module loaded');
    Placeholder.displayName = 'DynamicMock';
    return Placeholder;
  },
}));

vi.mock('../../stores/authStore', () => ({
  useAuthStore: createMockStore(mockAuthState),
}));

vi.mock('../../stores/adminStore', () => ({
  useAdminStore: createMockStore(mockAdminState),
  SETTINGS_MENU: ['服务商', '记忆', '技能', '通用', '审计日志', '用户管理', 'IM 接入', 'MCP', '工作流', '知识库', '模板市场', '工具市场', '工作空间', '上下文引擎', 'AgentNet', 'Agent 身份', 'Docker 沙箱', '多模态工作区', '集中日志', '模块连线', 'RAG 检索', '检索评估', 'A2A 互操作', 'A/B 测试', '成本分析', 'SLO 仪表板', '离线评估'],
}));

vi.mock('../../stores/agentStore', () => ({
  useAgentStore: createMockStore(mockAgentState),
}));

vi.mock('../../stores/memoryStore', () => ({
  useMemoryStore: createMockStore(mockMemoryStoreState),
}));

vi.mock('../../stores/userManagementStore', () => ({
  useUserManagementStore: createMockStore(mockUserMgmtState),
}));

vi.mock('../../stores/workflowStore', () => ({
  useWorkflowStore: createMockStore(mockWorkflowState),
}));

// Import page AFTER all mocks
import AdminPage from '../../app/admin/page';

describe('AdminPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it('renders without crashing', async () => {
    await act(async () => {
      render(<AdminPage />);
    });
    expect(screen.getByTestId('dynamic-module')).toBeInTheDocument();
  });

  it('does NOT show notice when notice is empty', async () => {
    await act(async () => {
      render(<AdminPage />);
    });
    expect(screen.queryByText(/warning/i)).toBeNull();
  });
});
