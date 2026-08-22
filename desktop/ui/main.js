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

elements.refresh.addEventListener('click', refresh);
elements.start.addEventListener('click', () => invokeRuntime('start_runtime'));
elements.stop.addEventListener('click', () => invokeRuntime('stop_runtime'));
elements.openConsole.addEventListener('click', openConsole);
refresh();
