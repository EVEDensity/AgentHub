'use client';

import { useEffect, type JSX } from 'react';
import dynamic from 'next/dynamic';
import { useAuthStore } from '../../stores/authStore';
import { useAdminStore, SETTINGS_MENU, type MenuItem } from '../../stores/adminStore';
import { useAgentStore } from '../../stores/agentStore';
import { useMemoryStore } from '../../stores/memoryStore';
import { useUserManagementStore } from '../../stores/userManagementStore';
import { useWorkflowStore } from '../../stores/workflowStore';

// ── Dynamic imports for admin modules (code-split by menu) ──
const AuditLogList = dynamic(() => import('../../components/admin/AuditLogList'), {
  ssr: false, loading: () => null,
});
const MCPLayout = dynamic(() => import('../../components/admin/MCPDashboard/MCPLayout'), {
  ssr: false, loading: () => null,
});
const GeneralSettingsModule = dynamic(() => import('../../components/admin/GeneralSettingsModule'), {
  ssr: false, loading: () => null,
});
const SkillsModule = dynamic(() => import('../../components/admin/SkillsModule'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const MemoryModule = dynamic(() => import('../../components/admin/MemoryModule'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const UserManagementModule = dynamic(() => import('../../components/admin/UserManagementModule'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const PermissionModule = dynamic(() => import('../../components/admin/PermissionModule'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const DecisionInbox = dynamic(() => import('../../components/admin/DecisionInbox'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const MissionControlPanel = dynamic(() => import('../../components/admin/MissionControlPanel'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12"><div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" /></div>
  ),
});
const ServiceProviderModule = dynamic(() => import('../../components/admin/ServiceProviderModule'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const WorkflowModule = dynamic(() => import('../../components/admin/WorkflowModule'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});

const KnowledgeBaseModule = dynamic(() => import('../../components/admin/KnowledgeBaseModule'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const TemplateMarketplace = dynamic(() => import('../../components/admin/TemplateMarketplace'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const WorkspaceManager = dynamic(() => import('../../components/admin/WorkspaceManager'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const ToolMarketplace = dynamic(() => import('../../components/admin/ToolMarketplace'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const ChannelModule = dynamic(() => import('../../components/admin/ChannelModule'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const MemoryDashboard = dynamic(() => import('../../components/admin/MemoryDashboard'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const AgentNetTopology = dynamic(() => import('../../components/admin/AgentNetTopology'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const AgentIdentityCard = dynamic(() => import('../../components/admin/AgentIdentityCard'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const AgentSandboxPanel = dynamic(() => import('../../components/admin/AgentSandboxPanel'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const AgentWorkspace = dynamic(() => import('../../components/admin/AgentWorkspace'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const LogsViewer = dynamic(() => import('../../components/admin/LogsViewer'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const ModuleRelationshipGraph = dynamic(() => import('../../components/admin/ModuleRelationshipGraph'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const RAGDocViewer = dynamic(() => import('../../components/admin/RAGDocViewer'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const RetrievalEvalPanel = dynamic(() => import('../../components/admin/RetrievalEvalPanel'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const A2AAgentManager = dynamic(() => import('../../components/admin/A2AAgentManager'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});
const ABTestManager = dynamic(() => import('../../components/admin/ABTestManager'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});

const CostAnalytics = dynamic(() => import('../../components/admin/CostAnalytics'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});

const SloDashboard = dynamic(() => import('../../components/admin/SloDashboard'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});

const EvalDashboard = dynamic(() => import('../../components/admin/EvalDashboard'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});

const Terminal = dynamic(() => import('../../components/admin/Terminal'), {
  ssr: false, loading: () => (
    <div className="flex justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
    </div>
  ),
});

export default function AdminPage(): JSX.Element {
  // ── Read from stores ────────────────────────────────────────────
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const setToken = useAuthStore((s) => s.setToken);
  const authHeaders = useAuthStore((s) => s.authHeaders);
  const fmtErr = useAuthStore((s) => s.fmtErr);

  const activeMenu = useAdminStore((s) => s.activeMenu);
  const notice = useAdminStore((s) => s.notice);
  const setNotice = useAdminStore((s) => s.setNotice);

  // Agent store
  const agents = useAgentStore((s) => s.agents);
  const agentTests = useAgentStore((s) => s.agentTests);
  const adapterOptions = useAgentStore((s) => s.adapterOptions);
  const selectedAdapterInfo = useAgentStore((s) => s.selectedAdapterInfo);
  const editSelectedAdapterInfo = useAgentStore((s) => s.editSelectedAdapterInfo);
  const isCreatingAgent = useAgentStore((s) => s.isCreatingAgent);
  const showLocalAgentModal = useAgentStore((s) => s.showLocalAgentModal);
  const editingAgentId = useAgentStore((s) => s.editingAgentId);
  const defaultChatAgent = useAgentStore((s) => s.defaultChatAgent);
  const newAgent = useAgentStore((s) => s.newAgent);
  const editAgent = useAgentStore((s) => s.editAgent);
  const fetchAdapters = useAgentStore((s) => s.fetchAdapters);
  const refresh = useAgentStore((s) => s.refresh);
  const createAgent = useAgentStore((s) => s.createAgent);
  const testAgent = useAgentStore((s) => s.testAgent);
  const removeAgent = useAgentStore((s) => s.removeAgent);
  const startEditAgent = useAgentStore((s) => s.startEditAgent);
  const cancelEditAgent = useAgentStore((s) => s.cancelEditAgent);
  const saveAgentEdit = useAgentStore((s) => s.saveAgentEdit);
  const handleSetDefaultChatAgent = useAgentStore((s) => s.handleSetDefaultChatAgent);
  const handleAdapterChange = useAgentStore((s) => s.handleAdapterChange);
  const setNewAgent = useAgentStore((s) => s.setNewAgent);
  const setEditAgent = useAgentStore((s) => s.setEditAgent);
  const setSelectedAdapterInfo = useAgentStore((s) => s.setSelectedAdapterInfo);
  const setEditSelectedAdapterInfo = useAgentStore((s) => s.setEditSelectedAdapterInfo);
  const setIsCreatingAgent = useAgentStore((s) => s.setIsCreatingAgent);
  const setShowLocalAgentModal = useAgentStore((s) => s.setShowLocalAgentModal);
  const setEditingAgentId = useAgentStore((s) => s.setEditingAgentId);

  // Memory store
  const memoryLoading = useMemoryStore((s) => s.memoryLoading);
  const memoryError = useMemoryStore((s) => s.memoryError);
  const memoryKeyword = useMemoryStore((s) => s.memoryKeyword);
  const memoryFiles = useMemoryStore((s) => s.memoryFiles);
  const activeMemoryFile = useMemoryStore((s) => s.activeMemoryFile);
  const memoryDetail = useMemoryStore((s) => s.memoryDetail);
  const memoryBodyDraft = useMemoryStore((s) => s.memoryBodyDraft);
  const memoryDirty = useMemoryStore((s) => s.memoryDirty);
  const memoryPreview = useMemoryStore((s) => s.memoryPreview);
  const memorySubTab = useMemoryStore((s) => s.memorySubTab);
  const sessionList = useMemoryStore((s) => s.sessionList);
  const sessionsLoading = useMemoryStore((s) => s.sessionsLoading);
  const activeSessionId = useMemoryStore((s) => s.activeSessionId);
  const activeSessionSummary = useMemoryStore((s) => s.activeSessionSummary);
  const globalSummary = useMemoryStore((s) => s.globalSummary);
  const globalSummaryLoading = useMemoryStore((s) => s.globalSummaryLoading);
  const consolidationLoading = useMemoryStore((s) => s.consolidationLoading);
  const consolidationResult = useMemoryStore((s) => s.consolidationResult);
  const consolidationError = useMemoryStore((s) => s.consolidationError);
  const consolidationDryRun = useMemoryStore((s) => s.consolidationDryRun);
  const memorySearchQuery = useMemoryStore((s) => s.memorySearchQuery);
  const memorySearchResults = useMemoryStore((s) => s.memorySearchResults);
  const memorySearchLoading = useMemoryStore((s) => s.memorySearchLoading);
  const showTrash = useMemoryStore((s) => s.showTrash);
  const trashItems = useMemoryStore((s) => s.trashItems);
  const trashLoading = useMemoryStore((s) => s.trashLoading);
  const showDeleteConfirm = useMemoryStore((s) => s.showDeleteConfirm);
  const pendingDeleteFile = useMemoryStore((s) => s.pendingDeleteFile);
  const getFilteredMemoryFiles = useMemoryStore((s) => s.getFilteredMemoryFiles);
  const setMemoryKeyword = useMemoryStore((s) => s.setMemoryKeyword);
  const setActiveMemoryFile = useMemoryStore((s) => s.setActiveMemoryFile);
  const setMemoryBodyDraft = useMemoryStore((s) => s.setMemoryBodyDraft);
  const setMemoryDirty = useMemoryStore((s) => s.setMemoryDirty);
  const setMemoryPreview = useMemoryStore((s) => s.setMemoryPreview);
  const setMemorySubTab = useMemoryStore((s) => s.setMemorySubTab);
  const setShowTrash = useMemoryStore((s) => s.setShowTrash);
  const setShowDeleteConfirm = useMemoryStore((s) => s.setShowDeleteConfirm);
  const setPendingDeleteFile = useMemoryStore((s) => s.setPendingDeleteFile);
  const setConsolidationDryRun = useMemoryStore((s) => s.setConsolidationDryRun);
  const setMemorySearchQuery = useMemoryStore((s) => s.setMemorySearchQuery);
  const setMemorySearchResults = useMemoryStore((s) => s.setMemorySearchResults);
  const loadMemoryFiles = useMemoryStore((s) => s.loadMemoryFiles);
  const loadMemoryDetail = useMemoryStore((s) => s.loadMemoryDetail);
  const saveMemoryDetail = useMemoryStore((s) => s.saveMemoryDetail);
  const handleExportMemory = useMemoryStore((s) => s.handleExportMemory);
  const handleImportMemory = useMemoryStore((s) => s.handleImportMemory);
  const confirmDeleteMemory = useMemoryStore((s) => s.confirmDeleteMemory);
  const handleDeleteMemory = useMemoryStore((s) => s.handleDeleteMemory);
  const loadTrash = useMemoryStore((s) => s.loadTrash);
  const handleRecoverFromTrash = useMemoryStore((s) => s.handleRecoverFromTrash);
  const handlePurgeFromTrash = useMemoryStore((s) => s.handlePurgeFromTrash);
  const loadSessionSummaries = useMemoryStore((s) => s.loadSessionSummaries);
  const loadSessionDetail = useMemoryStore((s) => s.loadSessionDetail);
  const loadGlobalSummary = useMemoryStore((s) => s.loadGlobalSummary);
  const refreshGlobalSummary = useMemoryStore((s) => s.refreshGlobalSummary);
  const runConsolidation = useMemoryStore((s) => s.runConsolidation);
  const runMemorySearch = useMemoryStore((s) => s.runMemorySearch);
  const memoryInit = useMemoryStore((s) => s.init);
  // Session Memory Store
  const sessionMemoryList = useMemoryStore((s) => s.sessionMemoryList);
  const sessionMemoryLoading = useMemoryStore((s) => s.sessionMemoryLoading);
  const activeSessionMemoryId = useMemoryStore((s) => s.activeSessionMemoryId);
  const sessionMemoryConversation = useMemoryStore((s) => s.sessionMemoryConversation);
  const sessionMemoryConversationLoading = useMemoryStore((s) => s.sessionMemoryConversationLoading);
  const loadSessionMemoryList = useMemoryStore((s) => s.loadSessionMemoryList);
  const loadSessionMemoryConversation = useMemoryStore((s) => s.loadSessionMemoryConversation);
  const consolidateSessionMemory = useMemoryStore((s) => s.consolidateSessionMemory);
  const createMemorySession = useMemoryStore((s) => s.createMemorySession);
  const updateSessionTopic = useMemoryStore((s) => s.updateSessionTopic);

  // User management store
  const tokenData = useUserManagementStore((s) => s.tokenData);
  const tokenLoading = useUserManagementStore((s) => s.tokenLoading);
  const tokenError = useUserManagementStore((s) => s.tokenError);
  const profileBio = useUserManagementStore((s) => s.profileBio);
  const profileEditingField = useUserManagementStore((s) => s.profileEditingField);
  const profileFieldDraft = useUserManagementStore((s) => s.profileFieldDraft);
  const profileLocation = useUserManagementStore((s) => s.profileLocation);
  const profileEmail = useUserManagementStore((s) => s.profileEmail);
  const profileOrg = useUserManagementStore((s) => s.profileOrg);
  const profileAvatarUrl = useUserManagementStore((s) => s.profileAvatarUrl);
  const profileUploading = useUserManagementStore((s) => s.profileUploading);
  const userList = useUserManagementStore((s) => s.userList);
  const userListLoading = useUserManagementStore((s) => s.userListLoading);
  const userListError = useUserManagementStore((s) => s.userListError);
  const newUserName = useUserManagementStore((s) => s.newUserName);
  const newUserPassword = useUserManagementStore((s) => s.newUserPassword);
  const newUserRole = useUserManagementStore((s) => s.newUserRole);
  const creatingUser = useUserManagementStore((s) => s.creatingUser);
  const setProfileEditingField = useUserManagementStore((s) => s.setProfileEditingField);
  const setProfileFieldDraft = useUserManagementStore((s) => s.setProfileFieldDraft);
  const setNewUserName = useUserManagementStore((s) => s.setNewUserName);
  const setNewUserPassword = useUserManagementStore((s) => s.setNewUserPassword);
  const setNewUserRole = useUserManagementStore((s) => s.setNewUserRole);
  const handleStartEditField = useUserManagementStore((s) => s.handleStartEditField);
  const handleSaveField = useUserManagementStore((s) => s.handleSaveField);
  const handleCancelEditField = useUserManagementStore((s) => s.handleCancelEditField);
  const handleUploadProfileAvatar = useUserManagementStore((s) => s.handleUploadProfileAvatar);
  const handleCreateUser = useUserManagementStore((s) => s.handleCreateUser);
  const handleChangeUserRole = useUserManagementStore((s) => s.handleChangeUserRole);
  const handleDeleteUser = useUserManagementStore((s) => s.handleDeleteUser);
  const loadTokenUsage = useUserManagementStore((s) => s.loadTokenUsage);
  const userManagementInit = useUserManagementStore((s) => s.init);

  // Workflow store
  const wfLoading = useWorkflowStore((s) => s.loading);
  const wfError = useWorkflowStore((s) => s.error);
  const workflows = useWorkflowStore((s) => s.workflows);
  const wfLoadWorkflows = useWorkflowStore((s) => s.loadWorkflows);
  const wfDeleteWorkflow = useWorkflowStore((s) => s.deleteWorkflow);
  const wfSetDefault = useWorkflowStore((s) => s.setDefault);
  const wfToggleActive = useWorkflowStore((s) => s.toggleActive);

  const setActiveMenu = useAdminStore((s) => s.setActiveMenu);

  // ── Initialization ──────────────────────────────────────────────
  useEffect(() => {
    // Restore user from localStorage
    const u = typeof window !== 'undefined' ? localStorage.getItem('agenthub_user') : null;
    if (u) {
      try { setUser(JSON.parse(u)); } catch { /* ignore */ }
    }
    const t = typeof window !== 'undefined' ? localStorage.getItem('agenthub_token') : null;
    if (t) setToken(t);

    // Sync activeMenu from URL query param
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      const menu = params.get('menu');
      if (menu && (SETTINGS_MENU as readonly string[]).includes(menu)) {
        setActiveMenu(menu as MenuItem);
      }
    }

    // Fetch initial data
    void refresh();
    void fetchAdapters();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Load data when menu changes
  useEffect(() => {
    if (activeMenu === '记忆') {
      void memoryInit();
    }
    if (activeMenu === '用户管理') {
      void userManagementInit();
    }
  }, [activeMenu]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Module renderer ─────────────────────────────────────────────
  function renderModuleContent(): JSX.Element {
    switch (activeMenu) {
      case 'Mission Control':
        return <MissionControlPanel authHeaders={authHeaders} setNotice={setNotice} fmtErr={fmtErr} />;
      case '服务商':
        return (
          <ServiceProviderModule
            agents={agents} agentTests={agentTests} adapterOptions={adapterOptions}
            selectedAdapterInfo={selectedAdapterInfo} editSelectedAdapterInfo={editSelectedAdapterInfo}
            isCreatingAgent={isCreatingAgent} showLocalAgentModal={showLocalAgentModal}
            editingAgentId={editingAgentId} newAgent={newAgent} editAgent={editAgent}
            defaultChatAgent={defaultChatAgent}
            setNewAgent={setNewAgent} setEditAgent={setEditAgent}
            setSelectedAdapterInfo={setSelectedAdapterInfo} setEditSelectedAdapterInfo={setEditSelectedAdapterInfo}
            setIsCreatingAgent={setIsCreatingAgent} setShowLocalAgentModal={setShowLocalAgentModal}
            setEditingAgentId={setEditingAgentId}
            handleAdapterChange={handleAdapterChange} createAgent={createAgent}
            testAgent={testAgent} removeAgent={removeAgent}
            startEditAgent={startEditAgent} cancelEditAgent={cancelEditAgent}
            saveAgentEdit={saveAgentEdit} handleSetDefaultChatAgent={handleSetDefaultChatAgent}
            refresh={refresh} authHeaders={authHeaders} setNotice={setNotice} fmtErr={fmtErr}
          />
        );
      case '记忆':
        return (
          <MemoryModule
            memoryLoading={memoryLoading} memoryError={memoryError} memoryKeyword={memoryKeyword}
            memoryFiles={memoryFiles} activeMemoryFile={activeMemoryFile}
            memoryDetail={memoryDetail} memoryBodyDraft={memoryBodyDraft}
            memoryDirty={memoryDirty} memoryPreview={memoryPreview}
            memorySubTab={memorySubTab} sessionList={sessionList}
            sessionsLoading={sessionsLoading} activeSessionId={activeSessionId}
            activeSessionSummary={activeSessionSummary} globalSummary={globalSummary}
            globalSummaryLoading={globalSummaryLoading}
            consolidationLoading={consolidationLoading} consolidationResult={consolidationResult}
            consolidationError={consolidationError} consolidationDryRun={consolidationDryRun}
            memorySearchQuery={memorySearchQuery} memorySearchResults={memorySearchResults}
            memorySearchLoading={memorySearchLoading}
            showTrash={showTrash} trashItems={trashItems}
            trashLoading={trashLoading} showDeleteConfirm={showDeleteConfirm}
            pendingDeleteFile={pendingDeleteFile}
            filteredMemoryFiles={getFilteredMemoryFiles()}
            setMemoryKeyword={setMemoryKeyword} setMemorySearchQuery={setMemorySearchQuery}
            setMemorySearchResults={setMemorySearchResults}
            setActiveMemoryFile={setActiveMemoryFile} setMemoryBodyDraft={setMemoryBodyDraft}
            setMemoryDirty={setMemoryDirty} setMemoryPreview={setMemoryPreview}
            setMemorySubTab={setMemorySubTab} setShowTrash={setShowTrash}
            setShowDeleteConfirm={setShowDeleteConfirm} setPendingDeleteFile={setPendingDeleteFile}
            setConsolidationDryRun={setConsolidationDryRun}
            loadMemoryFiles={loadMemoryFiles} loadMemoryDetail={loadMemoryDetail}
            saveMemoryDetail={saveMemoryDetail} handleExportMemory={handleExportMemory}
            handleImportMemory={handleImportMemory}
            confirmDeleteMemory={confirmDeleteMemory} handleDeleteMemory={handleDeleteMemory}
            loadTrash={loadTrash} handleRecoverFromTrash={handleRecoverFromTrash}
            handlePurgeFromTrash={handlePurgeFromTrash}
            loadSessionSummaries={loadSessionSummaries} loadSessionDetail={loadSessionDetail}
            loadGlobalSummary={loadGlobalSummary} refreshGlobalSummary={refreshGlobalSummary}
            runConsolidation={runConsolidation} runMemorySearch={runMemorySearch}
            sessionMemoryList={sessionMemoryList} sessionMemoryLoading={sessionMemoryLoading}
            activeSessionMemoryId={activeSessionMemoryId}
            sessionMemoryConversation={sessionMemoryConversation}
            sessionMemoryConversationLoading={sessionMemoryConversationLoading}
            loadSessionMemoryList={loadSessionMemoryList}
            loadSessionMemoryConversation={loadSessionMemoryConversation}
            consolidateSessionMemory={consolidateSessionMemory}
            createMemorySession={createMemorySession}
            updateSessionTopic={updateSessionTopic}
            authHeaders={authHeaders} setNotice={setNotice}
          />
        );
      case '技能':
        return <SkillsModule authHeaders={authHeaders} setNotice={setNotice} />;
      case '权限':
        return <PermissionModule authHeaders={authHeaders} setNotice={setNotice} fmtErr={fmtErr} />;
      case '决策收件箱':
        return <DecisionInbox authHeaders={authHeaders} setNotice={setNotice} fmtErr={fmtErr} />;
      case '通用':
        return <GeneralSettingsModule authHeaders={authHeaders} />;
      case '审计日志':
        return <AuditLogList authHeaders={authHeaders} />;
      case '用户管理':
        return (
          <UserManagementModule
            user={user} authHeaders={authHeaders} setNotice={setNotice} fmtErr={fmtErr}
            tokenData={tokenData} tokenLoading={tokenLoading} tokenError={tokenError}
            profileBio={profileBio} profileEditingField={profileEditingField}
            profileFieldDraft={profileFieldDraft} profileLocation={profileLocation}
            profileEmail={profileEmail} profileOrg={profileOrg}
            profileAvatarUrl={profileAvatarUrl} profileUploading={profileUploading}
            userList={userList} userListLoading={userListLoading} userListError={userListError}
            newUserName={newUserName} newUserPassword={newUserPassword}
            newUserRole={newUserRole} creatingUser={creatingUser}
            setProfileEditingField={setProfileEditingField}
            setProfileFieldDraft={setProfileFieldDraft}
            setNewUserName={setNewUserName} setNewUserPassword={setNewUserPassword}
            setNewUserRole={setNewUserRole}
            handleStartEditField={handleStartEditField} handleSaveField={handleSaveField}
            handleCancelEditField={handleCancelEditField}
            handleUploadProfileAvatar={handleUploadProfileAvatar}
            handleCreateUser={handleCreateUser} handleChangeUserRole={handleChangeUserRole}
            handleDeleteUser={handleDeleteUser} loadTokenUsage={loadTokenUsage}
          />
        );
      case 'IM 接入':
        return <ChannelModule authHeaders={authHeaders} setNotice={setNotice} />;
      case 'MCP':
        return <MCPLayout />;
      case '工作流':
        return (
          <WorkflowModule
            authHeaders={authHeaders}
            setNotice={setNotice}
          />
        );
      case '知识库':
        return <KnowledgeBaseModule authHeaders={authHeaders} setNotice={setNotice} />;
      case '模板市场':
        return <TemplateMarketplace authHeaders={authHeaders} setNotice={setNotice} />;
      case '工具市场':
        return <ToolMarketplace />;
      case '工作空间':
        return <WorkspaceManager authHeaders={authHeaders} setNotice={setNotice} />;
      case '上下文引擎':
        return <MemoryDashboard authHeaders={authHeaders} setNotice={setNotice} />;
      case 'AgentNet':
        return <AgentNetTopology authHeaders={authHeaders} setNotice={setNotice} />;
      case 'Agent 身份':
        return <AgentIdentityCard authHeaders={authHeaders} setNotice={setNotice} />;
      case 'Docker 沙箱':
        return <AgentSandboxPanel authHeaders={authHeaders} setNotice={setNotice} />;
      case '多模态工作区':
        return <AgentWorkspace authHeaders={authHeaders} setNotice={setNotice} />;
      case '集中日志':
        return <LogsViewer authHeaders={authHeaders} setNotice={setNotice} />;
      case '模块连线':
        return <ModuleRelationshipGraph />;
      case 'RAG 检索':
        return <RAGDocViewer authHeaders={authHeaders} setNotice={setNotice} />;
      case '检索评估':
        return <RetrievalEvalPanel authHeaders={authHeaders} setNotice={setNotice} />;
      case 'A2A 互操作':
        return <A2AAgentManager authHeaders={authHeaders} setNotice={setNotice} />;
      case 'A/B 测试':
        return <ABTestManager />;
      case '成本分析':
        return <CostAnalytics />;
      case 'SLO 仪表板':
        return <SloDashboard />;
      case '离线评估':
        return <EvalDashboard />;
      case '终端':
        return <Terminal />;
      default:
        return (
          <section className="card p-6">
            <h2 className="text-h3">{activeMenu}</h2>
            <p className="mt-3 text-sm text-warm-500">该模块已独立，等待配置项接入。</p>
          </section>
        );
    }
  }

  return (
    <>
      {notice && (
        <div className="rounded-lg bg-warning-50 border border-warning-200 px-4 py-3 text-sm text-warning-700 flex items-center justify-between gap-2">
          <span>{notice}</span>
          <button
            className="text-warning-500 hover:text-warning-700 shrink-0"
            onClick={() => setNotice('')}
          >
            <span className="material-symbols-outlined text-[16px]">close</span>
          </button>
        </div>
      )}
      {renderModuleContent()}
    </>
  );
}
