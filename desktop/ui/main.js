const elements = {
  status: document.querySelector('#runtime-status'),
  detail: document.querySelector('#runtime-detail'),
  readiness: document.querySelector('#runtime-readiness'),
  dot: document.querySelector('#state-dot'),
  start: document.querySelector('#start'),
  stop: document.querySelector('#stop'),
  refresh: document.querySelector('#refresh'),
};

const labels = {
  stopped: '已停止',
  starting: '启动中',
  running: '运行中',
  configuration_required: '需要配置',
  failed: '启动失败',
};

function nativeInvoke(command) {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) {
    return Promise.resolve({
      status: 'configuration_required',
      detail: '该页面需要在 AgentHub 桌面应用中打开。',
    });
  }
  return invoke(command);
}

function render(snapshot) {
  const status = labels[snapshot.status] ?? '未知状态';
  elements.status.textContent = status;
  elements.detail.textContent = snapshot.detail;
  const readiness = { unknown: '未知', probing: '探测中', ready: '已就绪', unhealthy: '不健康' };
  elements.readiness.textContent = `就绪状态：${readiness[snapshot.readiness] ?? '未知'}`;
  elements.dot.className = `dot ${snapshot.status}`;
  elements.start.disabled = ['running', 'starting'].includes(snapshot.status);
  elements.stop.disabled = snapshot.status === 'stopped' || snapshot.status === 'configuration_required';
}

async function invoke(command) {
  elements.refresh.disabled = true;
  try {
    render(await nativeInvoke(command));
  } finally {
    elements.refresh.disabled = false;
  }
}

elements.refresh.addEventListener('click', () => invoke('runtime_status'));
elements.start.addEventListener('click', () => invoke('start_runtime'));
elements.stop.addEventListener('click', () => invoke('stop_runtime'));
invoke('runtime_status');
