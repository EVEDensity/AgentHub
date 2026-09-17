import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import React from 'react';

const {
  createMockStore,
  mockAuthState,
  mockAdminState,
  mockAgentState,
  mockMemoryStoreState,
  mockUserMgmtState,
  mockWorkflowState,
} = vi.hoisted(() => {
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
    fmtErr: vi.fn((detail: unknown, fallback: string) => (typeof detail === 'string' ? detail : fallback)),
  };

  const mockAdminState = {
    activeMenu: '\u670d\u52a1\u5546',
    notice: '',
    setActiveMenu: mockVoid,
    setNotice: mockVoid,
  };

  const emptyAgent = {
    agentId: '',
    domain: '',
    adapterType: 'deepseek',
    baseModelName: '',
    rankLevel: 'L1',
    dutyNote: '',
    displayName: '',
    avatarUrl: '',
    capabilityTags: [],
    baseUrl: '',
    apiKey: '',
    systemPrompt: '',
    userPrompt: '',
    assistantPrompt: '',
    promptVariables: {},
    publicConfig: {
      enabled: false,
      welcomeMessage: '',
      placeholder: '',
      themeColor: '#6366f1',
      logoUrl: '',
      suggestedQuestions: [],
    },
  };

  const mockAgentState = {
    agents: [],
    agentTests: {},
    adapterOptions: [],
    selectedAdapterInfo: null,
    editSelectedAdapterInfo: null,
    defaultChatAgent: 'Orchestrator',
    isCreatingAgent: false,
    showLocalAgentModal: false,
    editingAgentId: null,
    newAgent: emptyAgent,
    editAgent: emptyAgent,
    fetchAdapters: mockVoid,
    refresh: mockVoid,
    createAgent: mockVoid,
    testAgent: mockVoid,
    removeAgent: mockVoid,
    startEditAgent: mockVoid,
    cancelEditAgent: mockVoid,
    saveAgentEdit: mockVoid,
    handleSetDefaultChatAgent: mockVoid,
    handleAdapterChange: mockVoid,
    setNewAgent: mockVoid,
    setEditAgent: mockVoid,
    setSelectedAdapterInfo: mockVoid,
    setEditSelectedAdapterInfo: mockVoid,
    setIsCreatingAgent: mockVoid,
    setShowLocalAgentModal: mockVoid,
    setEditingAgentId: mockVoid,
  };

  const mockMemoryStoreState = {
    init: mockVoid,
    loadMemoryFiles: mockVoid,
    loadMemoryDetail: mockVoid,
    saveMemoryDetail: mockVoid,
    setMemoryKeyword: mockVoid,
    setActiveMemoryFile: mockVoid,
    setMemoryBodyDraft: mockVoid,
    setMemoryDirty: mockVoid,
    setMemoryPreview: mockVoid,
    setMemorySubTab: mockVoid,
    setShowTrash: mockVoid,
    setShowDeleteConfirm: mockVoid,
    setPendingDeleteFile: mockVoid,
    setConsolidationDryRun: mockVoid,
    setMemorySearchQuery: mockVoid,
    setMemorySearchResults: mockVoid,
    handleExportMemory: mockVoid,
    handleImportMemory: mockVoid,
    confirmDeleteMemory: mockVoid,
    handleDeleteMemory: mockVoid,
    loadTrash: mockVoid,
    handleRecoverFromTrash: mockVoid,
    handlePurgeFromTrash: mockVoid,
    loadSessionSummaries: mockVoid,
    loadSessionDetail: mockVoid,
    loadGlobalSummary: mockVoid,
    refreshGlobalSummary: mockVoid,
    runConsolidation: mockVoid,
    runMemorySearch: mockVoid,
    getFilteredMemoryFiles: vi.fn(() => []),
    loadSessionMemoryList: mockVoid,
    loadSessionMemoryConversation: mockVoid,
    consolidateSessionMemory: mockVoid,
    createMemorySession: mockVoid,
    updateSessionTopic: mockVoid,
    memoryLoading: false,
    memoryError: null,
    memoryKeyword: '',
    memoryFiles: [],
    activeMemoryFile: null,
    memoryDetail: null,
    memoryBodyDraft: '',
    memoryDirty: false,
    memoryPreview: null,
    memorySubTab: 'files',
    sessionList: [],
    sessionsLoading: false,
    activeSessionId: null,
    activeSessionSummary: null,
    globalSummary: null,
    globalSummaryLoading: false,
    consolidationLoading: false,
    consolidationResult: null,
    consolidationError: null,
    consolidationDryRun: false,
    memorySearchQuery: '',
    memorySearchResults: null,
    memorySearchLoading: false,
    showTrash: false,
    trashItems: [],
    trashLoading: false,
    showDeleteConfirm: false,
    pendingDeleteFile: null,
    sessionMemoryList: [],
    sessionMemoryLoading: false,
    activeSessionMemoryId: null,
    sessionMemoryConversation: [],
    sessionMemoryConversationLoading: false,
  };

  const mockUserMgmtState = {
    tokenData: null,
    tokenLoading: false,
    tokenError: '',
    profileBio: '',
    profileEditingField: null,
    profileFieldDraft: '',
    profileLocation: '',
    profileEmail: '',
    profileOrg: '',
    profileAvatarUrl: '',
    profileUploading: false,
    userList: [],
    userListLoading: false,
    userListError: '',
    newUserName: '',
    newUserPassword: '',
    newUserRole: 'developer',
    creatingUser: false,
    setProfileEditingField: mockVoid,
    setProfileFieldDraft: mockVoid,
    setNewUserName: mockVoid,
    setNewUserPassword: mockVoid,
    setNewUserRole: mockVoid,
    handleStartEditField: mockVoid,
    handleSaveField: mockVoid,
    handleCancelEditField: mockVoid,
    handleUploadProfileAvatar: mockVoid,
    handleCreateUser: mockVoid,
    handleChangeUserRole: mockVoid,
    handleDeleteUser: mockVoid,
    loadTokenUsage: mockVoid,
    init: mockVoid,
  };

  const mockWorkflowState = {
    loading: false,
    error: null,
    workflows: [],
    loadWorkflows: mockVoid,
    deleteWorkflow: mockVoid,
    setDefault: mockVoid,
    toggleActive: mockVoid,
  };

  return {
    createMockStore,
    mockAuthState,
    mockAdminState,
    mockAgentState,
    mockMemoryStoreState,
    mockUserMgmtState,
    mockWorkflowState,
  };
});

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  useSearchParams: () => ({ get: vi.fn(() => null), has: vi.fn(() => false), toString: vi.fn(() => '') }),
  usePathname: () => '/admin',
  useParams: () => ({}),
}));

vi.mock('next/dynamic', () => ({
  default: () => {
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
  SETTINGS_MENU: [
    '\u670d\u52a1\u5546',
    '\u5de5\u4f5c\u6d41',
    '\u6743\u9650',
    '\u51b3\u7b56\u6536\u4ef6\u7bb1',
    '\u901a\u7528',
    '\u8bb0\u5fc6',
    '\u7528\u6237\u7ba1\u7406',
  ],
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

import AdminPage from '../../app/admin/page';

describe('AdminPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    mockAdminState.activeMenu = '\u670d\u52a1\u5546';
    mockAdminState.notice = '';
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

  it('routes the permissions menu to a real module', async () => {
    mockAdminState.activeMenu = '\u6743\u9650';

    await act(async () => {
      render(<AdminPage />);
    });

    expect(screen.getByTestId('dynamic-module')).toBeInTheDocument();
    expect(screen.queryByText(/\u7b49\u5f85\u914d\u7f6e\u9879\u63a5\u5165/)).toBeNull();
  });

  it('routes the decision inbox menu to a real module', async () => {
    mockAdminState.activeMenu = '\u51b3\u7b56\u6536\u4ef6\u7bb1';

    await act(async () => {
      render(<AdminPage />);
    });

    expect(screen.getByTestId('dynamic-module')).toBeInTheDocument();
    expect(screen.queryByText(/\u7b49\u5f85\u914d\u7f6e\u9879\u63a5\u5165/)).toBeNull();
  });
});
