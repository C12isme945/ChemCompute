"""Non-blocking Tk update panel: all widget access stays on Tk's thread."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from chemcompute import __version__
from chemcompute import online_update as update


class UpdatesPanel:
    def __init__(self, console):
        self.console = console
        self.frame = ttk.Frame(console.notebook, padding=20)
        console.notebook.add(self.frame, text='在线更新')
        self.queue = queue.Queue()
        self.cancelled = threading.Event()
        self.release = None
        self.installer = None
        self.busy = False
        self.status = tk.StringVar(value='安装前会检查所有计算任务。运行中的任务不会被强行中断。')
        self.version = tk.StringVar(value=f'当前版本  {__version__}')
        self.preview = tk.BooleanVar(value=True)
        ttk.Label(self.frame, text='让工作台保持最新', font=('Microsoft YaHei UI', 19, 'bold')).pack(anchor='w', pady=(0, 10))
        ttk.Label(self.frame, textvariable=self.version).pack(anchor='w', pady=(0, 12))
        ttk.Checkbutton(self.frame, text='包含预发布版本（当前项目仍处于预发布阶段）', variable=self.preview).pack(anchor='w')
        actions = ttk.Frame(self.frame)
        actions.pack(fill='x', pady=16)
        self.check_button = ttk.Button(actions, text='检查更新', command=self.check)
        self.check_button.pack(side='left', padx=(0, 8))
        self.download_button = ttk.Button(actions, text='下载更新', command=self.download, state='disabled')
        self.download_button.pack(side='left', padx=(0, 8))
        self.install_button = ttk.Button(actions, text='安装并重启', command=self.install, state='disabled')
        self.install_button.pack(side='left', padx=(0, 8))
        self.cancel_button = ttk.Button(actions, text='取消下载', command=self.cancelled.set, state='disabled')
        self.cancel_button.pack(side='left')
        self.progress = ttk.Progressbar(self.frame, maximum=100)
        self.progress.pack(fill='x', pady=(0, 10))
        ttk.Label(self.frame, textvariable=self.status, wraplength=940).pack(anchor='w', pady=(0, 14))
        body = ttk.Frame(self.frame)
        body.pack(fill='both', expand=True)
        self.notes = tk.Text(body, wrap='word', font=('Microsoft YaHei UI', 10), relief='flat', padx=16, pady=14,
                             background='#ffffff', foreground='#19364b', height=3, state='disabled')
        scrollbar = ttk.Scrollbar(body, command=self.notes.yview)
        self.notes.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.notes.pack(fill='both', expand=True)
        ttk.Button(self.frame, text='打开更新日志目录', command=self.open_logs).pack(side='bottom', anchor='w', before=body)
        ttk.Label(self.frame, text='官方 GitHub 来源 · SHA-256 校验 · 保留配置与结果 · 安装时短暂重启', foreground='#526c7e').pack(side='bottom', anchor='w', pady=10, before=body)
        self.set_notes('检查新版本后，这里会显示更新说明。\n\n下载可与计算同时进行；安装要求本机主控全部任务空闲。纯计算节点暂需手动升级。\n\n这是在线下载后重启升级，不提供运行中代码替换或自动回滚承诺。')
        self.timer = console.root.after(150, self.pump)
        self.show_last_status()

    def set_notes(self, text):
        self.notes.configure(state='normal')
        self.notes.delete('1.0', 'end')
        self.notes.insert('1.0', text)
        self.notes.configure(state='disabled')

    def show_last_status(self):
        import json
        files = sorted(Path('updates').glob('install-*/status.json'), key=lambda p: p.stat().st_mtime)
        if files:
            try:
                result = json.loads(files[-1].read_text('utf-8-sig'))
                if result['state'] == 'failed':
                    self.status.set('上次更新未完成：' + result['message'])
                elif result['state'] == 'completed':
                    self.status.set('上次更新安装完成。配置与结果已保留。')
            except (OSError, ValueError, KeyError):
                pass

    def open_logs(self):
        Path('updates').mkdir(exist_ok=True)
        os.startfile(str(Path('updates').resolve()))

    def run(self, kind, callback):
        if self.busy:
            return
        self.busy = True
        for button in [self.check_button, self.download_button, self.install_button]:
            button.configure(state='disabled')
        def work():
            try:
                self.queue.put((kind, callback(), None))
            except Exception as exc:
                self.queue.put((kind, None, getattr(exc, 'detail', str(exc))))
        threading.Thread(target=work, daemon=True).start()

    def check(self):
        self.status.set('正在查询官方发布…')
        preview = self.preview.get()
        self.run('check', lambda: update.check_updates(include_prerelease=preview))

    def download(self):
        release = self.release
        if not release or self.busy:
            return
        self.cancelled.clear()
        self.cancel_button.configure(state='normal')
        self.status.set('正在下载并校验安装包…')
        target = Path('updates') / ('ChemCompute-' + release['version'] + '-Setup.exe')
        self.run('download', lambda: update.download_update(release, target,
                 lambda done, total: self.queue.put(('progress', (done, total), None)), self.cancelled.is_set))

    def install(self):
        if not self.installer or self.busy:
            return
        if messagebox.askyesno('安装更新', '将检查任务是否空闲，并在安装后重启工作台。现有配置和结果保留。现在开始？', parent=self.console.root):
            self.status.set('正在检查任务与安装包，请稍候…')
            self.run('install', lambda: update.launch_update(self.release, self.installer))

    def pump(self):
        if self.console.closed:
            return
        try:
            while True:
                kind, value, error = self.queue.get_nowait()
                if kind == 'progress':
                    done, total = value
                    self.progress['value'] = done / total * 100
                    self.status.set(f'下载中  {done/1048576:.1f} / {total/1048576:.1f} MB')
                    continue
                self.busy = False
                self.cancel_button.configure(state='disabled')
                if error:
                    self.status.set(str(error))
                elif kind == 'check':
                    self.release, self.installer = value, None
                    self.progress['value'] = 0
                    if value:
                        self.version.set(f"当前 {__version__}  →  可更新至 {value['version']}   ·   {value['size']/1048576:.1f} MB")
                        self.set_notes(value['notes'] or '此版本未提供更新说明。')
                        self.status.set('发现新版本，可先下载，待计算完成后安装。')
                    else:
                        self.version.set(f'当前版本  {__version__}')
                        self.status.set('当前通道没有更新且包含有效校验信息的版本。')
                elif kind == 'download':
                    self.installer = value
                    self.status.set('下载完成，SHA-256 校验通过。可以安装并重启。')
                elif kind == 'install':
                    self.console.close()
                    return
                self.check_button.configure(state='normal')
                self.download_button.configure(state='normal' if self.release else 'disabled')
                self.install_button.configure(state='normal' if self.installer else 'disabled')
        except queue.Empty:
            pass
        self.timer = self.console.root.after(150, self.pump)

    def close(self):
        self.cancelled.set()
        if self.timer:
            self.console.root.after_cancel(self.timer)
