const elements = {
  workspaceState: document.querySelector('#workspace-state'), sidebarDot: document.querySelector('#sidebar-dot'), sidebarStatus: document.querySelector('#sidebar-status'),
  serviceList: document.querySelector('#service-list'), feedback: document.querySelector('#feedback'),
  taskInput: document.querySelector('#task-input'), startTask: document.querySelector('#start-task'), modelChip: document.querySelector('#model-chip'),
  stopRuntime: document.querySelector('#stop-runtime'), cancelMission: document.querySelector('#cancel-mission'),
  openConsole: document.querySelector('#open-console'), openConsoleNav: document.querySelector('#open-console-nav'),
  navHome: document.querySelector('#nav-home'), navHistory: document.querySelector('#nav-history'),
  homeView: document.querySelector('#home-view'), historyView: document.querySelector('#history-view'), historyList: document.querySelector('#history-list'), historyRefresh: document.querySelector('#history-refresh'),
  settingsView: document.querySelector('#settings-view'),
  settings: document.querySelector('#settings'), settingsNav: document.querySelector('#settings-nav'), settingsBack: document.querySelector('#settings-back'), mainNav: document.querySelector('#main-nav'),
  settingsItems: document.querySelectorAll('[data-settings-section]'), settingsPanels: document.querySelectorAll('[data-settings-panel]'), settingsTitle: document.querySelector('#settings-title'),
  adminRefresh: document.querySelector('#admin-refresh'), adminFeedback: document.querySelector('#admin-feedback'),
  modelForm: document.querySelector('#model-form'), modelProvider: document.querySelector('#model-provider'), modelName: document.querySelector('#model-name'), modelBaseUrl: document.querySelector('#model-base-url'), modelApiKey: document.querySelector('#model-api-key'),
  modelList: document.querySelector('#model-list'), agentList: document.querySelector('#agent-list'), mcpEndpointLabel: document.querySelector('#mcp-endpoint-label'),
  adminTabs: document.querySelectorAll('[data-admin-tab]'),
  dialog: document.querySelector('#configuration-dialog'), form: document.querySelector('#configuration-form'), closeConfiguration: document.querySelector('#close-configuration'), cancelConfiguration: document.querySelector('#cancel-configuration'), saveConfiguration: document.querySelector('#save-configuration'),
  missionControlEndpoint: document.querySelector('#mission-control-endpoint'), mcpEndpoint: document.querySelector('#mcp-endpoint'), artifactDirectory: document.querySelector('#artifact-directory'),
  missionControlToken: document.querySelector('#mission-control-token'), mcpToken: document.querySelector('#mcp-token'), secretStatus: document.querySelector('#secret-status'), configurationModelApiKey: document.querySelector('#configuration-model-api-key'),
  taskResult: document.querySelector('#task-result'), resultStatus: document.querySelector('#result-status'), resultSummary: document.querySelector('#result-summary'), resultItems: document.querySelector('#result-items'),
  adminFrame: document.querySelector('#admin-frame'),
};

function nativeInvoke(command, args) {
  const invoke = window.__TAURI__?.core?.invoke;
  if (invoke) return invoke(command, args);
  // Browser-preview fixtures: only reached outside the Tauri shell (design
  // preview in a plain browser). The packaged app always has __TAURI__, so
  // real commands never take these branches.
  if (command === 'service_status') return Promise.resolve([
    { name: 'mission-control', status: 'ready', detail: 'health endpoint is ready' },
    { name: 'gateway', status: 'ready', detail: 'health endpoint is ready' },
    { name: 'mcp-gateway', status: 'ready', detail: 'health endpoint is ready' },
    { name: 'frontend', status: 'ready', detail: 'health endpoint is ready' },
  ]);
  if (command === 'start_runtime') return Promise.resolve({});
  if (command === 'stop_runtime') return Promise.resolve({});
  if (command === 'save_configuration') return Promise.resolve({});
  if (command === 'set_configuration_secret') return Promise.resolve({});
  if (command === 'clear_configuration_secret') return Promise.resolve({});
  if (command === 'pin_stack' || command === 'clear_stack_pin') return Promise.resolve('预览环境不支持该操作');
  if (command === 'runtime_status') return Promise.resolve({ status: 'running', readiness: 'ready', detail: '本地运行时就绪。' });
  if (command === 'probe_control_plane') return Promise.resolve({ reachability: 'reachable', endpointConfigured: true, detail: '本地控制面已连接' });
  if (command === 'probe_mcp') return Promise.resolve({ reachability: 'reachable', endpointConfigured: true, detail: '本地 MCP 已连接' });
  if (command === 'configuration_status') return Promise.resolve({ readyForRuntime: true, artifactDirectoryConfigured: true, missionControlEndpointConfigured: true });
  if (command === 'configuration_details') return Promise.resolve({ missionControlEndpoint: null, mcpEndpoint: null, artifactDirectory: null, missionControlToken: 'missing', mcpToken: 'missing', modelApiKey: 'missing' });
  if (command === 'local_service_endpoint') return Promise.resolve('http://127.0.0.1:28000');
  if (command === 'frontend_endpoint') return Promise.resolve('http://127.0.0.1:28004/admin');
  if (command === 'stack_info') return Promise.resolve({ manifest: { schemaVersion: 1, version: '0.2.0', commit: 'preview', generatedAt: new Date().toISOString() }, source: 'bundled', persisted: [], pinned: null });
  return Promise.reject(new Error('桌面命令不可用'));
}

const serviceLabels = { 'mission-control': 'Mission Control', gateway: 'Gateway', 'mcp-gateway': 'MCP Gateway', frontend: '管理界面' };
const localMissionControl = { endpoint: 'http://127.0.0.1:28000', workspaceId: 'local-admin', token: null };
let activeMissionId = null;
let servicesReady = false;
let composerFocused = false;
let modelChipLoaded = false;

const statusClass = (status) => String(status || 'unknown').toLowerCase();
const statusText = (status) => ({ missing: '未打包', stopped: '已停止', starting: '启动中', ready: '已就绪', failed: '失败' }[statusClass(status)] || '未知');

function localizeDetail(detail) {
  const text = String(detail || '');
  if (/health endpoint is ready/.test(text)) return '已就绪';
  if (/health check pending/.test(text)) return '正在就绪检查…';
  const restarted = text.match(/automatically restarted \((\d)\/3\)/);
  if (restarted) return `已自动重启（${restarted[1]}/3）`;
  const exited = text.match(/service exited with (.+)/);
  if (exited) return `进程已退出（${exited[1]}）`;
  if (/restart limit reached/.test(text)) return '自动重启已达上限';
  if (/unable to start service/.test(text)) return '服务启动失败';
  if (/service resource is not bundled/.test(text)) return '服务未打包';
  if (/no free AgentHub port group/.test(text)) return '无可用本地端口组';
  return text;
}

function renderServices(services) {
  if (!Array.isArray(services) || services.length === 0) {
    elements.serviceList.replaceChildren();
    elements.serviceList.innerHTML = '<div class="service-row"><span class="service-dot stopped"></span><span class="service-name">本地服务</span><span class="service-detail">未返回服务状态</span></div>';
    return;
  }
  elements.serviceList.replaceChildren(...services.map((service) => {
    const row = document.createElement('div'); row.className = 'service-row';
    const detail = localizeDetail(service.detail);
    const detailText = detail && detail !== statusText(service.status) ? ` · ${detail}` : '';
    row.innerHTML = `<span class="service-dot ${statusClass(service.status)}"></span><span class="service-name">${serviceLabels[service.name] || service.name}</span><span class="service-detail">${statusText(service.status)}${detailText}</span>`;
    return row;
  }));
}

function renderWorkspaceState(services, controlPlane) {
  const states = Array.isArray(services) ? services.map((item) => statusClass(item.status)) : [];
  const readyCount = states.filter((state) => state === 'ready').length;
  const failed = states.some((state) => ['failed', 'missing'].includes(state));
  const allReady = states.length > 0 && states.every((state) => state === 'ready');
  const anyStarting = states.some((state) => state === 'starting');
  const allStopped = states.length > 0 && states.every((state) => ['stopped', 'missing'].includes(state));
  let label = '正在检查本地服务';
  let dotClass = 'starting';
  if (failed) { label = '本地服务异常'; dotClass = 'failed'; }
  else if (allReady) { label = '本地服务已就绪'; dotClass = 'ready'; }
  else if (anyStarting) { label = `正在启动本地服务 · ${readyCount}/${states.length}`; dotClass = 'starting'; }
  else if (allStopped) { label = '本地服务已停止'; dotClass = 'stopped'; }
  servicesReady = allReady;

  elements.workspaceState.innerHTML = `<span class="dot ${dotClass}"></span>${label}`;
  elements.sidebarDot.className = `dot ${dotClass}`;
  elements.sidebarStatus.textContent = label;
  if (elements.stopRuntime) elements.stopRuntime.hidden = !states.some((state) => ['ready', 'starting'].includes(state));
  elements.openConsoleNav.disabled = controlPlane?.endpointConfigured !== true;

  if (allReady) {
    elements.startTask.disabled = false;
    elements.startTask.innerHTML = '开始任务 <span aria-hidden="true">↗</span>';
    if (!composerFocused) { composerFocused = true; elements.taskInput.focus(); maybeLoadModelChip(); }
  } else {
    composerFocused = false;
    elements.startTask.disabled = true;
    elements.startTask.textContent = failed ? '服务不可用' : allStopped ? '开始任务' : '启动中…';
  }
}

async function maybeLoadModelChip() {
  if (modelChipLoaded) return;
  modelChipLoaded = true;
  try {
    const models = await localApi('/api/admin/models');
    const name = Array.isArray(models) && models[0]?.modelName;
    elements.modelChip.textContent = name ? `模型 ${name}` : '模型未配置';
  } catch { elements.modelChip.textContent = '模型未配置'; }
  elements.modelChip.hidden = false;
}

async function refresh() {
  try {
    await nativeInvoke('start_runtime');
    localMissionControl.endpoint = await nativeInvoke('local_service_endpoint');
    const [services, controlPlane, configStatus] = await Promise.all([
      nativeInvoke('service_status'), nativeInvoke('probe_control_plane'),
      nativeInvoke('configuration_status').catch(() => null),
    ]);
    renderServices(services); renderWorkspaceState(services, controlPlane);
    const mcp = (Array.isArray(services) ? services : []).find((service) => service.name === 'mcp-gateway');
    elements.mcpEndpointLabel.textContent = localizeDetail(mcp?.detail) || '本地服务状态读取中';
    let statusMessage = '';
    const boundPort = Number(new URL(localMissionControl.endpoint, 'http://127.0.0.1').port);
    if (boundPort && boundPort !== 28000) {
      statusMessage = `⚠ 当前实例绑定端口 ${boundPort}（非首选 28000）。捆绑管理后台的内置地址仍指向 28000，多实例并发时请在首个实例中使用管理后台。`;
    }
    if (configStatus && configStatus.readyForRuntime === false && !servicesReady) {
      const gaps = [
        !configStatus.artifactDirectoryConfigured && 'Artifact 目录未配置',
        !configStatus.missionControlEndpointConfigured && 'Mission Control 地址未配置',
      ].filter(Boolean);
      if (gaps.length) statusMessage = `运行时尚未就绪：${gaps.join(' · ')}（可在设置中完成配置）`;
    }
    elements.feedback.textContent = statusMessage;
  } catch (error) {
    elements.feedback.textContent = error instanceof Error ? error.message : '本地工作区启动失败';
    renderWorkspaceState([], { endpointConfigured: false });
  }
}

const missionStateClass = (status) => ['SUCCEEDED'].includes(status) ? 'ready' : ['FAILED', 'CANCELLED'].includes(status) ? 'failed' : 'starting';
const missionStateText = (status) => ({ SUCCEEDED: '已完成', FAILED: '失败', CANCELLED: '已取消', RUNNING: '运行中', PENDING: '排队中', CREATED: '已创建' }[status] || status || '未知');

async function loadHistory() {
  elements.historyList.innerHTML = '<p class="admin-muted">正在读取任务历史…</p>';
  try {
    const data = await localApi(`/api/v1/missions?workspace_id=${encodeURIComponent(localMissionControl.workspaceId)}&limit=30`);
    const missions = Array.isArray(data?.missions) ? data.missions : [];
    if (!missions.length) { elements.historyList.innerHTML = '<p class="admin-muted" style="padding:16px 4px">还没有任务记录。回到「新任务」创建第一个任务吧。</p>'; return; }
    elements.historyList.replaceChildren(...missions.map((mission) => {
      const row = document.createElement('div'); row.className = 'history-row';
      const time = (mission.updated_at || mission.created_at || '').toString().replace('T', ' ').slice(0, 16);
      row.innerHTML = `<span><strong>${mission.title || mission.objective || '未命名任务'}</strong><small>${time}</small></span><span class="history-state"><span class="dot ${missionStateClass(mission.status)}"></span>${missionStateText(mission.status)}</span>`;
      return row;
    }));
  } catch {
    elements.historyList.innerHTML = '<p class="admin-muted" style="padding:16px 4px">任务历史读取失败（本地服务未运行？）</p>';
  }
}

function switchView(view) {
  const showHistory = view === 'history';
  const showSettings = view === 'settings';
  elements.homeView.hidden = showSettings || showHistory;
  elements.historyView.hidden = !showHistory;
  elements.settingsView.hidden = !showSettings;
  elements.mainNav.hidden = showSettings;
  elements.settingsNav.hidden = !showSettings;
  elements.navHome.classList.toggle('active', !showSettings && !showHistory);
  elements.navHistory.classList.toggle('active', showHistory);
  if (showHistory) loadHistory();
  if (showSettings) selectSettingsSection('general');
}

function openAdminFrame() {
  nativeInvoke('frontend_endpoint').then((endpoint) => {
    elements.adminFrame.hidden = false;
    elements.adminFrame.src = `${endpoint}?menu=通用`;
  }).catch(() => {
    elements.adminFrame.hidden = true;
  });
}

function selectSettingsSection(section) {
  for (const item of elements.settingsItems) item.classList.toggle('active', item.dataset.settingsSection === section);
  for (const panel of elements.settingsPanels) panel.hidden = panel.dataset.settingsPanel !== section;
  const active = [...elements.settingsItems].find((item) => item.dataset.settingsSection === section);
  elements.settingsTitle.textContent = active?.textContent?.trim() || '设置';
  if (section === 'advanced') openAdminFrame();
  if (section === 'monitoring') loadMonitor();
  if (section === 'account') loadAccount();
}

function renderMcpProbe(probe) {
  if (!probe) return;
  const dot = document.querySelector('#mcp-status-dot');
  const dotState = probe.reachability === 'reachable' ? 'ready' : probe.reachability === 'not_configured' ? 'stopped' : 'failed';
  if (dot) dot.className = `service-dot ${dotState}`;
  if (probe.detail) elements.mcpEndpointLabel.textContent = localizeDetail(probe.detail);
}

async function loadAdmin() {
  elements.adminFeedback.textContent = '正在读取管理数据…';
  try {
    const [models, agents, mcpProbe] = await Promise.all([
      localApi('/api/admin/models'), localApi('/api/agent/registry'),
      nativeInvoke('probe_mcp').catch(() => null),
    ]);
    renderMcpProbe(mcpProbe);
    elements.modelList.replaceChildren(...(Array.isArray(models) && models.length ? models : [{ modelName: '暂无模型配置', provider: '', baseUrl: '' }]).map((model) => {
      const row = document.createElement('div'); row.className = 'admin-list-row';
      row.innerHTML = `<strong>${model.modelName || '未命名模型'}</strong><span>${model.provider || ''}${model.baseUrl ? ` · ${model.baseUrl}` : ''}</span>${model.id ? `<button class="text-button" type="button" data-model-test="${model.id}">测试连通</button>` : ''}`; return row;
    }));
    elements.agentList.replaceChildren(...(Array.isArray(agents) && agents.length ? agents : [{ agentId: '暂无 Agent 配置', adapterType: '', baseModelName: '' }]).map((agent) => {
      const row = document.createElement('div'); row.className = 'admin-list-row';
      row.innerHTML = `<strong>${agent.displayName || agent.agentId || '未命名 Agent'}</strong><span>${agent.adapterType || ''}${agent.baseModelName ? ` · ${agent.baseModelName}` : ''}</span>`; return row;
    }));
    elements.adminFeedback.textContent = '管理数据已更新';
  } catch (error) { elements.adminFeedback.textContent = error instanceof Error ? error.message : '管理数据读取失败'; }
}

async function saveModel(event) {
  event.preventDefault();
  try {
    await localApi('/api/admin/models', { method: 'POST', body: JSON.stringify({ provider: elements.modelProvider.value.trim(), modelName: elements.modelName.value.trim(), baseUrl: elements.modelBaseUrl.value.trim(), apiKey: elements.modelApiKey.value }) });
    elements.modelForm.reset(); modelChipLoaded = false; await loadAdmin(); elements.adminFeedback.textContent = '模型配置已保存';
  } catch (error) { elements.adminFeedback.textContent = error instanceof Error ? error.message : '模型配置保存失败'; }
}

async function openConsole() {
  try { switchView('settings'); selectSettingsSection('advanced'); }
  catch (error) { elements.feedback.textContent = error instanceof Error ? error.message : '工作区打开失败'; }
}

async function localApi(path, options = {}) {
  if (!localMissionControl.token) {
    const login = await fetch(`${localMissionControl.endpoint}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'admin', password: 'admin123' }) });
    if (!login.ok) throw new Error('本地 Mission Control 登录失败，请检查服务状态');
    localMissionControl.token = (await login.json()).accessToken;
  }
  const response = await fetch(`${localMissionControl.endpoint}${path}`, { ...options, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localMissionControl.token}`, ...(options.headers || {}) } });
  if (response.status === 401) { localMissionControl.token = null; return localApi(path, options); }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Mission Control 请求失败 (${response.status})`);
  return body;
}

function renderTaskResult(mission, details = {}) {
  elements.taskResult.hidden = false;
  elements.resultStatus.textContent = mission.status || '处理中';
  elements.resultSummary.textContent = mission.objective || '';
  const items = [...(details.workUnits || []), ...(details.artifacts || []), ...(details.evidence || [])];
  elements.resultItems.innerHTML = items.length ? items.map((item) => `<div class="result-item"><strong>${item.id || item.kind || '结果'}</strong><span>${item.status || item.verdict || item.summary || '已记录'}</span></div>`).join('') : '<div class="result-item"><span>任务已创建，等待执行服务产生结果。</span></div>';
}

async function pollMission(missionId) {
  let afterSequence = 0;
  const eventBuffer = [];
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const mission = (await localApi(`/api/v1/missions/${encodeURIComponent(missionId)}`)).mission || await localApi(`/api/v1/missions/${encodeURIComponent(missionId)}`);
    const [workUnits, artifacts, evidence, events] = await Promise.all([
      localApi(`/api/v1/missions/${missionId}/work-units`), localApi(`/api/v1/missions/${missionId}/artifacts`), localApi(`/api/v1/missions/${missionId}/evidence`),
      localApi(`/api/v1/missions/${missionId}/events?afterSequence=${afterSequence}&limit=100`).catch(() => ({ events: [] })),
    ]);
    const newEvents = events.events || [];
    if (newEvents.length) {
      afterSequence = newEvents[newEvents.length - 1].sequence ?? afterSequence;
      eventBuffer.push(...newEvents);
      const container = document.querySelector('#result-events');
      if (container) container.replaceChildren(...eventBuffer.slice(-12).reverse().map((e) => {
        const row = document.createElement('div'); row.className = 'result-item';
        row.innerHTML = `<strong>#${e.sequence ?? '?'} ${e.event_type || e.eventType || '事件'}</strong><span>${(e.occurred_at || e.occurredAt || '').toString().replace('T', ' ').slice(0, 19)}</span>`; return row;
      }));
    }
    renderTaskResult(mission, { workUnits: workUnits.workUnits, artifacts: artifacts.artifacts, evidence: evidence.evidence });
    if (['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(mission.status)) { elements.cancelMission.hidden = true; activeMissionId = null; return; }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  elements.cancelMission.hidden = true; activeMissionId = null;
}

async function cancelActiveMission() {
  if (!activeMissionId) return;
  elements.cancelMission.disabled = true;
  try {
    await localApi(`/api/v1/missions/${encodeURIComponent(activeMissionId)}/cancel`, { method: 'POST' });
    elements.feedback.textContent = '已请求取消任务，等待状态更新…';
  } catch (error) { elements.feedback.textContent = error instanceof Error ? error.message : '任务取消失败'; }
  finally { elements.cancelMission.disabled = false; }
}

async function stopServices() {
  elements.stopRuntime.disabled = true;
  try {
    await nativeInvoke('stop_runtime');
    const services = await nativeInvoke('service_status');
    renderServices(services); renderWorkspaceState(services, { endpointConfigured: !elements.openConsoleNav.disabled });
    elements.feedback.textContent = '本地服务已停止';
  } catch (error) { elements.feedback.textContent = error instanceof Error ? error.message : '停止本地服务失败'; }
  finally { elements.stopRuntime.disabled = false; }
}

async function startTask() {
  const prompt = elements.taskInput.value.trim();
  if (!prompt) { elements.feedback.textContent = '请先描述要完成的任务'; elements.taskInput.focus(); return; }
  if (!servicesReady) { elements.feedback.textContent = '本地服务尚未就绪，请稍候…'; return; }
  elements.startTask.disabled = true; elements.feedback.textContent = '正在创建 Mission…';
  try {
    const contract = { id: `desktop-${Date.now()}`, version: 1, repositoryScopes: [], allowedCapabilities: [], budgets: { timeSeconds: 300, modelCost: 1, retries: 0 }, acceptanceCriteria: [{ id: 'desktop-review', kind: 'manual', description: '桌面端用户审核 Mission 输出。', required: true, configuration: {} }], decisionGates: [], forbiddenActions: [] };
    const created = await localApi('/api/v1/missions', { method: 'POST', body: JSON.stringify({ workspaceId: localMissionControl.workspaceId, title: prompt.slice(0, 80), objective: prompt, source: { type: 'manual' }, contract }) });
    const mission = created.mission || created;
    activeMissionId = mission.id; elements.cancelMission.hidden = false;
    renderTaskResult(mission); elements.feedback.textContent = 'Mission 已创建，正在启动…';
    await localApi(`/api/v1/missions/${encodeURIComponent(mission.id)}/start`, { method: 'POST' });
    elements.feedback.textContent = 'Mission 已启动，正在更新执行结果…';
    await pollMission(mission.id);
    elements.feedback.textContent = 'Mission 状态已更新';
  } catch (error) { elements.feedback.textContent = error instanceof Error ? error.message : '任务创建失败'; }
  finally { renderWorkspaceFromLastState(); }
}

function renderWorkspaceFromLastState() {
  elements.startTask.disabled = !servicesReady;
  elements.startTask.innerHTML = servicesReady ? '开始任务 <span aria-hidden="true">↗</span>' : '启动中…';
}

const secretLabel = (value) => value === 'configured' ? '已保存' : value === 'unavailable' ? '不可用' : '未配置';
function renderConfigurationDetails(details) {
  elements.missionControlEndpoint.value = details.missionControlEndpoint ?? ''; elements.mcpEndpoint.value = details.mcpEndpoint ?? ''; elements.artifactDirectory.value = details.artifactDirectory ?? '';
  elements.missionControlToken.value = ''; elements.mcpToken.value = ''; elements.configurationModelApiKey.value = '';
  elements.secretStatus.textContent = `凭据：Mission Control ${secretLabel(details.missionControlToken)} · MCP ${secretLabel(details.mcpToken)} · Model API ${secretLabel(details.modelApiKey)}`;
  const availability = { mission_control_token: details.missionControlToken, mcp_token: details.mcpToken, model_api_key: details.modelApiKey };
  for (const button of document.querySelectorAll('[data-clear-secret]')) button.disabled = availability[button.dataset.clearSecret] !== 'configured';
}
async function clearStoredSecret(kind, button) {
  button.disabled = true;
  try {
    await nativeInvoke('clear_configuration_secret', { kind });
    renderConfigurationDetails(await nativeInvoke('configuration_details'));
    elements.feedback.textContent = '凭据已清除';
  } catch (error) { elements.feedback.textContent = error instanceof Error ? error.message : '凭据清除失败'; button.disabled = false; }
}
async function loadAccount() {
  const stateEl = document.querySelector('#account-credential-state');
  try {
    const probe = await fetch(`${localMissionControl.endpoint}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'admin', password: 'admin123' }) });
    if (probe.status === 401 || probe.status === 403) {
      if (stateEl) stateEl.textContent = '✓ 默认密码已修改';
    } else if (probe.ok) {
      if (stateEl) stateEl.textContent = '⚠ 仍在使用默认密码 admin123，请在下方修改';
    } else {
      if (stateEl) stateEl.textContent = '本地服务未运行，无法检查凭据状态。';
    }
  } catch {
    if (stateEl) stateEl.textContent = '本地服务未运行，无法检查凭据状态。';
  }
}

async function changePassword(event) {
  event.preventDefault();
  const current = document.querySelector('#account-current-password').value;
  const next = document.querySelector('#account-new-password').value;
  const confirm = document.querySelector('#account-confirm-password').value;
  if (next !== confirm) { elements.adminFeedback.textContent = '两次输入的新密码不一致'; return; }
  try {
    await localApi('/api/user/change-password', { method: 'POST', body: JSON.stringify({ current_password: current, new_password: next }) });
    elements.adminFeedback.textContent = '密码已修改';
    document.querySelector('#password-form').reset();
    await loadAccount();
  } catch (error) {
    elements.adminFeedback.textContent = error instanceof Error ? error.message : '密码修改失败';
  }
}

async function loadMonitor() {
  const stackEl = document.querySelector('#monitor-stack');
  const stackState = document.querySelector('#monitor-stack-state');
  const pinSelect = document.querySelector('#stack-pin-select');
  try {
    const stack = await nativeInvoke('stack_info');
    if (stack && stack.manifest) {
      const m = stack.manifest;
      const sourceLabel = stack.source === 'bundled' ? '捆绑栈' : stack.source === 'persisted' ? '历史栈回退' : stack.source === 'pinned' ? `已钉住 ${stack.pinned || ''}` : '';
      let text = `v${m.version}${m.commit ? ` · ${m.commit}` : ''} · ${sourceLabel} · 打包于 ${new Date(m.generatedAt).toLocaleString()}`;
      if (Array.isArray(stack.persisted) && stack.persisted.length > 0) {
        text += `；本机已存栈：${stack.persisted.map((p) => `v${p.version}${p.commit ? `@${p.commit.slice(0, 7)}` : ''}`).join('、')}`;
      }
      if (stackEl) stackEl.textContent = text;
      if (stackState) stackState.textContent = stack.source === 'persisted' ? '回退' : stack.source === 'pinned' ? '钉住' : '已加载';
      if (pinSelect) {
        pinSelect.replaceChildren(...(stack.persisted || []).map((p) => {
          const option = document.createElement('option');
          option.value = `${p.version}|${p.commit || ''}`;
          option.textContent = `v${p.version}${p.commit ? ` @${p.commit.slice(0, 7)}` : ''}（${new Date(p.generatedAt).toLocaleDateString()}）`;
          return option;
        }));
        if (stack.pinned) {
          const current = [...pinSelect.options].find((o) => stack.pinned.startsWith(o.value.replace('|', '-')));
          if (current) pinSelect.value = current.value;
        }
      }
    } else {
      if (stackEl) stackEl.textContent = '未找到 stack-manifest（旧包或开发目录运行）。';
      if (stackState) stackState.textContent = '无清单';
      if (pinSelect) pinSelect.replaceChildren();
    }
  } catch {
    if (stackEl) stackEl.textContent = '桌面命令不可用，需在 AgentHub 桌面应用中打开。';
  }
  const runtimeEl = document.querySelector('#monitor-runtime');
  const runtimeState = document.querySelector('#monitor-runtime-state');
  const healthState = document.querySelector('#monitor-health-state');
  const healthEl = document.querySelector('#monitor-health');
  try {
    const services = await nativeInvoke('service_status');
    renderServices(services);
    const runtime = await nativeInvoke('runtime_status');
    if (runtimeEl) runtimeEl.textContent = runtime.detail || '—';
    if (runtimeState) runtimeState.textContent = `${statusText(runtime.status)} / ${runtime.readiness || 'unknown'}`;
  } catch (error) {
    renderServices([]);
  }
  try {
    const health = await localApi('/api/metrics/health');
    if (healthState) healthState.textContent = health.status === 'healthy' ? '健康' : health.status || '未知';
    if (healthEl) healthEl.textContent = `模型 ${health.modelsHealthy ?? '?'} 正常 / ${health.modelsDegraded ?? '?'} 降级 · 活动降级 ${health.activeDegradations ?? '?'} · 运行 ${Math.round((health.uptimeSeconds ?? 0) / 60)} 分钟`;
  } catch {
    if (healthState) healthState.textContent = '不可达';
    if (healthEl) healthEl.textContent = '控制面健康探测失败（本地服务未运行？）';
  }
}

async function openConfiguration() { try { renderConfigurationDetails(await nativeInvoke('configuration_details')); elements.dialog.showModal(); } catch (error) { elements.feedback.textContent = error instanceof Error ? error.message : '设置读取失败'; } }
function closeConfiguration() { if (elements.dialog.open) elements.dialog.close(); }
async function saveConfiguration(event) {
  event.preventDefault(); elements.saveConfiguration.disabled = true;
  try {
    await nativeInvoke('save_configuration', { input: { missionControlEndpoint: elements.missionControlEndpoint.value, mcpEndpoint: elements.mcpEndpoint.value, artifactDirectory: elements.artifactDirectory.value } });
    for (const [field, kind] of [['missionControlToken', 'mission_control_token'], ['mcpToken', 'mcp_token'], ['configurationModelApiKey', 'model_api_key']]) if (elements[field].value) await nativeInvoke('set_configuration_secret', { input: { kind, value: elements[field].value } });
    closeConfiguration(); await refresh(); elements.feedback.textContent = '高级设置已保存';
  } catch (error) { elements.feedback.textContent = error instanceof Error ? error.message : '设置保存失败'; }
  finally { elements.saveConfiguration.disabled = false; }
}

elements.workspaceState.addEventListener('click', refresh);
elements.startTask.addEventListener('click', startTask);
elements.stopRuntime.addEventListener('click', stopServices);
elements.cancelMission.addEventListener('click', cancelActiveMission);
elements.historyRefresh.addEventListener('click', loadHistory);
elements.taskInput.addEventListener('input', () => {
  const el = elements.taskInput;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 340)}px`;
});
elements.modelList.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-model-test]');
  if (!button) return;
  button.disabled = true; button.textContent = '测试中…';
  try {
    const result = await localApi(`/api/admin/models/${button.dataset.modelTest}/test`, { method: 'POST' });
    button.textContent = result.status === 'success' ? `连通 ${result.latencyMs}ms` : '失败';
  } catch { button.textContent = '失败'; }
  finally { button.disabled = false; }
});
elements.navHome.addEventListener('click', () => switchView('home'));
elements.navHistory.addEventListener('click', () => switchView('history'));
elements.adminRefresh.addEventListener('click', loadAdmin); elements.modelForm.addEventListener('submit', saveModel);
for (const tab of elements.adminTabs) tab.addEventListener('click', () => { for (const item of elements.adminTabs) item.classList.toggle('active', item === tab); for (const section of document.querySelectorAll('.admin-section')) section.hidden = section.id !== `admin-${tab.dataset.adminTab}`; });
document.querySelector('#open-mcp-settings').addEventListener('click', openConfiguration);
document.querySelector('#open-advanced-settings').addEventListener('click', openConfiguration);
for (const button of document.querySelectorAll('[data-clear-secret]')) button.addEventListener('click', () => clearStoredSecret(button.dataset.clearSecret, button));
document.querySelector('#password-form').addEventListener('submit', changePassword);
document.querySelector('#stack-pin-apply').addEventListener('click', () => {
  const select = document.querySelector('#stack-pin-select');
  if (!select || !select.value) { elements.feedback.textContent = '本机暂无可钉住的历史栈'; return; }
  const [version, commit] = select.value.split('|');
  nativeInvoke('pin_stack', { version, commit }).then((message) => { elements.feedback.textContent = message; loadMonitor(); })
    .catch((error) => { elements.feedback.textContent = error instanceof Error ? error.message : '钉住失败'; });
});
document.querySelector('#stack-pin-clear').addEventListener('click', () => {
  nativeInvoke('clear_stack_pin').then((message) => { elements.feedback.textContent = message; loadMonitor(); })
    .catch((error) => { elements.feedback.textContent = error instanceof Error ? error.message : '取消钉住失败'; });
});
for (const button of [elements.openConsole, elements.openConsoleNav]) button.addEventListener('click', openConsole);
for (const card of document.querySelectorAll('[data-prompt]')) card.addEventListener('click', () => { elements.taskInput.value = card.dataset.prompt; elements.taskInput.dispatchEvent(new Event('input')); elements.taskInput.focus(); });
elements.settings.addEventListener('click', () => switchView('settings')); elements.settingsBack.addEventListener('click', () => switchView('home')); for (const item of elements.settingsItems) item.addEventListener('click', () => selectSettingsSection(item.dataset.settingsSection)); elements.closeConfiguration.addEventListener('click', closeConfiguration); elements.cancelConfiguration.addEventListener('click', closeConfiguration); elements.form.addEventListener('submit', saveConfiguration);

refresh();
