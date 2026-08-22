const elements = {
  controlState: document.querySelector('#control-state'),
  controlTitle: document.querySelector('#control-plane-title'),
  controlDetail: document.querySelector('#control-detail'),
  openConsole: document.querySelector('#open-console'),
  runtimeStatus: document.querySelector('#runtime-status'),
  runtimeDetail: document.querySelector('#runtime-detail'),
  runtimeReadiness: document.querySelector('#runtime-readiness'),
  runtimeDot: document.querySelector('#state-dot'),
  start: document.querySelector('#start'),
  stop: document.querySelector('#stop'),
  refresh: document.querySelector('#refresh'),
  feedback: document.querySelector('#feedback'),
  settings: document.querySelector('#settings'),
  dialog: document.querySelector('#configuration-dialog'),
  form: document.querySelector('#configuration-form'),
  closeConfiguration: document.querySelector('#close-configuration'),
  cancelConfiguration: document.querySelector('#cancel-configuration'),
  saveConfiguration: document.querySelector('#save-configuration'),
  missionControlEndpoint: document.querySelector('#mission-control-endpoint'),
  mcpEndpoint: document.querySelector('#mcp-endpoint'),
  artifactDirectory: document.querySelector('#artifact-directory'),
  missionControlToken: document.querySelector('#mission-control-token'),
  mcpToken: document.querySelector('#mcp-token'),
  modelApiKey: document.querySelector('#model-api-key'),
  secretStatus: document.querySelector('#secret-status'),
};

const runtimeLabels = {
  stopped: '已停止',
  starting: '启动中',
  running: '运行中',
  configuration_required: '需要配置',
  failed: '启动失败',
};

const readinessLabels = {
  unknown: '未知',
  probing: '探测中',
  ready: '已就绪',
  unhealthy: '不健康',
};

function nativeInvoke(command, args) {
  const invoke = window.__TAURI__?.core?.invoke;
  if (invoke) {
    return invoke(command, args);
  }

  if (command === 'runtime_status') {
    return Promise.resolve({
      status: 'configuration_required',
      readiness: 'unknown',
      detail: '该页面需要在 AgentHub 桌面应用中打开。',
    });
  }
  if (command === 'configuration_status') {
    return Promise.resolve({
      missionControlEndpointConfigured: false,
      readyForRuntime: false,
      missionControlToken: 'missing',
    });
  }
  if (command === 'configuration_details') {
    return Promise.resolve({
      missionControlEndpoint: null,
      mcpEndpoint: null,
      artifactDirectory: null,
      missionControlToken: 'missing',
      mcpToken: 'missing',
      modelApiKey: 'missing',
    });
  }
  return Promise.reject(new Error('桌面命令不可用'));
}

function renderRuntime(snapshot) {
  elements.runtimeStatus.textContent = runtimeLabels[snapshot.status] ?? '未知状态';
  elements.runtimeDetail.textContent = snapshot.detail;
  elements.runtimeReadiness.textContent = `就绪状态：${readinessLabels[snapshot.readiness] ?? '未知'}`;
  elements.runtimeDot.className = `dot ${snapshot.status}`;
  elements.start.disabled = ['running', 'starting'].includes(snapshot.status);
  elements.stop.disabled = ['stopped', 'configuration_required'].includes(snapshot.status);
}

function renderConfiguration(configuration) {
  const connected = configuration.missionControlEndpointConfigured === true;
  elements.controlState.textContent = connected ? '已配置' : '未配置';
  elements.controlState.className = `state-chip ${connected ? 'configured' : 'missing'}`;
  elements.controlTitle.textContent = connected ? '工作区已准备好' : '还没有连接工作区';
  elements.controlDetail.textContent = connected
    ? '从这里进入管理后台，继续处理当前工作。'
    : '完成连接配置后，从这里打开管理后台。';
  elements.openConsole.disabled = !connected;
}

async function refresh() {
  elements.refresh.disabled = true;
  elements.feedback.textContent = '正在检查连接状态';
  try {
    const [runtime, configuration] = await Promise.all([
      nativeInvoke('runtime_status'),
      nativeInvoke('configuration_status'),
    ]);
    renderRuntime(runtime);
    renderConfiguration(configuration);
    elements.feedback.textContent = configuration.missionControlEndpointConfigured
      ? '状态已更新'
      : '需要完成连接配置';
  } catch (error) {
    elements.feedback.textContent = error instanceof Error ? error.message : '状态读取失败';
  } finally {
    elements.refresh.disabled = false;
  }
}

async function invokeRuntime(command) {
  elements.refresh.disabled = true;
  try {
    renderRuntime(await nativeInvoke(command));
  } catch (error) {
    elements.feedback.textContent = error instanceof Error ? error.message : '操作失败';
  } finally {
    elements.refresh.disabled = false;
  }
}

async function openConsole() {
  elements.openConsole.disabled = true;
  elements.feedback.textContent = '正在打开管理后台';
  try {
    await nativeInvoke('open_control_plane');
    elements.feedback.textContent = '已在默认浏览器打开管理后台';
  } catch (error) {
    elements.feedback.textContent = error instanceof Error ? error.message : '管理后台打开失败';
  } finally {
    elements.openConsole.disabled = false;
  }
}

function secretLabel(value) {
  return value === 'configured' ? '已保存' : value === 'unavailable' ? '不可用' : '未配置';
}

function renderConfigurationDetails(details) {
  elements.missionControlEndpoint.value = details.missionControlEndpoint ?? '';
  elements.mcpEndpoint.value = details.mcpEndpoint ?? '';
  elements.artifactDirectory.value = details.artifactDirectory ?? '';
  elements.missionControlToken.value = '';
  elements.mcpToken.value = '';
  elements.modelApiKey.value = '';
  elements.secretStatus.textContent = `凭据：Mission Control ${secretLabel(details.missionControlToken)} · MCP ${secretLabel(details.mcpToken)} · Model API ${secretLabel(details.modelApiKey)}`;
}

async function openConfiguration() {
  elements.settings.disabled = true;
  elements.feedback.textContent = '正在读取连接设置';
  try {
    renderConfigurationDetails(await nativeInvoke('configuration_details'));
    elements.dialog.showModal();
  } catch (error) {
    elements.feedback.textContent = error instanceof Error ? error.message : '设置读取失败';
  } finally {
    elements.settings.disabled = false;
  }
}

function closeConfiguration() {
  if (elements.dialog.open) elements.dialog.close();
}

async function saveConfiguration(event) {
  event.preventDefault();
  elements.saveConfiguration.disabled = true;
  elements.feedback.textContent = '正在保存连接设置';
  try {
    await nativeInvoke('save_configuration', {
      input: {
        missionControlEndpoint: elements.missionControlEndpoint.value,
        mcpEndpoint: elements.mcpEndpoint.value,
        artifactDirectory: elements.artifactDirectory.value,
      },
    });
    const secrets = [
      ['missionControlToken', 'mission_control_token'],
      ['mcpToken', 'mcp_token'],
      ['modelApiKey', 'model_api_key'],
    ];
    for (const [field, kind] of secrets) {
      const value = elements[field].value;
      if (value) await nativeInvoke('set_configuration_secret', { input: { kind, value } });
    }
    closeConfiguration();
    await refresh();
    elements.feedback.textContent = '连接设置已保存';
  } catch (error) {
    elements.feedback.textContent = error instanceof Error ? error.message : '设置保存失败，可重试';
  } finally {
    elements.saveConfiguration.disabled = false;
  }
}

elements.refresh.addEventListener('click', refresh);
elements.start.addEventListener('click', () => invokeRuntime('start_runtime'));
elements.stop.addEventListener('click', () => invokeRuntime('stop_runtime'));
elements.openConsole.addEventListener('click', openConsole);
elements.settings.addEventListener('click', openConfiguration);
elements.closeConfiguration.addEventListener('click', closeConfiguration);
elements.cancelConfiguration.addEventListener('click', closeConfiguration);
elements.form.addEventListener('submit', saveConfiguration);
refresh();
