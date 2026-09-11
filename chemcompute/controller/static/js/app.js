/**
 * ChemCompute Web 控制台前端脚本
 * 安全原则：严格转义动态输出，杜绝 XSS 注入风险
 */

// 安全字符转义函数
function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// 格式化时间戳
function formatTime(isoStr) {
  if (!isoStr) return '-';
  try {
    const d = new Date(isoStr);
    return d.toLocaleString('zh-CN', { hour12: false });
  } catch (e) {
    return escapeHtml(isoStr);
  }
}

// 状态管理
const state = {
  adminToken: sessionStorage.getItem('chemcompute_admin_token') || '',
  currentTab: 'nodes',
  pollInterval: null,
};

// 通用受鉴权 API 请求包装
async function apiRequest(url, options = {}) {
  const headers = options.headers || {};
  if (state.adminToken) {
    headers['Authorization'] = `Bearer ${state.adminToken}`;
  }
  options.headers = headers;
  const res = await fetch(url, options);
  if (res.status === 401) {
    showLoginModal();
    throw new Error('未授权，请先登录管理员账户');
  }
  return res;
}

// 页面初始化
document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  checkAuth();
  loadOverview();
  loadCurrentTabData();

  // 定时刷新总览数据与节点列表
  state.pollInterval = setInterval(() => {
    loadOverview();
    if (state.currentTab === 'nodes') loadNodes();
    if (state.currentTab === 'jobs') loadJobs();
  }, 10000);
});

function initTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      tabBtns.forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));

      btn.classList.add('active');
      const target = btn.dataset.tab;
      state.currentTab = target;
      document.getElementById(`tab-${target}`).classList.add('active');
      loadCurrentTabData();
    });
  });
}

function loadCurrentTabData() {
  if (state.currentTab === 'nodes') loadNodes();
  else if (state.currentTab === 'jobs') loadJobs();
  else if (state.currentTab === 'invites') loadInvites();
  else if (state.currentTab === 'audit') loadAuditLogs();
}

function checkAuth() {
  const authStatus = document.getElementById('auth-status');
  const loginBtn = document.getElementById('btn-admin-login');
  if (state.adminToken) {
    authStatus.innerHTML = '<span style="color: var(--accent-green);">● 已认证管理员</span>';
    loginBtn.textContent = '退出登录';
    loginBtn.onclick = logoutAdmin;
  } else {
    authStatus.innerHTML = '<span style="color: var(--text-muted);">○ 访客预览</span>';
    loginBtn.textContent = '管理员登录';
    loginBtn.onclick = () => showLoginModal();
  }
}

function showLoginModal() {
  document.getElementById('login-modal').classList.add('show');
}

function closeLoginModal() {
  document.getElementById('login-modal').classList.remove('show');
}

async function submitAdminLogin() {
  const tokenInput = document.getElementById('admin-token-input');
  const token = tokenInput.value.trim();
  if (!token) return alert('请输入管理员凭据');

  try {
    const res = await fetch('/api/v1/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || '验证失败');
    }
    state.adminToken = token;
    sessionStorage.setItem('chemcompute_admin_token', token);
    closeLoginModal();
    checkAuth();
    loadOverview();
    loadCurrentTabData();
    alert('管理员认证成功');
  } catch (e) {
    alert(e.message);
  }
}

function logoutAdmin() {
  state.adminToken = '';
  sessionStorage.removeItem('chemcompute_admin_token');
  checkAuth();
  loadCurrentTabData();
}

// ---------------- 概览统计 ----------------
async function loadOverview() {
  try {
    const res = await apiRequest('/api/v1/overview');
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById('stat-total-nodes').textContent = data.total_nodes;
    document.getElementById('stat-online-nodes').textContent = data.online_nodes;
    document.getElementById('stat-running-jobs').textContent = data.running_jobs;
    document.getElementById('stat-total-gpus').textContent = data.total_gpus;
  } catch (e) {
    console.warn('获取仪表盘统计失败', e);
  }
}

// ---------------- 节点列表 ----------------
async function loadNodes() {
  const tbody = document.getElementById('nodes-table-body');
  try {
    const res = await apiRequest('/api/v1/nodes');
    if (!res.ok) throw new Error('无法加载节点列表');
    const nodes = await res.json();

    if (!nodes || nodes.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="8" class="empty-state">
            <div class="empty-icon">💻</div>
            <div>暂无纳管的计算节点，请前往【邀请码管理】生成邀请码并在节点执行启动注册</div>
          </td>
        </tr>`;
      return;
    }

    let rowsHtml = '';
    for (const node of nodes) {
      const hw = node.hardware_info || {};
      const sw = node.software_info || {};
      const gmx = sw.gromacs || {};

      let statusBadge = '';
      if (node.status === 'online') statusBadge = '<span class="badge badge-online">● 在线</span>';
      else if (node.status === 'busy') statusBadge = '<span class="badge badge-busy">● 计算中</span>';
      else statusBadge = '<span class="badge badge-offline">○ 离线</span>';

      // GPU 概括
      let gpuDesc = '无 GPU / 未启用';
      if (hw.gpus && hw.gpus.length > 0) {
        gpuDesc = hw.gpus.map(g => `${escapeHtml(g.name)} (${g.total_memory_mb}MB)`).join('<br>');
      }

      // GROMACS 信息
      let gmxDesc = '<span style="color: var(--text-muted)">未检测到</span>';
      if (gmx.found) {
        const cudaTag = gmx.cuda_enabled ? '<span class="badge badge-online">CUDA</span>' : '';
        const mpiTag = gmx.mpi_enabled ? '<span class="badge badge-pending">MPI</span>' : '';
        gmxDesc = `<strong>${escapeHtml(gmx.version || 'GROMACS')}</strong> ${cudaTag} ${mpiTag}`;
      }

      rowsHtml += `
        <tr>
          <td>
            <strong>${escapeHtml(node.name)}</strong><br>
            <small style="color: var(--text-muted)">${escapeHtml(node.node_id)}</small>
          </td>
          <td>${statusBadge}</td>
          <td>
            <div>${escapeHtml(node.ip_address || '-')}</div>
            <small style="color: var(--text-muted)">${escapeHtml(node.hostname)} (${escapeHtml(node.os)})</small>
          </td>
          <td>
            <div>${hw.cpu_count_logical || 1} 核 (${hw.cpu_percent || 0}%)</div>
            <div class="progress-bar-container">
              <div class="progress-bar ${hw.cpu_percent > 85 ? 'high' : ''}" style="width: ${Math.min(100, hw.cpu_percent || 0)}%"></div>
            </div>
          </td>
          <td>
            <div>${Math.round((hw.ram_total_mb || 0) / 1024)}GB (${hw.ram_percent || 0}%)</div>
            <div class="progress-bar-container">
              <div class="progress-bar ${hw.ram_percent > 85 ? 'high' : ''}" style="width: ${Math.min(100, hw.ram_percent || 0)}%"></div>
            </div>
          </td>
          <td>${gpuDesc}</td>
          <td>${gmxDesc}</td>
          <td>${formatTime(node.last_seen)}</td>
          <td>
            <button class="btn btn-secondary btn-sm" onclick="openDispatchModal('${escapeHtml(node.node_id)}', '${escapeHtml(node.node_id)}')">分发作业</button>
            <button class="btn btn-danger btn-sm" onclick="deleteNode('${escapeHtml(node.node_id)}')">注销</button>
          </td>
        </tr>`;
    }
    tbody.innerHTML = rowsHtml;
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-state" style="color: var(--accent-red)">加载失败: ${escapeHtml(e.message)}</td></tr>`;
  }
}

async function deleteNode(nodeId) {
  if (!confirm(`确定要注销并移除节点 ${nodeId} 吗？`)) return;
  try {
    const res = await apiRequest(`/api/v1/nodes/${nodeId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('注销失败');
    alert('节点已成功移除');
    loadNodes();
    loadOverview();
  } catch (e) {
    alert(e.message);
  }
}

// ---------------- 作业调度 ----------------
async function loadJobs() {
  const tbody = document.getElementById('jobs-table-body');
  try {
    const res = await apiRequest('/api/v1/jobs');
    if (!res.ok) throw new Error('无法获取作业列表');
    const jobs = await res.json();

    if (!jobs || jobs.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" class="empty-state">
            <div class="empty-icon">🧪</div>
            <div>暂无计算作业，点击右上角【新建作业】向在线节点派发 GROMACS 任务</div>
          </td>
        </tr>`;
      return;
    }

    let rowsHtml = '';
    for (const j of jobs) {
      let statusBadge = '';
      if (j.status === 'completed') statusBadge = '<span class="badge badge-completed">完成</span>';
      else if (j.status === 'running') statusBadge = '<span class="badge badge-busy">运行中</span>';
      else if (j.status === 'failed') statusBadge = '<span class="badge badge-failed">失败</span>';
      else statusBadge = '<span class="badge badge-pending">排队中</span>';

      const argsStr = (j.arguments || []).map(a => escapeHtml(a)).join(' ');
      const fullCmd = `gmx ${escapeHtml(j.subcommand)} ${argsStr}`;

      rowsHtml += `
        <tr>
          <td><code>${escapeHtml(j.job_id)}</code></td>
          <td>${escapeHtml(j.node_name || j.node_id)}</td>
          <td><code>${fullCmd}</code></td>
          <td>${statusBadge}</td>
          <td>${formatTime(j.created_at)}</td>
          <td>${j.completed_at ? formatTime(j.completed_at) : '-'}</td>
          <td>
            <button class="btn btn-secondary btn-sm" onclick="viewJobLog('${escapeHtml(j.job_id)}')">查看详情/日志</button>
          </td>
        </tr>`;
    }
    tbody.innerHTML = rowsHtml;
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state" style="color: var(--accent-red)">加载失败: ${escapeHtml(e.message)}</td></tr>`;
  }
}

async function viewJobLog(jobId) {
  try {
    const res = await apiRequest(`/api/v1/jobs/${jobId}`);
    if (!res.ok) throw new Error('读取作业详情失败');
    const job = await res.json();

    document.getElementById('log-modal-title').textContent = `作业日志: ${job.job_id}`;
    document.getElementById('log-modal-status').textContent = `状态: ${job.status} (Exit Code: ${job.exit_code !== null ? job.exit_code : 'N/A'})`;

    const outElem = document.getElementById('log-terminal-output');
    let content = `=== 标准输出 (STDOUT) ===\n${job.stdout || '(无输出)'}\n\n=== 错误输出 (STDERR) ===\n${job.stderr || '(无输出)'}`;
    if (job.error_message) {
      content += `\n\n=== 异常报告 ===\n${job.error_message}`;
    }
    outElem.textContent = content; // 安全纯文本，绝不以 HTML 执行
    document.getElementById('log-modal').classList.add('show');
  } catch (e) {
    alert(e.message);
  }
}

function closeJobLogModal() {
  document.getElementById('log-modal').classList.remove('show');
}

function openDispatchModal(nodeId = '', nodeName = '') {
  document.getElementById('dispatch-modal').classList.add('show');
  const nodeInput = document.getElementById('job-node-id');
  if (nodeId) {
    nodeInput.value = nodeId;
  }
}

function closeDispatchModal() {
  document.getElementById('dispatch-modal').classList.remove('show');
}

async function submitDispatchJob() {
  const nodeId = document.getElementById('job-node-id').value.trim();
  const subcmd = document.getElementById('job-subcommand').value.trim();
  const rawArgs = document.getElementById('job-arguments').value.trim();
  const timeoutSec = parseInt(document.getElementById('job-timeout').value.trim() || '300', 10);
  const desc = document.getElementById('job-description').value.trim();

  if (!nodeId) return alert('请输入目标节点 ID');
  if (!subcmd) return alert('请选择 GROMACS 子命令');

  // 将参数安全分词，避免空项
  const args = rawArgs ? rawArgs.split(/\s+/).filter(Boolean) : [];

  try {
    const res = await apiRequest('/api/v1/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        node_id: nodeId,
        subcommand: subcmd,
        arguments: args,
        timeout_seconds: timeoutSec,
        description: desc
      })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || '作业分发失败');
    }
    const created = await res.json();
    alert(`作业 ${created.job_id} 已成功分发至节点队列！`);
    closeDispatchModal();
    loadJobs();
    loadOverview();
  } catch (e) {
    alert(e.message);
  }
}

// ---------------- 邀请码管理 ----------------
async function loadInvites() {
  const tbody = document.getElementById('invites-table-body');
  try {
    const res = await apiRequest('/api/v1/invites');
    if (!res.ok) throw new Error('无法读取邀请码列表');
    const list = await res.json();

    if (!list || list.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" class="empty-state">
            <div class="empty-icon">🎟️</div>
            <div>暂无邀请码记录，点击上方【生成新邀请码】授权节点入网</div>
          </td>
        </tr>`;
      return;
    }

    let rowsHtml = '';
    for (const inv of list) {
      let statusBadge = '';
      if (inv.is_used) statusBadge = '<span class="badge badge-completed">已使用</span>';
      else if (inv.is_expired) statusBadge = '<span class="badge badge-offline">已过期</span>';
      else statusBadge = '<span class="badge badge-online">有效待用</span>';

      rowsHtml += `
        <tr>
          <td><code>${escapeHtml(inv.code_prefix)}</code></td>
          <td>${statusBadge}</td>
          <td>${formatTime(inv.created_at)}</td>
          <td>${formatTime(inv.expires_at)}</td>
          <td>${inv.used_by_node_id ? escapeHtml(inv.used_by_node_id) : '-'}</td>
          <td>${escapeHtml(inv.note || '-')}</td>
          <td>
            ${(!inv.is_used && !inv.is_expired) ? `<button class="btn btn-danger btn-sm" onclick="revokeInvite(${inv.id})">撤销</button>` : '-'}
          </td>
        </tr>`;
    }
    tbody.innerHTML = rowsHtml;
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state" style="color: var(--accent-red)">加载失败: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function openCreateInviteModal() {
  document.getElementById('invite-modal').classList.add('show');
}

function closeCreateInviteModal() {
  document.getElementById('invite-modal').classList.remove('show');
}

async function submitCreateInvite() {
  const hours = parseInt(document.getElementById('invite-hours').value.trim() || '24', 10);
  const note = document.getElementById('invite-note').value.trim();

  try {
    const res = await apiRequest('/api/v1/invites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expires_in_hours: hours, note: note })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || '创建邀请码失败');
    }
    const data = await res.json();
    closeCreateInviteModal();
    loadInvites();

    // 弹出一次性明文复制窗口
    document.getElementById('show-invite-code').value = data.invite_code;
    document.getElementById('invite-result-modal').classList.add('show');
  } catch (e) {
    alert(e.message);
  }
}

function closeInviteResultModal() {
  document.getElementById('invite-result-modal').classList.remove('show');
}

function copyInviteCode() {
  const elem = document.getElementById('show-invite-code');
  elem.select();
  document.execCommand('copy');
  alert('邀请码已复制到剪贴板！');
}

async function revokeInvite(inviteId) {
  if (!confirm('确定要撤销该邀请码吗？')) return;
  try {
    const res = await apiRequest(`/api/v1/invites/${inviteId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('撤销失败');
    loadInvites();
  } catch (e) {
    alert(e.message);
  }
}

// ---------------- 审计日志 ----------------
async function loadAuditLogs() {
  const tbody = document.getElementById('audit-table-body');
  try {
    const res = await apiRequest('/api/v1/audit-logs');
    if (!res.ok) throw new Error('无法获取审计日志');
    const logs = await res.json();

    if (!logs || logs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty-state">暂无安全审计日志</td></tr>';
      return;
    }

    let rowsHtml = '';
    for (const l of logs) {
      rowsHtml += `
        <tr>
          <td>${formatTime(l.timestamp)}</td>
          <td><code>${escapeHtml(l.actor)}</code></td>
          <td><strong>${escapeHtml(l.action)}</strong></td>
          <td>${escapeHtml(l.details || '-')}</td>
        </tr>`;
    }
    tbody.innerHTML = rowsHtml;
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty-state" style="color: var(--accent-red)">加载失败: ${escapeHtml(e.message)}</td></tr>`;
  }
}
