"""Task submission, package transfer, enrollment, and AI controls in native tabs."""
from __future__ import annotations

import json
import shlex
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from chemcompute import deepseek
from chemcompute import desktop_client as api
from chemcompute.common.config import NodeConfig, load_yaml_config, save_yaml_config
from chemcompute.packages import inspect_package
from chemcompute.secrets_store import read_credentials, save_credentials
from chemcompute.tasks import TaskSpec

PRESETS = {
    'Gaussian 输入文件': [{'subcommand': 'run', 'arguments': ['input.gjf']}],
    '版本与环境检查': [{'subcommand': 'version', 'arguments': []}],
    '预处理 + 分子动力学': [
        {'subcommand': 'grompp', 'arguments': ['-f', 'run.mdp', '-c', 'conf.gro', '-p', 'topol.top', '-o', 'run.tpr']},
        {'subcommand': 'mdrun', 'arguments': ['-s', 'run.tpr', '-deffnm', 'run', '-nt', '1']}],
    '运行已有 TPR': [{'subcommand': 'mdrun', 'arguments': ['-s', 'run.tpr', '-deffnm', 'run', '-nt', '1']}],
    '结构校验': [{'subcommand': 'check', 'arguments': ['-f', 'conf.gro']}],
}


class Workspace:
    def __init__(self, console, notebook):
        self.console = console
        self.root = console.root
        self.package = None
        self.nodes = []
        self.tasks = {}
        self.invites = {}
        self.model = deepseek.DEFAULT_MODEL
        self.task_tab = self.tab(notebook, '任务中心')
        self.submit_tab = self.tab(notebook, '提交计算包')
        self.invite_tab = self.tab(notebook, '节点与邀请码')
        self.settings_tab = self.tab(notebook, '连接与 AI 设置')
        self.deploy_tab = self.tab(notebook, '依赖安装')
        self.update_tab = self.tab(notebook, '在线更新')
        self.build_tasks()
        self.build_submit()
        self.build_invites()
        self.build_settings()
        self.build_deployment()
        self.build_updates()

    def tab(self, notebook, name):
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text=name)
        return frame

    def run(self, function, callback=None):
        def complete(value, error):
            self.console.notice.set(error or '操作完成。')
            if not error and callback:
                callback(value)
        self.console.notice.set('正在处理…')
        self.console.worker(complete, function)

    def field(self, frame, label, default='', width=22, secret=False):
        box = ttk.Frame(frame)
        box.pack(side='left', padx=(0, 10), pady=5)
        ttk.Label(box, text=label).pack(anchor='w')
        variable = tk.StringVar(value=default)
        ttk.Entry(box, textvariable=variable, width=width, show='*' if secret else '').pack()
        return variable

    def show_text(self, title, text):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry('800x520')
        area = tk.Text(window, wrap='word', font=('Microsoft YaHei UI', 10))
        area.pack(fill='both', expand=True, padx=10, pady=10)
        area.insert('1.0', text)
        area.configure(state='disabled')

    def build_tasks(self):
        row = ttk.Frame(self.task_tab)
        row.pack(fill='x')
        for label, function in [('刷新任务', self.refresh_tasks), ('日志与详情', self.task_log),
                                ('取消任务', self.cancel_task), ('复制重试', self.retry_task), ('下载结果 ZIP', self.download)]:
            ttk.Button(row, text=label, command=function).pack(side='left', padx=4)
        self.task_tree = ttk.Treeview(self.task_tab, columns=('name', 'status', 'progress', 'node'), show='headings')
        for name, text in [('name', '任务'), ('status', '状态'), ('progress', '步骤进度'), ('node', '执行节点')]:
            self.task_tree.heading(name, text=text)
        self.task_tree.pack(fill='both', expand=True, pady=10)
        ttk.Label(self.task_tab, text='失联后自动重连并补传结果；GROMACS 可在原节点从检查点恢复。不会把失联任务重复派给其他节点。').pack(anchor='w')

    def refresh_tasks(self):
        def display(values):
            self.tasks = {v['id']: v for v in values}
            selected = self.task_tree.selection()
            self.task_tree.delete(*self.task_tree.get_children())
            states = {'queued': '排队', 'running': '运行中', 'completed': '完成', 'failed': '失败', 'cancelled': '已取消', 'recovering': '失联恢复等待'}
            for value in values:
                self.task_tree.insert('', 'end', iid=value['id'], values=(value['spec']['name'], states.get(value['status'], value['status']), str(value['progress']) + '%', value['node'] or '等待匹配'))
            if selected and selected[0] in self.tasks:
                self.task_tree.selection_set(selected[0])
        self.run(lambda: api.call('GET', '/api/v2/tasks'), display)

    def selected_task(self):
        selected = self.task_tree.selection()
        if not selected:
            self.console.notice.set('请先选择任务。')
            return None
        return selected[0]

    def task_log(self):
        identity = self.selected_task()
        if identity:
            self.run(lambda: api.call('GET', '/api/v2/tasks/' + identity), lambda value: self.show_text('任务详情与日志', json.dumps(value, ensure_ascii=False, indent=2)))

    def cancel_task(self):
        identity = self.selected_task()
        if identity and messagebox.askyesno('取消任务', '向节点发送取消请求？已完成的步骤产物会留在节点本机。', parent=self.root):
            self.run(lambda: api.call('POST', f'/api/v2/tasks/{identity}/cancel'), lambda _: self.refresh_tasks())

    def retry_task(self):
        identity = self.selected_task()
        if identity:
            self.run(lambda: api.call('POST', f'/api/v2/tasks/{identity}/retry'), lambda _: self.refresh_tasks())

    def download(self):
        identity = self.selected_task()
        if identity:
            path = filedialog.asksaveasfilename(parent=self.root, defaultextension='.zip', initialfile=identity + '-results.zip')
            if path:
                self.run(lambda: api.download_result(identity, path), lambda value: self.console.notice.set('已校验 SHA-256 并保存：' + value))

    def build_submit(self):
        row = ttk.Frame(self.submit_tab)
        row.pack(fill='x')
        self.name = self.field(row, '任务名称', '新的计算任务', 24)
        self.software = ttk.Combobox(row, values=['gromacs', 'gaussian'], width=10, state='readonly')
        self.software.set('gromacs')
        self.software.pack(side='left', padx=5)
        self.preset = ttk.Combobox(row, values=list(PRESETS), state='readonly', width=24)
        self.preset.set('版本与环境检查')
        self.preset.pack(side='left', pady=10)
        self.preset.bind('<<ComboboxSelected>>', lambda _: self.choose_preset())
        ttk.Button(row, text='选择并上传 ZIP', command=self.upload).pack(side='left', padx=8)
        self.package_label = tk.StringVar(value='尚未上传计算包（版本检查可不上传）')
        ttk.Label(self.submit_tab, textvariable=self.package_label).pack(anchor='w')
        row = ttk.Frame(self.submit_tab)
        row.pack(fill='x')
        self.cpu = self.field(row, 'CPU 核数', '1', 8)
        self.ram = self.field(row, '最低可用内存 MB', '256', 12)
        self.timeout = self.field(row, '超时 / 秒', '3600', 10)
        self.priority = self.field(row, '优先级 0–10', '0', 10)
        self.replicas = self.field(row, '跨机副本数', '1', 8)
        self.gpu = tk.BooleanVar()
        ttk.Checkbutton(row, text='要求 CUDA GPU', variable=self.gpu).pack(side='left', padx=8)
        row = ttk.Frame(self.submit_tab)
        row.pack(fill='x')
        ttk.Label(row, text='执行节点').pack(side='left')
        self.node = ttk.Combobox(row, values=['自动匹配'], width=45, state='readonly')
        self.node.set('自动匹配')
        self.node.pack(side='left', padx=8)
        ttk.Button(row, text='刷新节点', command=self.refresh_nodes).pack(side='left')
        self.description = tk.StringVar()
        ttk.Entry(self.submit_tab, textvariable=self.description).pack(fill='x', pady=5)
        ttk.Label(self.submit_tab, text='任务说明（可留空）；下方编辑步骤 JSON，仅允许受限 GROMACS / Gaussian 命令，路径相对计算包根目录。').pack(anchor='w')
        row = ttk.Frame(self.submit_tab)
        row.pack(fill='x')
        self.step_command = ttk.Combobox(row, values=['version', 'check', 'grompp', 'mdrun', 'editconf', 'solvate', 'genion', 'energy', 'run'], width=12, state='readonly')
        self.step_command.set('mdrun')
        self.step_command.pack(side='left')
        self.step_args = self.field(row, '步骤参数（文件名有空格时加双引号）', '-s run.tpr -deffnm run -nt 1', 52)
        ttk.Button(row, text='追加步骤', command=self.add_step).pack(side='left', padx=8)
        self.steps = tk.Text(self.submit_tab, height=5, font=('Consolas', 10))
        self.steps.pack(fill='both', expand=True)
        self.set_steps(PRESETS['版本与环境检查'])
        row = ttk.Frame(self.submit_tab)
        row.pack(fill='x', pady=5)
        ttk.Button(row, text='DeepSeek 建议节点', command=self.ai_allocate).pack(side='left')
        ttk.Button(row, text='预览并提交', command=self.submit).pack(side='left', padx=8)
        ttk.Label(row, text='AI 发送说明、步骤、文件名和资源摘要；不发送计算包正文。').pack(side='left')

    def add_step(self):
        try:
            from chemcompute.tasks import Step
            steps = json.loads(self.steps.get('1.0', 'end'))
            step = Step(subcommand=self.step_command.get(), arguments=shlex.split(self.step_args.get()))
            if len(steps) >= 16:
                raise ValueError('一个任务最多 16 步。')
            self.set_steps([*steps, step.model_dump()])
        except Exception as exc:
            self.console.notice.set(str(exc))

    def set_steps(self, steps):
        self.steps.delete('1.0', 'end')
        self.steps.insert('1.0', json.dumps(steps, ensure_ascii=False, indent=2))

    def specification(self):
        selected = self.node.get()
        return TaskSpec(software=self.software.get(), name=self.name.get(), description=self.description.get(),
                        node_id='' if selected == '自动匹配' else selected.split(' | ')[0],
                        package_id=self.package['id'] if self.package else None,
                        steps=json.loads(self.steps.get('1.0', 'end')),
                        resources={'cpu': int(self.cpu.get()), 'ram_mb': int(self.ram.get()), 'gpu': self.gpu.get()},
                        timeout_seconds=int(self.timeout.get()), priority=int(self.priority.get()))

    def upload(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[('ZIP calculation package', '*.zip')])
        if not path:
            return
        def action():
            inspect_package(Path(path))
            return api.upload(path)
        def complete(value):
            self.package = value
            self.package_label.set(f"{Path(path).name} · {len(value['files'])} 个文件 · {value['size_bytes'] / 1024:.1f} KB")
            manifest = value.get('manifest') or {}
            self.software.set(manifest.get('software', 'gromacs'))
            if 'steps' in manifest:
                self.set_steps(manifest['steps'])
            if 'name' in manifest:
                self.name.set(str(manifest['name'])[:160])
            self.show_text('计算包已上传', '\n'.join(value['files'][:200]) + '\n\nSHA-256: ' + value['sha256'])
        self.run(action, complete)

    def refresh_nodes(self):
        def display(values):
            self.nodes = values
            self.node['values'] = ['自动匹配'] + [v['node_id'] + ' | ' + v['name'] for v in values]
        self.run(lambda: api.call('GET', '/api/v1/nodes'), display)

    def ai_allocate(self):
        try:
            spec = self.specification()
            count = int(self.replicas.get())
            if not 1 <= count <= 128:
                raise ValueError('副本数必须为 1–128。')
        except Exception as exc:
            self.console.notice.set(str(exc))
            return
        files = self.package['files'] if self.package else []
        model = self.model
        def action():
            nodes = api.call('GET', '/api/v1/nodes')
            return deepseek.recommend(spec, nodes, files, model)
        def display(value):
            self.node.set(value['node_id'])
            self.show_text('DeepSeek 分配建议（尚未提交）', value['reason'] + '\n\n节点：' + value['node_id'])
        self.run(action, display)

    def submit(self):
        try:
            spec = self.specification()
            count = int(self.replicas.get())
            if not 1 <= count <= 128:
                raise ValueError('副本数必须为 1–128。')
        except Exception as exc:
            self.console.notice.set(str(exc))
            return
        summary = f'{spec.name}\n节点：{spec.node_id or "自动按资源匹配"}\n步骤：' + ' → '.join(s.subcommand for s in spec.steps) + f'\n超时：{spec.timeout_seconds} 秒'
        if messagebox.askyesno('确认提交计算', summary, parent=self.root):
            count = int(self.replicas.get())
            if count > 1:
                spec.node_id = ''
                self.run(lambda: api.call('POST', '/api/v2/batches', json={'task': spec.model_dump(), 'count': count}), lambda value: self.show_text('跨机任务组已提交', value['batch_id']))
            else:
                self.run(lambda: api.call('POST', '/api/v2/tasks', json=spec.model_dump()), lambda value: self.show_text('任务已提交', value['id'] + '\n可在任务中心刷新查看进度。'))

    def build_invites(self):
        row = ttk.Frame(self.invite_tab)
        row.pack(fill='x')
        self.invite_hours = self.field(row, '有效小时', '24', 10)
        self.invite_note = self.field(row, '备注', '', 30)
        ttk.Button(row, text='创建邀请码', command=self.create_invite).pack(side='left')
        ttk.Button(row, text='刷新邀请码', command=self.refresh_invites).pack(side='left', padx=8)
        self.invite_tree = ttk.Treeview(self.invite_tab, columns=('prefix', 'expires', 'used'), show='headings', height=4)
        for name, label in [('prefix', '邀请码前缀'), ('expires', '到期时间'), ('used', '已使用')]:
            self.invite_tree.heading(name, text=label)
        self.invite_tree.pack(fill='both', expand=True, pady=8)
        ttk.Button(self.invite_tab, text='撤销选中邀请码', command=self.revoke_invite).pack(anchor='w')
        row = ttk.Frame(self.invite_tab)
        row.pack(fill='x')
        self.join_url = self.field(row, '本节点主控 URL', 'https://chemcompute.666945726.xyz', 32)
        self.join_name = self.field(row, '节点名称', '', 18)
        self.join_code = self.field(row, '新邀请码', '', 35, secret=True)
        row = ttk.Frame(self.invite_tab)
        row.pack(fill='x')
        self.gaussian_path = self.field(row, '本机 Gaussian g16.exe / g09.exe 路径', '', 55)
        self.local_limit = self.field(row, '节点任务时限 / 秒', '3600', 12)
        try:
            cfg = load_yaml_config('config/node.yaml', NodeConfig)
            self.gaussian_path.set(cfg.gaussian_custom_path or '')
            self.local_limit.set(str(cfg.max_job_timeout_seconds))
            self.join_url.set(cfg.controller_url)
            self.join_name.set(cfg.node_name)
        except Exception:
            pass
        ttk.Button(self.invite_tab, text='保存本节点入网配置（后台停止后操作）', command=self.enroll).pack(anchor='w')

    def create_invite(self):
        try:
            payload = {'expires_in_hours': int(self.invite_hours.get()), 'note': self.invite_note.get()}
        except ValueError:
            return
        def display(value):
            self.show_text('新邀请码（仅显示一次，可选择复制）', value['invite_code'])
            self.refresh_invites()
        self.run(lambda: api.call('POST', '/api/v1/invites', json=payload), display)

    def refresh_invites(self):
        def display(values):
            self.invite_tree.delete(*self.invite_tree.get_children())
            for value in values:
                self.invite_tree.insert('', 'end', iid=str(value['id']), values=(value['code_prefix'], value['expires_at'], '是' if value['is_used'] else '否'))
        self.run(lambda: api.call('GET', '/api/v1/invites'), display)

    def revoke_invite(self):
        selected = self.invite_tree.selection()
        if selected:
            self.run(lambda: api.call('DELETE', '/api/v1/invites/' + selected[0]), lambda _: self.refresh_invites())

    def enroll(self):
        from chemcompute import desktop_backend as backend
        if backend.running_process():
            self.console.notice.set('请先停止本机后台，再修改节点入网配置。')
            return
        config = load_yaml_config('config/node.yaml', NodeConfig)
        config.gaussian_custom_path = self.gaussian_path.get().strip() or None
        config.max_job_timeout_seconds = max(5, min(604800, int(self.local_limit.get())))
        config.controller_url = self.join_url.get().strip()
        config.node_name = self.join_name.get().strip()
        if self.join_code.get().strip():
            config.invite_code = self.join_code.get().strip()
            config.node_token = config.node_id = None
        save_yaml_config(config, 'config/node.yaml')
        self.join_code.set('')
        self.console.notice.set('入网配置已保存，请启动后台。')

    def build_settings(self):
        row = ttk.Frame(self.settings_tab)
        row.pack(fill='x')
        self.remote = self.field(row, '远程主控 URL（留空使用本机）', '', 38)
        self.admin = self.field(row, '远程管理员密钥（留空保留）', '', 40, True)
        row = ttk.Frame(self.settings_tab)
        row.pack(fill='x')
        self.deep_key = self.field(row, 'DeepSeek 密钥（留空保留）', '', 42, True)
        self.model_field = self.field(row, 'DeepSeek 模型', deepseek.DEFAULT_MODEL, 28)
        row = ttk.Frame(self.settings_tab)
        row.pack(fill='x', pady=12)
        ttk.Button(row, text='加密保存设置', command=self.save_settings).pack(side='left')
        ttk.Button(row, text='测试 DeepSeek 连接', command=lambda: self.run(deepseek.test_connection, lambda models: self.show_text('DeepSeek 可用模型', '\n'.join(models)))).pack(side='left', padx=8)
        ttk.Label(self.settings_tab, text='密钥通过 Windows DPAPI 加密，绑定当前 Windows 账号。安装包不包含密钥。\n其他电脑无需 Python。WSL/GROMACS/驱动可在依赖安装页配置；Gaussian 需已有授权安装。\n资源要求是节点准入门槛，不会自动改写科学参数；计算线程数请在步骤参数 -nt 中设置。', wraplength=950).pack(anchor='w', pady=10)
        try:
            settings = read_credentials()
            self.remote.set(settings.get('controller_url', ''))
            self.model_field.set(settings.get('model', deepseek.DEFAULT_MODEL))
            self.model = self.model_field.get()
        except Exception:
            self.console.notice.set('本机凭据无法解密，请重新保存设置。')

    def save_settings(self):
        try:
            values = read_credentials()
            values['controller_url'] = self.remote.get().strip()
            if values['controller_url'] and not values['controller_url'].startswith(('http://', 'https://')):
                raise ValueError('主控地址必须为 http:// 或 https:// URL')
            values['model'] = self.model_field.get().strip()
            for key, variable in [('admin_key', self.admin), ('deepseek_key', self.deep_key)]:
                if variable.get().strip():
                    values[key] = variable.get().strip()
            save_credentials(values)
            self.model = values['model']
            self.admin.set('')
            self.deep_key.set('')
            self.console.notice.set('设置已加密保存。')
        except Exception as exc:
            self.console.notice.set(str(exc))


    def choose_preset(self):
        name = self.preset.get()
        self.software.set('gaussian' if name.startswith('Gaussian') else 'gromacs')
        self.set_steps(PRESETS[name])

    def build_deployment(self):
        self.install_choices = {}
        for flag, label in [('WSL', 'WSL / Ubuntu（缺失时安装）'), ('Gromacs', 'GROMACS（仓库版本，可能仅支持 CPU）'), ('Drivers', 'Windows Update 匹配的显示驱动')]:
            variable = tk.BooleanVar(value=True)
            self.install_choices[flag] = variable
            ttk.Checkbutton(self.deploy_tab, text=label, variable=variable).pack(anchor='w', pady=6)
        ttk.Label(self.deploy_tab, text='可取消任一选项。系统会请求管理员权限；需要重启时保存进度，不强制重启。\nGaussian 使用已有授权安装，不随包分发。服务器入口：https://chemcompute.666945726.xyz', wraplength=950).pack(anchor='w', pady=12)
        ttk.Button(self.deploy_tab, text='安装 / 继续安装所选依赖', command=self.install_dependencies).pack(anchor='w')
        ttk.Button(self.deploy_tab, text='刷新安装状态', command=self.deployment_status).pack(anchor='w', pady=8)
        self.dependency_text = tk.Text(self.deploy_tab, height=8, wrap='word')
        self.dependency_text.pack(fill='both', expand=True)
        self.deployment_status()

    def deployment_status(self):
        import os
        path = Path(os.environ.get('LOCALAPPDATA', '.')) / 'ChemComputeData/dependency-status.json'
        text = path.read_text(encoding='utf-8-sig') if path.exists() else '尚未运行依赖安装。'
        self.dependency_text.delete('1.0', 'end')
        self.dependency_text.insert('1.0', text)

    def install_dependencies(self):
        import subprocess
        import sys
        folder = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
        script = folder / 'scripts/dependencies.ps1'
        flags = ['-' + name for name, variable in self.install_choices.items() if variable.get()]
        if not flags:
            return
        subprocess.Popen(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), *flags], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.console.notice.set('依赖安装已启动；请处理 Windows 权限提示，然后刷新安装状态。')

    def build_updates(self):
        self.update_info = {}
        self.is_downloading = False

        # Top info card
        info_frame = ttk.Frame(self.update_tab)
        info_frame.pack(fill='x', pady=(0, 12))

        header_frame = ttk.Frame(info_frame)
        header_frame.pack(fill='x', pady=4)

        from chemcompute import __version__
        ttk.Label(header_frame, text=f'当前运行版本: v{__version__}', font=('Microsoft YaHei UI', 11, 'bold')).pack(side='left')
        self.update_version_badge = ttk.Label(header_frame, text='(点击右侧按钮检查 GitHub 官方更新)', font=('Microsoft YaHei UI', 10), foreground='#0284c7')
        self.update_version_badge.pack(side='left', padx=12)

        ttk.Button(header_frame, text='立即检查更新', command=self.trigger_check_updates).pack(side='right')

        # Release details box
        ttk.Label(self.update_tab, text='更新说明与发行日志 (Changelog):', font=('Microsoft YaHei UI', 10, 'bold')).pack(anchor='w', pady=(8, 4))
        self.update_notes_text = tk.Text(self.update_tab, height=9, wrap='word', font=('Consolas', 10))
        self.update_notes_text.pack(fill='both', expand=True, pady=(0, 10))
        self.update_notes_text.insert('1.0', '点击【立即检查更新】从官方发布源获取最新版本与更新说明。')

        # Actions & progress
        action_frame = ttk.Frame(self.update_tab)
        action_frame.pack(fill='x', pady=4)

        self.btn_auto_update = ttk.Button(action_frame, text='⚡ 一键在线下载并安装更新', command=self.start_download_update, state='disabled')
        self.btn_auto_update.pack(side='left', padx=(0, 8))

        self.btn_open_github = ttk.Button(action_frame, text='🌐 前往 GitHub Release 页面', command=self.open_github_releases)
        self.btn_open_github.pack(side='left', padx=(0, 8))

        self.update_progress = ttk.Progressbar(self.update_tab, orient='horizontal', mode='determinate')
        self.update_progress.pack(fill='x', pady=(8, 4))

        self.update_status_label = ttk.Label(self.update_tab, text='', font=('Microsoft YaHei UI', 9))
        self.update_status_label.pack(anchor='w')

    def trigger_check_updates(self):
        self.update_version_badge.configure(text='正在连接 GitHub 检查更新…', foreground='#d97706')
        self.console.notice.set('正在连接 GitHub 官方源检查版本更新…')

        def worker():
            from chemcompute.updater.online_update import check_github_updates
            from chemcompute import __version__
            return check_github_updates(__version__)

        def callback(res):
            self.update_info = res
            latest = res.get('latest_version', '')
            has_update = res.get('has_update', False)

            if res.get('status') == 'error':
                self.update_version_badge.configure(text=f"检查失败: {res.get('error', '')}", foreground='#dc2626')
                return

            self.update_notes_text.delete('1.0', 'end')
            notes = res.get('release_notes') or '官方暂未填写更新说明。'
            self.update_notes_text.insert('1.0', f"【最新版本】v{latest} (发布于 {res.get('published_at')})\n【安装包资源】{res.get('asset_name')} ({res.get('asset_size', 0) / 1024 / 1024:.1f} MB)\n\n--- 发行更新日志 ---\n{notes}")

            if has_update:
                self.update_version_badge.configure(text=f'🎉 发现新版本: v{latest}！推荐更新', foreground='#16a34a')
                self.btn_auto_update.configure(state='normal', text=f'⚡ 一键在线下载并升级至 v{latest}')
                self.console.notice.set(f'发现新版本 v{latest}！可点击在线更新。')
            else:
                self.update_version_badge.configure(text=f'✓ 当前已是最新版本 (v{latest})', foreground='#16a34a')
                self.btn_auto_update.configure(state='normal', text='⚡ 重新下载当前最新安装包')
                self.console.notice.set('当前已是最新版本。')

        self.run(worker, callback)

    def start_download_update(self):
        if not self.update_info or not self.update_info.get('download_url'):
            messagebox.showwarning('提示', '未在发布信息中检测到可用安装包，请点击前往 GitHub 页面下载。', parent=self.root)
            return

        download_url = self.update_info['download_url']
        asset_name = self.update_info.get('asset_name', 'ChemCompute-Setup.exe')
        temp_dir = Path(tempfile.gettempdir())
        dest_path = temp_dir / asset_name

        self.btn_auto_update.configure(state='disabled')
        self.update_progress['value'] = 0
        self.update_status_label.configure(text='准备开始下载更新安装包…')

        def progress_cb(pct, current, total):
            self.root.after(0, lambda: self._update_download_progress(pct, current, total))

        def worker():
            from chemcompute.updater.online_update import download_file_stream
            return download_file_stream(download_url, dest_path, progress_cb)

        def callback(success):
            self.btn_auto_update.configure(state='normal')
            if success:
                self.update_status_label.configure(text=f'✓ 下载完成: {dest_path}')
                if messagebox.askyesno('更新就绪', f'新版本安装包已成功下载至：\n{dest_path}\n\n是否立即启动安装向导完成更新？\n(启动后将自动退出当前操控台)', parent=self.root):
                    from chemcompute.updater.online_update import launch_installer
                    launch_installer(dest_path)
                    self.root.destroy()
            else:
                self.update_status_label.configure(text='❌ 下载失败，请检查网络或通过 GitHub 页面下载。')
                messagebox.showerror('下载失败', '网络超时或连接中断，请重试或通过浏览器直接下载。', parent=self.root)

        self.run(worker, callback)

    def _update_download_progress(self, pct, current, total):
        self.update_progress['value'] = pct
        self.update_status_label.configure(text=f'正在下载更新包: {pct}% ({current / 1024 / 1024:.1f} MB / {total / 1024 / 1024:.1f} MB)')

    def open_github_releases(self):
        import webbrowser
        url = self.update_info.get('html_url') if self.update_info else 'https://github.com/C12isme945/ChemCompute/releases'
        webbrowser.open(url)
