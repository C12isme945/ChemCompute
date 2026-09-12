// ChemCompute Web Console Script with Multi-Tier Hot Updates & Canary Rollout

let currentLogJobId = null;
let logPollInterval = null;
let currentView = 'grid';

function switchView(viewName) {
  currentView = viewName;
  const gridView = document.getElementById('view-grid');
  const updatesView = document.getElementById('view-updates');
  const tabGrid = document.getElementById('tab-grid');
  const tabUpdates = document.getElementById('tab-updates');

  if (viewName === 'grid') {
    gridView.classList.remove('hidden');
    updatesView.classList.add('hidden');
    tabGrid.className = "px-3.5 py-1.5 rounded-md font-medium bg-cyan-600 text-white shadow transition";
    tabUpdates.className = "px-3.5 py-1.5 rounded-md font-medium text-slate-400 hover:text-white transition flex items-center space-x-1";
  } else {
    gridView.classList.add('hidden');
    updatesView.classList.remove('hidden');
    tabGrid.className = "px-3.5 py-1.5 rounded-md font-medium text-slate-400 hover:text-white transition";
    tabUpdates.className = "px-3.5 py-1.5 rounded-md font-medium bg-cyan-600 text-white shadow transition flex items-center space-x-1";
    fetchReleases();
  }
}

async function fetchClusterStats() {
  try {
    const res = await fetch('/api/cluster/stats');
    if (res.ok) {
      const data = await res.json();
      document.getElementById('stat-nodes').innerText = `${data.online_nodes} / ${data.total_nodes}`;
      document.getElementById('stat-running').innerText = data.running_jobs;
      document.getElementById('stat-completed').innerText = data.completed_jobs;
      document.getElementById('node-count-badge').innerText = `${data.total_nodes} 台`;
    }
  } catch (e) {
    console.error("Failed to fetch stats", e);
  }
}

async function fetchNodes() {
  try {
    const res = await fetch('/api/nodes');
    if (!res.ok) return;
    const nodes = await res.json();
    const grid = document.getElementById('nodes-grid');
    const matrixBody = document.getElementById('node-matrix-tbody');
    grid.innerHTML = '';
    if (matrixBody) matrixBody.innerHTML = '';

    if (nodes.length === 0) {
      grid.innerHTML = `
        <div class="col-span-full p-8 rounded-xl border border-dashed border-slate-800 text-center text-slate-500">
          <p class="text-sm">暂无注册算力节点。点击右上角“邀请码”生成加入码，并在目标电脑运行 ChemCompute Agent。</p>
        </div>
      `;
      if (matrixBody) {
        matrixBody.innerHTML = `
          <tr>
            <td colspan="6" class="px-6 py-8 text-center text-slate-500 text-xs">
              暂无计算节点。节点接入后将在此展示版本分布与定向推送控制。
            </td>
          </tr>
        `;
      }
      return;
    }

    nodes.forEach(node => {
      const isOnline = node.status !== 'offline';
      const statusColor = node.status === 'idle' ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30' :
                          node.status === 'busy' ? 'text-amber-400 bg-amber-500/10 border-amber-500/30' :
                          'text-rose-400 bg-rose-500/10 border-rose-500/30';
      const statusText = node.status === 'idle' ? '🟢 空闲 (Idle)' :
                         node.status === 'busy' ? '🟡 计算中 (Busy)' : '🔴 离线 (Offline)';

      const gpu = node.telemetry.gpu;
      const cpu = node.telemetry.cpu;
      const ram = node.telemetry.ram;

      // GPU Metric block
      let gpuHtml = '';
      if (gpu) {
        const vramUsed = Math.round(gpu.memory_used_mb);
        const vramTotal = Math.round(gpu.memory_total_mb);
        const vramPct = vramTotal > 0 ? Math.round((vramUsed / vramTotal) * 100) : 0;
        gpuHtml = `
          <div class="p-2.5 rounded-lg bg-slate-900/80 border border-slate-800 space-y-1.5">
            <div class="flex items-center justify-between text-xs">
              <span class="text-slate-300 font-medium truncate" title="${gpu.name}">${gpu.name}</span>
              <span class="text-[11px] text-cyan-400">${gpu.temperature_c ? gpu.temperature_c + '°C' : ''}</span>
            </div>
            <div class="flex items-center justify-between text-[11px] text-slate-400">
              <span>显存: ${vramUsed} / ${vramTotal} MB</span>
              <span>负载: ${gpu.utilization_gpu_pct}%</span>
            </div>
            <div class="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
              <div class="bg-cyan-500 h-1.5 rounded-full transition-all duration-500" style="width: ${vramPct}%"></div>
            </div>
          </div>
        `;
      } else {
        gpuHtml = `
          <div class="p-2.5 rounded-lg bg-slate-900/40 border border-slate-800/60 text-xs text-slate-500 flex items-center justify-between">
            <span>GPU: 无独立显卡 (仅 CPU 计算)</span>
          </div>
        `;
      }

      // Software Badges
      const gmx = node.software.gromacs;
      const gmxBadge = gmx && gmx.available ?
        `<span class="px-2 py-0.5 rounded text-[10px] font-medium bg-cyan-900/40 text-cyan-300 border border-cyan-700/50">GROMACS ${gmx.version}${gmx.is_wsl ? ' (WSL)' : ''}</span>` :
        `<span class="px-2 py-0.5 rounded text-[10px] font-medium bg-slate-800 text-slate-500">GROMACS ×</span>`;

      const orcaBadge = node.software.orca && node.software.orca.available ?
        `<span class="px-2 py-0.5 rounded text-[10px] font-medium bg-indigo-900/40 text-indigo-300 border border-indigo-700/50">ORCA</span>` :
        `<span class="px-2 py-0.5 rounded text-[10px] font-medium bg-slate-800 text-slate-500">ORCA ×</span>`;

      const comsolBadge = node.software.comsol && node.software.comsol.available ?
        `<span class="px-2 py-0.5 rounded text-[10px] font-medium bg-blue-900/40 text-blue-300 border border-blue-700/50">COMSOL</span>` :
        `<span class="px-2 py-0.5 rounded text-[10px] font-medium bg-slate-800 text-slate-500">COMSOL ×</span>`;

      const channelBadge = node.channel === 'canary' ? '<span class="px-1.5 py-0.5 rounded text-[10px] bg-amber-500/10 text-amber-400 border border-amber-500/30">Canary</span>' :
                           node.channel === 'beta' ? '<span class="px-1.5 py-0.5 rounded text-[10px] bg-blue-500/10 text-blue-400 border border-blue-500/30">Beta</span>' :
                           '<span class="px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">Stable</span>';

      const updateBtn = node.update_status === 'update_available' ?
        `<button onclick="pushNodeUpdate('${node.node_id}')" class="px-2 py-0.5 rounded text-[11px] bg-cyan-600 hover:bg-cyan-500 text-white animate-pulse">升级</button>` : '';

      const card = `
        <div class="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg space-y-4 hover:border-slate-700 transition">
          <div class="flex items-start justify-between">
            <div>
              <div class="flex items-center space-x-2">
                <h3 class="font-semibold text-white text-base">${node.node_name}</h3>
                <span class="font-mono text-xs text-slate-400">v${node.agent_version || '0.1.0'}</span>
                ${channelBadge}
              </div>
              <p class="text-xs text-slate-400 mt-0.5 font-mono">${node.ip_address} · ${node.node_id}</p>
            </div>
            <div class="flex flex-col items-end space-y-1">
              <span class="px-2.5 py-1 rounded-full text-xs font-medium border ${statusColor}">
                ${statusText}
              </span>
              ${updateBtn}
            </div>
          </div>

          <!-- Hardware telemetry -->
          <div class="space-y-2">
            ${gpuHtml}

            <!-- CPU & RAM -->
            <div class="grid grid-cols-2 gap-2 text-xs">
              <div class="p-2.5 rounded-lg bg-slate-900/80 border border-slate-800 space-y-1">
                <div class="flex justify-between text-slate-400 text-[11px]">
                  <span>CPU: ${cpu.logical_cores} 核</span>
                  <span>${cpu.utilization_pct}%</span>
                </div>
                <div class="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                  <div class="bg-indigo-500 h-1.5 rounded-full transition-all" style="width: ${cpu.utilization_pct}%"></div>
                </div>
              </div>

              <div class="p-2.5 rounded-lg bg-slate-900/80 border border-slate-800 space-y-1">
                <div class="flex justify-between text-slate-400 text-[11px]">
                  <span>内存: ${Math.round(ram.used_pct)}%</span>
                  <span>${Math.round(ram.total_mb / 1024)}G</span>
                </div>
                <div class="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                  <div class="bg-blue-500 h-1.5 rounded-full transition-all" style="width: ${ram.used_pct}%"></div>
                </div>
              </div>
            </div>
          </div>

          <!-- Adapters list -->
          <div class="pt-2 border-t border-slate-800/80 flex flex-wrap gap-1.5 items-center">
            <span class="text-[11px] text-slate-400 mr-1">支持引擎:</span>
            ${gmxBadge}
            ${orcaBadge}
            ${comsolBadge}
          </div>
        </div>
      `;
      grid.innerHTML += card;

      // Populate Rollout Matrix Table in Updates View
      if (matrixBody) {
        const updateStatusBadge = node.update_status === 'update_available' ?
          '<span class="px-2 py-0.5 rounded text-xs bg-amber-500/10 text-amber-400 border border-amber-500/30 animate-pulse">新版本可用</span>' :
          node.update_status === 'updating' ?
          '<span class="px-2 py-0.5 rounded text-xs bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">更新中...</span>' :
          '<span class="px-2 py-0.5 rounded text-xs bg-slate-800 text-slate-400 border border-slate-700">已是最新</span>';

        const matrixRow = `
          <tr class="hover:bg-slate-800/40 transition">
            <td class="px-5 py-3.5">
              <div class="font-medium text-white">${node.node_name}</div>
              <div class="text-[11px] text-slate-500 font-mono">${node.node_id}</div>
            </td>
            <td class="px-4 py-3.5 font-mono text-xs text-slate-300">v${node.agent_version || '0.1.0'}</td>
            <td class="px-4 py-3.5">
              <select onchange="changeNodeChannel('${node.node_id}', this.value)" class="px-2.5 py-1 rounded bg-slate-800 border border-slate-700 text-xs text-slate-200 focus:outline-none focus:border-cyan-500">
                <option value="canary" ${node.channel === 'canary' ? 'selected' : ''}>Canary (金丝雀)</option>
                <option value="beta" ${node.channel === 'beta' ? 'selected' : ''}>Beta (灰度)</option>
                <option value="stable" ${node.channel === 'stable' ? 'selected' : ''}>Stable (稳定)</option>
              </select>
            </td>
            <td class="px-4 py-3.5">${updateStatusBadge}</td>
            <td class="px-4 py-3.5">
              <span class="px-2 py-0.5 rounded-full text-xs font-medium border ${statusColor}">
                ${node.status}
              </span>
            </td>
            <td class="px-5 py-3.5 text-right whitespace-nowrap">
              <button onclick="pushNodeUpdate('${node.node_id}')" class="px-3 py-1 rounded bg-cyan-600 hover:bg-cyan-500 text-white text-xs transition">
                推送更新
              </button>
            </td>
          </tr>
        `;
        matrixBody.innerHTML += matrixRow;
      }
    });
  } catch (e) {
    console.error("Failed to fetch nodes", e);
  }
}

async function fetchReleases() {
  try {
    const res = await fetch('/api/v1/releases');
    if (!res.ok) return;
    const data = await res.json();
    const releases = data.releases || [];
    const counts = data.channel_counts || { canary: 0, beta: 0, stable: 0 };

    document.getElementById('count-canary').innerText = `${counts.canary} 节点`;
    document.getElementById('count-beta').innerText = `${counts.beta} 节点`;
    document.getElementById('count-stable').innerText = `${counts.stable} 节点`;

    // Find latest versions per channel
    let latestCanary = '0.1.0', latestBeta = '0.1.0', latestStable = '0.1.0';
    releases.forEach(r => {
      if (r.component === 'agent') {
        if (r.channel === 'canary' && r.version > latestCanary) latestCanary = r.version;
        if (r.channel === 'beta' && r.version > latestBeta) latestBeta = r.version;
        if (r.channel === 'stable' && r.version > latestStable) latestStable = r.version;
      }
    });
    document.getElementById('latest-canary-ver').innerText = latestCanary;
    document.getElementById('latest-beta-ver').innerText = latestBeta;
    document.getElementById('latest-stable-ver').innerText = latestStable;

    const tbody = document.getElementById('releases-tbody');
    tbody.innerHTML = '';

    if (releases.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" class="px-6 py-8 text-center text-slate-500 text-xs">
            暂无已发布的版本包。点击上方“发布新版本”上传第一个 Agent 或 Adapter 软件包。
          </td>
        </tr>
      `;
      return;
    }

    releases.forEach(rel => {
      const isAgent = rel.component === 'agent';
      const compLabel = isAgent ?
        `<span class="px-2 py-0.5 rounded text-[11px] bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">Agent 核心</span>` :
        `<span class="px-2 py-0.5 rounded text-[11px] bg-indigo-500/10 text-indigo-400 border border-indigo-500/30">Adapter: ${rel.adapter_name}</span>`;

      const chanBadge = rel.channel === 'canary' ? '<span class="px-2 py-0.5 rounded text-xs bg-amber-500/10 text-amber-400 border border-amber-500/30">Canary</span>' :
                        rel.channel === 'beta' ? '<span class="px-2 py-0.5 rounded text-xs bg-blue-500/10 text-blue-400 border border-blue-500/30">Beta</span>' :
                        '<span class="px-2 py-0.5 rounded text-xs bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">Stable</span>';

      const releaseDate = new Date(rel.created_at * 1000).toLocaleString();
      const shaShort = rel.package.sha256 ? `${rel.package.sha256.substring(0, 8)}...` : 'N/A';
      const sizeKB = Math.round((rel.package.size_bytes || 0) / 1024);

      let promoteActions = '';
      if (rel.channel === 'canary') {
        promoteActions = `
          <button onclick="promoteRelease('${rel.version}', 'beta', '${rel.component}', '${rel.adapter_name || ''}')" class="px-2.5 py-1 rounded bg-blue-600 hover:bg-blue-500 text-white text-[11px] mr-1">推至 Beta</button>
        `;
      } else if (rel.channel === 'beta') {
        promoteActions = `
          <button onclick="promoteRelease('${rel.version}', 'stable', '${rel.component}', '${rel.adapter_name || ''}')" class="px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] mr-1">全量至 Stable</button>
        `;
      }

      const row = `
        <tr class="hover:bg-slate-800/40 transition">
          <td class="px-5 py-3.5">
            <div class="flex items-center space-x-2">
              <span class="font-mono font-bold text-white text-sm">v${rel.version}</span>
              ${compLabel}
            </div>
          </td>
          <td class="px-4 py-3.5">${chanBadge}</td>
          <td class="px-4 py-3.5 font-mono text-xs text-slate-400" title="${rel.package.sha256}">${shaShort}</td>
          <td class="px-4 py-3.5 text-xs text-slate-400 font-mono">${sizeKB} KB</td>
          <td class="px-4 py-3.5 text-xs text-slate-300 max-w-xs truncate" title="${rel.changelog || ''}">${rel.changelog || '无日志说明'}</td>
          <td class="px-4 py-3.5 text-xs text-slate-400">${releaseDate}</td>
          <td class="px-5 py-3.5 text-right whitespace-nowrap">
            ${promoteActions}
            <a href="/api/v1/releases/${rel.version}/download?component=${rel.component}" class="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px]">下载</a>
          </td>
        </tr>
      `;
      tbody.innerHTML += row;
    });
  } catch (e) {
    console.error("Failed to fetch releases", e);
  }
}

async function promoteRelease(version, toChannel, component, adapterName) {
  try {
    const res = await fetch(`/api/v1/releases/${version}/promote`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        to_channel: toChannel,
        component: component,
        adapter_name: adapterName || null
      })
    });
    if (res.ok) {
      await fetchReleases();
      alert(`版本 v${version} 已成功晋级至 ${toChannel} 通道！`);
    } else {
      alert("晋级失败: " + (await res.text()));
    }
  } catch (e) {
    alert("网络错误: " + e.message);
  }
}

async function changeNodeChannel(nodeId, newChannel) {
  try {
    const res = await fetch(`/api/v1/nodes/${nodeId}/channel`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ channel: newChannel })
    });
    if (res.ok) {
      await fetchNodes();
      await fetchReleases();
    }
  } catch (e) {}
}

async function pushNodeUpdate(nodeId) {
  try {
    const res = await fetch(`/api/v1/nodes/${nodeId}/update`, { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      alert(`已向节点 ${nodeId} 推送目标版本 v${data.target_version} 更新指令！`);
      await fetchNodes();
    } else {
      alert("推送更新失败: " + (await res.text()));
    }
  } catch (e) {
    alert("网络错误: " + e.message);
  }
}

async function pushUpdateAll() {
  if (!confirm("确认向所有可用灰度节点全量广播推送更新？")) return;
  try {
    const res = await fetch('/api/v1/update/push-all', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ component: 'agent' })
    });
    if (res.ok) {
      const data = await res.json();
      alert(`已成功通知 ${data.notified_nodes} 台节点准备热更新！`);
      await fetchNodes();
    }
  } catch (e) {
    alert("网络错误: " + e.message);
  }
}

// Release Modal
function openReleaseModal() {
  document.getElementById('release-modal').classList.remove('hidden');
}

function closeReleaseModal() {
  document.getElementById('release-modal').classList.add('hidden');
}

function toggleAdapterNameInput() {
  const comp = document.getElementById('rel-component').value;
  const container = document.getElementById('adapter-name-container');
  if (comp === 'adapter') {
    container.classList.remove('hidden');
    document.getElementById('rel-adapter-name').required = true;
  } else {
    container.classList.add('hidden');
    document.getElementById('rel-adapter-name').required = false;
  }
}

async function handleReleaseSubmit(e) {
  e.preventDefault();
  const comp = document.getElementById('rel-component').value;
  const adapterName = document.getElementById('rel-adapter-name').value;
  const version = document.getElementById('rel-version').value;
  const channel = document.getElementById('rel-channel').value;
  const changelog = document.getElementById('rel-changelog').value;
  const fileInput = document.getElementById('rel-file');

  if (fileInput.files.length === 0) {
    alert("请选择要发布的 .zip 压缩包文件！");
    return;
  }

  const meta = {
    version: version,
    component: comp,
    adapter_name: comp === 'adapter' ? adapterName : null,
    channel: channel,
    changelog: changelog,
    minimum_agent_version: "0.1.0"
  };

  const formData = new FormData();
  formData.append('metadata', JSON.stringify(meta));
  formData.append('package', fileInput.files[0]);

  const btn = document.getElementById('rel-submit-btn');
  btn.disabled = true;
  btn.innerText = "上传校验中...";

  try {
    const res = await fetch('/api/v1/releases', {
      method: 'POST',
      body: formData
    });
    if (res.ok) {
      closeReleaseModal();
      document.getElementById('release-form').reset();
      await fetchReleases();
      alert(`版本 v${version} 成功发布至 ${channel} 通道！`);
    } else {
      alert("发布失败: " + (await res.text()));
    }
  } catch (err) {
    alert("网络错误: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerText = "确认发布";
  }
}

async function fetchJobs() {
  try {
    const res = await fetch('/api/jobs');
    if (!res.ok) return;
    const jobs = await res.json();
    const tbody = document.getElementById('jobs-tbody');
    tbody.innerHTML = '';

    if (jobs.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" class="p-6">
            <div class="relative overflow-hidden rounded-2xl bg-gradient-to-br from-slate-900 via-slate-900/90 to-cyan-950/30 border border-slate-800 p-6 flex flex-col lg:flex-row items-center justify-between gap-8 shadow-2xl">
              <div class="space-y-4 max-w-lg">
                <div class="inline-flex items-center space-x-2 px-3 py-1 rounded-full text-xs font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">
                  <span class="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></span>
                  <span>计算网格待命 (Ready for Simulations)</span>
                </div>
                <h3 class="text-xl font-bold text-white tracking-tight">无人值守科学模拟调度就绪</h3>
                <p class="text-xs text-slate-400 leading-relaxed">
                  当前作业队列为空，所有接入的 GPU / CPU 节点均处于待命就绪状态。您可以点击下方按钮提交分子动力学 (GROMACS)、量子化学 (ORCA) 或多物理场仿真任务。
                </p>
                <div class="pt-2 flex items-center space-x-3">
                  <button onclick="openJobModal()" class="px-5 py-2.5 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-white text-xs font-semibold shadow-lg shadow-cyan-500/25 transition flex items-center space-x-2">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                    <span>新建第一个计算作业 (Submit First Job)</span>
                  </button>
                  <button onclick="openInviteModal()" class="px-4 py-2.5 rounded-xl bg-slate-800/80 hover:bg-slate-700 text-slate-200 border border-slate-700 text-xs transition flex items-center space-x-1.5">
                    <span>接入更多电脑</span>
                  </button>
                </div>
              </div>
              <div class="relative w-full lg:w-[460px] rounded-xl overflow-hidden border border-slate-800/80 shadow-2xl group">
                <img src="/static/hero_dashboard.jpg" alt="ChemCompute Dashboard Preview" class="w-full h-auto object-cover transform group-hover:scale-105 transition duration-700">
                <div class="absolute inset-0 bg-gradient-to-t from-slate-950/80 via-transparent to-transparent flex items-end p-4">
                  <span class="text-xs font-mono text-cyan-300 font-medium">ChemCompute Distributed Simulation Matrix</span>
                </div>
              </div>
            </div>
          </td>
        </tr>
      `;
      return;
    }

    jobs.forEach(job => {
      let statusBadge = '';
      if (job.status === 'completed') {
        statusBadge = '<span class="px-2 py-0.5 rounded text-xs bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">已完成</span>';
      } else if (job.status === 'running') {
        statusBadge = '<span class="px-2 py-0.5 rounded text-xs bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 animate-pulse">运行中</span>';
      } else if (job.status === 'assigned') {
        statusBadge = '<span class="px-2 py-0.5 rounded text-xs bg-blue-500/10 text-blue-400 border border-blue-500/30">已派发</span>';
      } else if (job.status === 'failed') {
        statusBadge = '<span class="px-2 py-0.5 rounded text-xs bg-rose-500/10 text-rose-400 border border-rose-500/30">失败</span>';
      } else {
        statusBadge = '<span class="px-2 py-0.5 rounded text-xs bg-slate-800 text-slate-400 border border-slate-700">排队中</span>';
      }

      const submitTime = new Date(job.submitted_at * 1000).toLocaleTimeString();
      const nodeDisplay = job.assigned_node_id || '<span class="text-slate-500">待调度</span>';

      let actions = `
        <button onclick="viewJobLog('${job.job_id}')" class="text-xs text-cyan-400 hover:text-cyan-300 mr-2">日志</button>
      `;

      if (job.result_archive) {
        actions += `
          <a href="/api/jobs/${job.job_id}/results" target="_blank" class="text-xs text-emerald-400 hover:text-emerald-300 font-medium">下载结果</a>
        `;
      }

      const row = `
        <tr class="hover:bg-slate-800/40 transition">
          <td class="px-5 py-3.5">
            <div class="font-medium text-white">${job.name}</div>
            <div class="text-[11px] text-slate-500 font-mono">${job.job_id}</div>
          </td>
          <td class="px-4 py-3.5 uppercase font-mono text-xs text-slate-300">${job.adapter}</td>
          <td class="px-4 py-3.5 font-mono text-xs text-slate-300">${nodeDisplay}</td>
          <td class="px-4 py-3.5">${statusBadge}</td>
          <td class="px-6 py-3.5">
            <div class="flex items-center space-x-2">
              <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                <div class="bg-cyan-500 h-2 rounded-full transition-all duration-300" style="width: ${job.progress_pct}%"></div>
              </div>
              <span class="text-xs text-slate-400 font-mono w-10 text-right">${Math.round(job.progress_pct)}%</span>
            </div>
          </td>
          <td class="px-4 py-3.5 text-xs text-slate-400">${submitTime}</td>
          <td class="px-5 py-3.5 text-right whitespace-nowrap">${actions}</td>
        </tr>
      `;
      tbody.innerHTML += row;
    });
  } catch (e) {
    console.error("Failed to fetch jobs", e);
  }
}

let currentAdminKey = '';

// Toast Notification
function showToast(text) {
  const toast = document.getElementById('toast');
  const toastText = document.getElementById('toast-text');
  if (!toast || !toastText) return;
  toastText.innerText = text;
  toast.classList.remove('opacity-0', 'translate-y-12', 'pointer-events-none');
  toast.classList.add('opacity-100', 'translate-y-0');
  setTimeout(() => {
    toast.classList.remove('opacity-100', 'translate-y-0');
    toast.classList.add('opacity-0', 'translate-y-12', 'pointer-events-none');
  }, 2500);
}

// Admin Key & Credentials Center
async function fetchActiveAdminKey() {
  try {
    const res = await fetch('/api/invite-codes/active');
    if (res.ok) {
      const data = await res.json();
      currentAdminKey = data.admin_key || data.invite_code;
      const navKeyEl = document.getElementById('nav-admin-key');
      const dispKeyEl = document.getElementById('display-admin-key');
      const dispCmdEl = document.getElementById('display-join-cmd');
      if (navKeyEl) navKeyEl.innerText = currentAdminKey;
      if (dispKeyEl) dispKeyEl.innerText = currentAdminKey;
      if (dispCmdEl) {
        dispCmdEl.innerText = `python scripts/start_agent.py --controller ${window.location.origin} --invite ${currentAdminKey}`;
      }
    }
  } catch (e) {
    console.error("Failed to fetch active admin key", e);
  }
}

function copyCurrentAdminKey() {
  if (!currentAdminKey) return;
  navigator.clipboard.writeText(currentAdminKey);
  showToast(`管理员密钥 ${currentAdminKey} 已复制到剪贴板！`);
}

function copyJoinCommand() {
  const cmd = `python scripts/start_agent.py --controller ${window.location.origin} --invite ${currentAdminKey}`;
  navigator.clipboard.writeText(cmd);
  showToast("节点一键加入命令已复制到剪贴板！");
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text);
  showToast(`邀请码 ${text} 已复制到剪贴板！`);
}

// Modal Handlers
function openJobModal() {
  document.getElementById('job-modal').classList.remove('hidden');
}

function closeJobModal() {
  document.getElementById('job-modal').classList.add('hidden');
}

function openInviteModal() {
  document.getElementById('invite-modal').classList.remove('hidden');
  fetchActiveAdminKey();
  loadInviteCodes();
}

function closeInviteModal() {
  document.getElementById('invite-modal').classList.add('hidden');
}

function openRolesModal() {
  document.getElementById('roles-modal').classList.remove('hidden');
}

function closeRolesModal() {
  document.getElementById('roles-modal').classList.add('hidden');
}

// Template Handling
function loadTemplate(templateId) {
  const templateIdInput = document.getElementById('job-template-id');
  const nameInput = document.getElementById('job-name');
  const adapterSelect = document.getElementById('job-adapter');
  const banner = document.getElementById('template-loaded-banner');
  const bannerText = document.getElementById('template-loaded-text');

  templateIdInput.value = templateId;

  if (templateId === 'gromacs-water-smoke') {
    nameInput.value = 'Water-Smoke-MD01';
    adapterSelect.value = 'gromacs';
    bannerText.innerText = '✓ 已载入 GROMACS 水分子动力学官方范本 (直接点击下方提交，无需准备压缩包)';
  } else if (templateId === 'orca-water-sp') {
    nameInput.value = 'Water-B3LYP-Opt';
    adapterSelect.value = 'orca';
    bannerText.innerText = '✓ 已载入 ORCA 水分子几何优化官方范本 (直接点击下方提交，无需准备压缩包)';
  }

  banner.classList.remove('hidden');
  showToast('官方计算范本已载入！可直接确认提交。');
}

function clearTemplate() {
  document.getElementById('job-template-id').value = '';
  document.getElementById('template-loaded-banner').classList.add('hidden');
  showToast('已取消范本选择');
}

let currentTemplateData = null;

async function previewTemplate(templateId) {
  try {
    const res = await fetch(`/api/templates/${templateId}`);
    if (!res.ok) return;
    currentTemplateData = await res.json();

    document.getElementById('preview-title').innerText = currentTemplateData.name;
    document.getElementById('preview-desc').innerText = currentTemplateData.description;

    const tabsContainer = document.getElementById('preview-file-tabs');
    tabsContainer.innerHTML = '';

    const files = Object.keys(currentTemplateData.file_previews || {});
    if (files.length > 0) {
      files.forEach((file, index) => {
        const activeClass = index === 0 ? 'bg-cyan-600 text-white' : 'bg-slate-800 text-slate-400 hover:text-slate-200';
        tabsContainer.innerHTML += `
          <button type="button" onclick="selectPreviewFile('${file}')" class="px-2.5 py-1 rounded text-xs font-mono ${activeClass}" id="tab-file-${file}">
            ${file}
          </button>
        `;
      });
      selectPreviewFile(files[0]);
    }

    document.getElementById('preview-modal').classList.remove('hidden');
  } catch (e) {
    alert("获取范本信息失败: " + e.message);
  }
}

function selectPreviewFile(filename) {
  if (!currentTemplateData || !currentTemplateData.file_previews) return;
  const content = currentTemplateData.file_previews[filename] || '';
  document.getElementById('preview-code-content').innerText = content;

  const files = Object.keys(currentTemplateData.file_previews);
  files.forEach(f => {
    const btn = document.getElementById(`tab-file-${f}`);
    if (btn) {
      if (f === filename) {
        btn.className = 'px-2.5 py-1 rounded text-xs font-mono bg-cyan-600 text-white';
      } else {
        btn.className = 'px-2.5 py-1 rounded text-xs font-mono bg-slate-800 text-slate-400 hover:text-slate-200';
      }
    }
  });
}

function closePreviewModal() {
  document.getElementById('preview-modal').classList.add('hidden');
}

async function loadInviteCodes() {
  try {
    const res = await fetch('/api/invite-codes');
    const codes = await res.json();
    const list = document.getElementById('invite-list');
    list.innerHTML = '';
    if (codes.length === 0) {
      list.innerHTML = '<p class="text-xs text-slate-500">暂无历史邀请码，点击上方按钮可生成。</p>';
      return;
    }
    codes.forEach(c => {
      list.innerHTML += `
        <div class="p-2 rounded-lg bg-slate-800/80 border border-slate-700 flex items-center justify-between text-xs">
          <div>
            <span class="font-mono font-bold text-amber-300 tracking-wider">${c.code}</span>
            <span class="text-[11px] text-slate-400 ml-2">(${c.uses_count}/${c.max_uses} 次使用)</span>
          </div>
          <button onclick="copyToClipboard('${c.code}')" class="px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-200 text-[11px]">复制</button>
        </div>
      `;
    });
  } catch (e) {}
}

async function createInviteCode() {
  try {
    const res = await fetch('/api/invite-codes', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ max_uses: 20, expires_in_days: 7 })
    });
    if (res.ok) {
      await fetchActiveAdminKey();
      await loadInviteCodes();
      showToast("已成功生成新管理员密钥！");
    }
  } catch (e) {}
}

async function handleJobSubmit(e) {
  e.preventDefault();
  const name = document.getElementById('job-name').value;
  const adapter = document.getElementById('job-adapter').value;
  const requireGpu = document.getElementById('job-require-gpu').checked;
  const templateId = document.getElementById('job-template-id').value;
  const bundleInput = document.getElementById('job-bundle');

  const metadata = {
    name: name,
    adapter: adapter,
    requirements: {
      software: adapter,
      require_gpu: requireGpu,
      min_vram_mb: requireGpu ? 2048 : 0,
      min_ram_mb: 2048
    },
    parameters: {
      use_gpu: requireGpu,
      template_id: templateId || undefined
    },
    priority: 1
  };

  const formData = new FormData();
  formData.append('metadata', JSON.stringify(metadata));

  if (bundleInput.files.length > 0) {
    formData.append('bundle', bundleInput.files[0]);
  }

  const btn = document.getElementById('job-submit-btn');
  btn.disabled = true;
  btn.innerText = "提交中...";

  try {
    const res = await fetch('/api/jobs', {
      method: 'POST',
      body: formData
    });
    if (res.ok) {
      closeJobModal();
      document.getElementById('job-form').reset();
      clearTemplate();
      showToast("计算作业提交成功！已加入调度流水线。");
      await fetchJobs();
      await fetchClusterStats();
    } else {
      alert("提交作业失败: " + (await res.text()));
    }
  } catch (err) {
    alert("网络错误: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerText = "确认提交作业";
  }
}

async function viewJobLog(jobId) {
  currentLogJobId = jobId;
  document.getElementById('log-title').innerText = `作业日志 (${jobId})`;
  document.getElementById('log-modal').classList.remove('hidden');
  await refreshJobLog();

  if (logPollInterval) clearInterval(logPollInterval);
  logPollInterval = setInterval(refreshJobLog, 2000);
}

async function refreshJobLog() {
  if (!currentLogJobId) return;
  try {
    const res = await fetch(`/api/jobs/${currentLogJobId}`);
    if (res.ok) {
      const job = await res.json();
      const content = job.log_tail || "暂无日志输出...";
      const logEl = document.getElementById('log-content');
      logEl.innerText = content;
      logEl.scrollTop = logEl.scrollHeight;

      if (job.status === 'completed' || job.status === 'failed') {
        if (logPollInterval) clearInterval(logPollInterval);
      }
    }
  } catch (e) {}
}

function closeLogModal() {
  document.getElementById('log-modal').classList.add('hidden');
  if (logPollInterval) clearInterval(logPollInterval);
  currentLogJobId = null;
}

function refreshData() {
  fetchActiveAdminKey();
  fetchClusterStats();
  fetchNodes();
  fetchJobs();
  if (currentView === 'updates') {
    fetchReleases();
  }
}

// Initial fetch and periodic polling
fetchActiveAdminKey();
refreshData();
setInterval(refreshData, 3000);
