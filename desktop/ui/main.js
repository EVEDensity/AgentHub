const elements = {
  workspaceState: document.querySelector('#workspace-state'), serviceList: document.querySelector('#service-list'), refresh: document.querySelector('#refresh'), feedback: document.querySelector('#feedback'), taskInput: document.querySelector('#task-input'), startTask: document.querySelector('#start-task'), openConsole: document.querySelector('#open-console'), openConsoleNav: document.querySelector('#open-console-nav'), openConsoleCard: document.querySelector('#open-console-card'), navHome: document.querySelector('#nav-home'), homeView: document.querySelector('#home-view'), adminView: document.querySelector('#admin-view'), settingsView: document.querySelector('#settings-view'), settings: document.querySelector('#settings'), settingsNav: document.querySelector('#settings-nav'), mainNav: document.querySelector('#main-nav'), settingsBack: document.querySelector('#settings-back'), settingsItems: document.querySelectorAll('[data-settings-section]'), settingsPanels: document.querySelectorAll('[data-settings-panel]'), settingsTitle: document.querySelector('#settings-title'), adminRefresh: document.querySelector('#admin-refresh'), adminFeedback: document.querySelector('#admin-feedback'), modelForm: document.querySelector('#model-form'), modelProvider: document.querySelector('#model-provider'), modelName: document.querySelector('#model-name'), modelBaseUrl: document.querySelector('#model-base-url'), modelApiKey: document.querySelector('#model-api-key'), modelList: document.querySelector('#model-list'), agentList: document.querySelector('#agent-list'), mcpEndpointLabel: document.querySelector('#mcp-endpoint-label'), adminTabs: document.querySelectorAll('[data-admin-tab]'), dialog: document.querySelector('#configuration-dialog'), form: document.querySelector('#configuration-form'), closeConfiguration: document.querySelector('#close-configuration'), cancelConfiguration: document.querySelector('#cancel-configuration'), saveConfiguration: document.querySelector('#save-configuration'), missionControlEndpoint: document.querySelector('#mission-control-endpoint'), mcpEndpoint: document.querySelector('#mcp-endpoint'), artifactDirectory: document.querySelector('#artifact-directory'), missionControlToken: document.querySelector('#mission-control-token'), mcpToken: document.querySelector('#mcp-token'), secretStatus: document.querySelector('#secret-status'), taskResult: document.querySelector('#task-result'), resultStatus: document.querySelector('#result-status'), resultSummary: document.querySelector('#result-summary'), resultItems: document.querySelector('#result-items'), configurationModelApiKey: document.querySelector('#configuration-model-api-key'), adminFrame: document.querySelector('#admin-frame'), stopRuntime: document.querySelector('#stop-runtime'), cancelMission: document.querySelector('#cancel-mission'),
};

function nativeInvoke(command, args) {
  const invoke = window.__TAURI__?.core?.invoke;
  if (invoke) return invoke(command, args);
  if (command === 'service_status') return Promise.resolve([]);
  if (command === 'runtime_status') return Promise.resolve({ status: 'stopped', readiness: 'unknown', detail: '请在 AgentHub 桌面应用中打开。' });
  if (command === 'probe_control_plane') return Promise.resolve({ reachability: 'not_configured', endpointConfigured: false, detail: '请在 AgentHub 桌面应用中打开。' });
  if (command === 'probe_mcp') return Promise.resolve({ reachability: 'not_configured', endpointConfigured: false, detail: '请在 AgentHub 桌面应用中打开。' });
  if (command === 'configuration_details') return Promise.resolve({ missionControlEndpoint: null, mcpEndpoint: null, artifactDirectory: null, missionControlToken: 'missing', mcpToken: 'missing', modelApiKey: 'missing' });
  if (command === 'local_service_endpoint') return Promise.resolve('http://127.0.0.1:28000');
  if (command === 'frontend_endpoint') return Promise.resolve('http://127.0.0.1:28004');
  return Promise.reject(new Error('桌面命令不可用'));
}

const serviceLabels = { 'mission-control': 'Mission Control', gateway: 'Gateway', 'mcp-gateway': 'MCP Gateway' };
const localMissionControl = { endpoint: 'http://127.0.0.1:28000', workspaceId: 'local-admin', token: null };
let activeMissionId = null;
const statusClass = (status) => String(status || 'unknown').toLowerCase();
const statusText = (status) => ({ missing: '未打包', stopped: '已停止', starting: '启动中', ready: '已就绪', failed: '失败' }[statusClass(status)] || '未知');

function renderServices(services) {
  if (!Array.isArray(services) || services.length === 0) {
    elements.serviceList.innerHTML = '<div class="service-row"><span class="service-dot failed"></span><span class="service-name">本地服务</span><span class="service-detail">未返回服务状态</span></div>';
    return;
  }
  elements.serviceList.replaceChildren(...services.map((service) => {
    const row = document.createElement('div'); row.className = 'service-row';
    row.innerHTML = `<span class="service-dot ${statusClass(service.status)}"></span><span class="service-name">${serviceLabels[service.name] || service.name}</span><span class="service-detail">${statusText(service.status)}${service.detail ? ` · ${service.detail}` : ''}</span>`;
    return row;
  }));
}

function renderWorkspaceState(services, controlPlane) {
  const states = Array.isArray(services) ? services.map((item) => statusClass(item.status)) : [];
  const failed = states.some((state) => ['failed', 'missing'].includes(state));
  const ready = states.length > 0 && states.every((state) => state === 'ready');
  const starting = states.some((state) => state === 'starting');
  const label = failed ? '服务异常' : ready ? '本地工作区已就绪' : starting ? '正在启动本地服务' : '正在检查本地服务';
  elements.workspaceState.innerHTML = `<span class="dot ${failed ? 'failed' : ready ? 'ready' : 'starting'}"></span>${label}`;
  if (elements.stopRuntime) elements.stopRuntime.hidden = !states.some((state) => ['ready', 'starting'].includes(state));
  const canOpen = controlPlane?.endpointConfigured === true;
  for (const button of [elements.openConsole, elements.openConsoleNav, elements.openConsoleCard]) button.disabled = !canOpen;
}

async function refresh() {
  elements.refresh.disabled = true; elements.feedback.textContent = '正在启动本地工作区…';
  try {
    await nativeInvoke('start_runtime');
    localMissionControl.endpoint = await nativeInvoke('local_service_endpoint');
    const [services, controlPlane, configStatus] = await Promise.all([
      nativeInvoke('service_status'), nativeInvoke('probe_control_plane'),
      nativeInvoke('configuration_status').catch(() => null),
    ]);
    renderServices(services); renderWorkspaceState(services, controlPlane);
    const mcp = services.find((service) => service.name === 'mcp-gateway');
    elements.mcpEndpointLabel.textContent = mcp?.detail || '本地服务状态读取中';
    let statusMessage = controlPlane?.detail || '本地服务状态已更新';
    if (configStatus && configStatus.readyForRuntime === false) {
      const gaps = [
        !configStatus.artifactDirectoryConfigured && 'Artifact 目录未配置',
        !configStatus.missionControlEndpointConfigured && 'Mission Control 地址未配置',
      ].filter(Boolean);
      if (gaps.length) statusMessage = `运行时尚未就绪：${gaps.join(' · ')}（可在设置中完成配置）`;
    }
    elements.feedback.textContent = statusMessage;
  } catch (error) { elements.feedback.textContent = error instanceof Error ? error.message : '本地工作区启动失败'; renderWorkspaceState([], { endpointConfigured: false }); }
  finally { elements.refresh.disabled = false; }
}

function switchView(view) {
  const settings = view === 'settings';
  elements.homeView.hidden = settings; elements.adminView.hidden = true; elements.settingsView.hidden = !settings;
  elements.mainNav.hidden = settings; elements.settingsNav.hidden = !settings;
  elements.navHome.classList.toggle('active', !settings);
  if (settings) selectSettingsSection('general');
}

async function openAdminFrame() {
  try {
    const endpoint = await nativeInvoke('frontend_endpoint');
    elements.adminFrame.src = `${endpoint}/admin?menu=通用`;
  } catch (error) {
    elements.settingsTitle.textContent = '配置';
    elements.adminFrame.replaceWith(Object.assign(document.createElement('p'), { textContent: error instanceof Error ? error.message : '完整管理后台启动失败', className: 'settings-description' }));
  }
}

function selectSettingsSection(section) {
  for (const item of elements.settingsItems) item.classList.toggle('active', item.dataset.settingsSection === section);
  for (const panel of elements.settingsPanels) panel.hidden = panel.dataset.settingsPanel !== section;
  const active = [...elements.settingsItems].find((item) => item.dataset.settingsSection === section);
  elements.settingsTitle.textContent = active?.textContent?.trim() || '设置';
  if (section === 'configuration') openAdminFrame();
  if (section === 'monitoring') loadMonitor();
}

function renderMcpProbe(probe) {
  if (!probe) return;
  const dot = document.querySelector('#mcp-status-dot');
  const dotState = probe.reachability === 'reachable' ? 'ready' : probe.reachability === 'not_configured' ? 'stopped' : 'failed';
  if (dot) dot.className = `service-dot ${dotState}`;
  if (probe.detail) elements.mcpEndpointLabel.textContent = probe.detail;
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
    elements.modelForm.reset(); await loadAdmin(); elements.adminFeedback.textContent = '模型配置已保存';
  } catch (error) { elements.adminFeedback.textContent = error instanceof Error ? error.message : '模型配置保存失败'; }
}

async function openConsole() {
  elements.feedback.textContent = '正在打开工作区…';
  try { switchView('settings'); selectSettingsSection('configuration'); elements.feedback.textContent = '已打开本地完整管理后台'; }
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
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const mission = (await localApi(`/api/v1/missions/${encodeURIComponent(missionId)}`)).mission || await localApi(`/api/v1/missions/${encodeURIComponent(missionId)}`);
    const [workUnits, artifacts, evidence] = await Promise.all([
      localApi(`/api/v1/missions/${missionId}/work-units`), localApi(`/api/v1/missions/${missionId}/artifacts`), localApi(`/api/v1/missions/${missionId}/evidence`),
    ]);
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
    renderServices(services); renderWorkspaceState(services, { endpointConfigured: false });
    elements.feedback.textContent = '本地服务已停止';
  } catch (error) { elements.feedback.textContent = error instanceof Error ? error.message : '停止本地服务失败'; }
  finally { elements.stopRuntime.disabled = false; }
}

async function startTask() {
  const prompt = elements.taskInput.value.trim();
  if (!prompt) { elements.feedback.textContent = '请先描述要完成的任务'; elements.taskInput.focus(); return; }
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
  finally { elements.startTask.disabled = false; }
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
async function loadMonitor() {
  const servicesEl = document.querySelector('#monitor-services');
  const runtimeEl = document.querySelector('#monitor-runtime');
  const runtimeState = document.querySelector('#monitor-runtime-state');
  const healthEl = document.querySelector('#monitor-health');
  const healthState = document.querySelector('#monitor-health-state');
  try {
    const services = await nativeInvoke('service_status');
    const rows = (Array.isArray(services) ? services : []).map((s) => `${serviceLabels[s.name] || s.name}: ${statusText(s.status)}`);
    if (servicesEl) servicesEl.textContent = rows.length ? rows.join(' · ') : '未返回服务状态';
    const runtime = await nativeInvoke('runtime_status');
    if (runtimeEl) runtimeEl.textContent = runtime.detail || '—';
    if (runtimeState) runtimeState.textContent = `${statusText(runtime.status)} / ${runtime.readiness || 'unknown'}`;
  } catch (error) {
    if (servicesEl) servicesEl.textContent = error instanceof Error ? error.message : '服务状态读取失败';
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

elements.refresh.addEventListener('click', refresh); elements.startTask.addEventListener('click', startTask);
elements.stopRuntime.addEventListener('click', stopServices);
elements.cancelMission.addEventListener('click', cancelActiveMission);
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
elements.navHome.addEventListener('click', () => switchView('home')); elements.adminRefresh.addEventListener('click', loadAdmin); elements.modelForm.addEventListener('submit', saveModel);
for (const tab of elements.adminTabs) tab.addEventListener('click', () => { for (const item of elements.adminTabs) item.classList.toggle('active', item === tab); for (const section of document.querySelectorAll('.admin-section')) section.hidden = section.id !== `admin-${tab.dataset.adminTab}`; });
document.querySelector('#open-mcp-settings').addEventListener('click', openConfiguration);
for (const button of document.querySelectorAll('[data-clear-secret]')) button.addEventListener('click', () => clearStoredSecret(button.dataset.clearSecret, button));
for (const button of [elements.openConsole, elements.openConsoleNav, elements.openConsoleCard]) button.addEventListener('click', openConsole);
for (const card of document.querySelectorAll('[data-prompt]')) card.addEventListener('click', () => { elements.taskInput.value = card.dataset.prompt; elements.taskInput.focus(); });
elements.settings.addEventListener('click', () => switchView('settings')); elements.settingsBack.addEventListener('click', () => switchView('home')); for (const item of elements.settingsItems) item.addEventListener('click', () => selectSettingsSection(item.dataset.settingsSection)); elements.closeConfiguration.addEventListener('click', closeConfiguration); elements.cancelConfiguration.addEventListener('click', closeConfiguration); elements.form.addEventListener('submit', saveConfiguration);
refresh();
