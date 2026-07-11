import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// ── Helpers ──────────────────────────────────────────────────────────────

async function getAgentStore() {
  vi.resetModules();
  // Pre-populate auth headers mock since agentStore imports authStore
  vi.doMock('../../stores/authStore', () => ({
    useAuthStore: {
      getState: () => ({
        authHeaders: () => ({ Authorization: 'Bearer test-token' }),
        fmtErr: (detail: unknown, fallback: string) =>
          typeof detail === 'string' ? detail : fallback,
      }),
    },
  }));
  vi.doMock('../../stores/adminStore', () => ({
    useAdminStore: {
      getState: () => ({ setNotice: vi.fn() }),
    },
  }));

  const mod = await import('../../stores/agentStore');
  return mod.useAgentStore;
}

// Agent object matching the shape the store expects
function makeAgent(overrides: Record<string, unknown> = {}) {
  return {
    agentId: 'test-bot',
    domain: 'chat',
    adapterType: 'deepseek',
    baseModelName: 'deepseek-chat',
    rankLevel: 'L1',
    dutyNote: '',
    displayName: 'Test Bot',
    avatarUrl: '',
    capabilityTags: [],
    baseUrl: 'https://api.deepseek.com',
    status: 'online',
    publicConfig: undefined,
    ...overrides,
  };
}

describe('agentStore', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  // ── Initial state ──────────────────────────────────────────────

  it('starts with empty agents array', async () => {
    const store = await getAgentStore();
    expect(store.getState().agents).toEqual([]);
  });

  it('starts with null editingAgentId', async () => {
    const store = await getAgentStore();
    expect(store.getState().editingAgentId).toBeNull();
  });

  it('has correct default publicConfig in newAgent form', async () => {
    const store = await getAgentStore();
    const pc = store.getState().newAgent.publicConfig;
    expect(pc).toEqual({
      enabled: false,
      welcomeMessage: '',
      placeholder: '',
      themeColor: '#6366f1',
      logoUrl: '',
      suggestedQuestions: [],
    });
  });

  it('has correct default publicConfig in editAgent form', async () => {
    const store = await getAgentStore();
    const pc = store.getState().editAgent.publicConfig;
    expect(pc.themeColor).toBe('#6366f1');
    expect(pc.enabled).toBe(false);
  });

  // ── startEditAgent ─────────────────────────────────────────────

  it('startEditAgent populates form fields from agent', async () => {
    const store = await getAgentStore();
    const agent = makeAgent({ agentId: 'my-bot', displayName: 'My Bot' });

    store.getState().startEditAgent(agent);
    const form = store.getState().editAgent;
    expect(form.agentId).toBe('my-bot');
    expect(form.displayName).toBe('My Bot');
    expect(form.domain).toBe('chat');
  });

  it('startEditAgent populates publicConfig from agent', async () => {
    const store = await getAgentStore();
    const agent = makeAgent({
      agentId: 'public-bot',
      publicConfig: {
        enabled: true,
        welcomeMessage: '欢迎使用！',
        placeholder: '请输入...',
        themeColor: '#ff0000',
        logoUrl: 'https://example.com/logo.png',
        suggestedQuestions: ['问题1', '问题2'],
      },
    });

    store.getState().startEditAgent(agent as any);
    const pc = store.getState().editAgent.publicConfig;
    expect(pc.enabled).toBe(true);
    expect(pc.welcomeMessage).toBe('欢迎使用！');
    expect(pc.placeholder).toBe('请输入...');
    expect(pc.themeColor).toBe('#ff0000');
    expect(pc.logoUrl).toBe('https://example.com/logo.png');
    expect(pc.suggestedQuestions).toEqual(['问题1', '问题2']);
  });

  it('startEditAgent uses empty defaults when agent has no publicConfig', async () => {
    const store = await getAgentStore();
    const agent = makeAgent({ agentId: 'bare-bot' });
    // No publicConfig field at all

    store.getState().startEditAgent(agent as any);
    const pc = store.getState().editAgent.publicConfig;
    expect(pc.enabled).toBe(false);
    expect(pc.welcomeMessage).toBe('');
    expect(pc.themeColor).toBe('#6366f1');
    expect(pc.suggestedQuestions).toEqual([]);
  });

  it('startEditAgent handles partial publicConfig gracefully', async () => {
    const store = await getAgentStore();
    const agent = makeAgent({
      agentId: 'partial-bot',
      publicConfig: { enabled: true, themeColor: '#00ff00' },
    });

    store.getState().startEditAgent(agent as any);
    const pc = store.getState().editAgent.publicConfig;
    expect(pc.enabled).toBe(true);
    expect(pc.themeColor).toBe('#00ff00');
    expect(pc.welcomeMessage).toBe(''); // fallback to default
  });

  it('startEditAgent sets the editingAgentId', async () => {
    const store = await getAgentStore();
    store.getState().startEditAgent(makeAgent());
    expect(store.getState().editingAgentId).toBe('test-bot');
  });

  it('startEditAgent handles agent with all empty fields gracefully', async () => {
    const store = await getAgentStore();
    const agent = makeAgent({
      agentId: 'empty-bot',
      domain: '',
      displayName: '',
      dutyNote: '',
      avatarUrl: '',
      baseUrl: '',
    });
    store.getState().startEditAgent(agent as any);
    expect(store.getState().editingAgentId).toBe('empty-bot');
    expect(store.getState().editAgent.agentId).toBe('empty-bot');
  });

  // ── cancelEditAgent ────────────────────────────────────────────

  it('cancelEditAgent resets form and clears editingAgentId', async () => {
    const store = await getAgentStore();
    // First start editing
    store.getState().startEditAgent(makeAgent({ displayName: 'Before' } as any));
    expect(store.getState().editingAgentId).toBe('test-bot');
    expect(store.getState().editAgent.displayName).toBe('Before');

    // Then cancel
    store.getState().cancelEditAgent();
    expect(store.getState().editingAgentId).toBeNull();
    // Should be back to defaults
    expect(store.getState().editAgent.publicConfig.enabled).toBe(false);
    expect(store.getState().editAgent.agentId).toBe('');
  });

  // ── handleAdapterChange ────────────────────────────────────────

  it('handleAdapterChange in create mode updates adapter info', async () => {
    const store = await getAgentStore();
    // Set up adapter options
    store.setState({
      adapterOptions: [
        {
          id: 'openai',
          name: 'OpenAI',
          description: 'OpenAI API',
          default_model: 'gpt-4',
          default_base_url: 'https://api.openai.com',
          requires_api_key: true,
          category: 'cloud',
        },
      ],
    });

    store.getState().handleAdapterChange('openai', 'create');
    const form = store.getState().newAgent;
    expect(form.adapterType).toBe('openai');
    expect(form.baseModelName).toBe('gpt-4');
    expect(form.baseUrl).toBe('https://api.openai.com');
  });

  it('handleAdapterChange in edit mode updates adapter info', async () => {
    const store = await getAgentStore();
    store.setState({
      adapterOptions: [
        {
          id: 'anthropic',
          name: 'Anthropic',
          description: 'Claude API',
          default_model: 'claude-3-opus',
          default_base_url: 'https://api.anthropic.com',
          requires_api_key: true,
          category: 'cloud',
        },
      ],
    });

    store.getState().handleAdapterChange('anthropic', 'edit');
    const form = store.getState().editAgent;
    expect(form.adapterType).toBe('anthropic');
    expect(form.baseModelName).toBe('claude-3-opus');
  });

  it('handleAdapterChange with unknown adapter keeps existing values', async () => {
    const store = await getAgentStore();
    const initialModel = store.getState().newAgent.baseModelName;
    store.getState().handleAdapterChange('nonexistent', 'create');
    expect(store.getState().newAgent.adapterType).toBe('nonexistent');
    // baseModelName stays unchanged since no matching adapter
    expect(store.getState().newAgent.baseModelName).toBe(initialModel);
  });

  // ── UI flag setters ─────────────────────────────────────────────

  it('setIsCreatingAgent toggles the isCreatingAgent flag', async () => {
    const store = await getAgentStore();
    expect(store.getState().isCreatingAgent).toBe(false);
    store.getState().setIsCreatingAgent(true);
    expect(store.getState().isCreatingAgent).toBe(true);
  });

  it('setShowLocalAgentModal toggles flag', async () => {
    const store = await getAgentStore();
    store.getState().setShowLocalAgentModal(true);
    expect(store.getState().showLocalAgentModal).toBe(true);
  });

  // ── setNewAgent / setEditAgent updaters ────────────────────────

  it('setNewAgent replaces the newAgent form', async () => {
    const store = await getAgentStore();
    store.getState().setNewAgent((prev) => ({
      ...prev,
      displayName: 'Updated',
    }));
    expect(store.getState().newAgent.displayName).toBe('Updated');
  });

  it('setEditAgent replaces the editAgent form', async () => {
    const store = await getAgentStore();
    store.getState().setEditAgent((prev) => ({
      ...prev,
      publicConfig: { ...prev.publicConfig, enabled: true },
    }));
    expect(store.getState().editAgent.publicConfig.enabled).toBe(true);
  });
});
