"""Native Tk desktop console. Network and process work stays off the UI thread."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from chemcompute import __version__
from chemcompute import desktop_backend as backend


class Console:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events = queue.Queue()
        self.busy = False
        self.refreshing = False
        self.closed = False
        self.timer = None
        root.title(f'ChemCompute 桌面操控台 · {__version__}')
        root.geometry('1180x820')
        root.minsize(1080, 740)
        root.configure(background='#eef3f8')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('TFrame', background='#eef3f8')
        style.configure('TLabel', background='#eef3f8', font=('Microsoft YaHei UI', 10))
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 21, 'bold'), foreground='#12324d')
        style.configure('TButton', font=('Microsoft YaHei UI', 10), padding=(12, 9))
        style.configure('Treeview', font=('Microsoft YaHei UI', 10), rowheight=34)
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'))
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='ChemCompute', style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame, text='桌面操控台  /  私人化学计算网络').pack(anchor='w', pady=(2, 18))
        self.status = tk.StringVar(value='正在读取本机状态…')
        self.address = tk.StringVar()
        self.notice = tk.StringVar(value='关闭窗口后后台继续运行。')
        ttk.Label(frame, textvariable=self.status).pack(anchor='w')
        ttk.Label(frame, textvariable=self.address).pack(anchor='w', pady=(6, 16))
        actions = ttk.Frame(frame)
        actions.pack(fill='x', pady=(0, 16))
        self.buttons = {}
        for text, action in [('启动后台', lambda: self.perform(backend.start)),
                             ('停止后台', lambda: self.confirm_stop(False)),
                             ('重启后台', lambda: self.confirm_stop(True)),
                             ('打开 Web 控制台', self.open_web),
                             ('刷新', self.refresh)]:
            button = ttk.Button(actions, text=text, command=action)
            button.pack(side='left', padx=(0, 8))
            self.buttons[text] = button
        self.notebook = ttk.Notebook(frame)
        self.notebook.pack(fill='both', expand=True)
        overview = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(overview, text='本机与节点概览')
        columns = ('name', 'state', 'cpu', 'ram', 'gpu', 'gromacs', 'gaussian')
        self.table = ttk.Treeview(overview, columns=columns, show='headings', height=8)
        for key, title, width in zip(columns,
                                    ['节点名称', '状态', 'CPU 占用', '内存占用', 'GPU', 'GROMACS', 'Gaussian'],
                                    [160, 80, 85, 85, 190, 135, 155]):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=60)
        self.table.pack(fill='both', expand=True)
        ttk.Label(frame, textvariable=self.notice, wraplength=930).pack(anchor='w', pady=12)
        footer = ttk.Frame(frame)
        footer.pack(fill='x')
        self.copy_button = ttk.Button(footer, text='复制本机管理员密钥', command=self.copy_key)
        self.copy_button.pack(side='left', padx=(0, 8))
        for text, folder in [('配置文件', 'config'), ('运行日志', 'logs')]:
            ttk.Button(footer, text=text, command=lambda f=folder: self.open_folder(f)).pack(side='left', padx=(0, 8))
        ttk.Label(footer, text='关闭窗口后后台继续运行').pack(side='right')
        from chemcompute.desktop_workspace import Workspace
        self.workspace = Workspace(self, self.notebook)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.timer = root.after(100, self.pump)
        self.refresh()

    def worker(self, kind, function):
        def run():
            try:
                self.events.put((kind, function(), None))
            except Exception as exc:
                self.events.put((kind, None, str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def perform(self, function):
        if self.busy:
            return
        self.busy = True
        self.notice.set('正在处理…')
        for name in ['启动后台', '停止后台', '重启后台']:
            self.buttons[name].configure(state='disabled')
        self.worker('action', function)

    def confirm_stop(self, restart):
        if messagebox.askyesno('确认操作', '此操作会中断本机后台管理。请先确认没有需要继续的计算任务。', parent=self.root):
            self.perform(backend.restart if restart else backend.stop)

    def refresh(self):
        if not self.refreshing:
            self.refreshing = True
            self.worker('snapshot', backend.snapshot)

    def render(self, data):
        roles = {'both': '主控 + 计算节点', 'controller': '主控端', 'node': '计算节点'}
        self.status.set(f"本机角色：{roles[data['role']]}     后台：{'运行中' if data['running'] else '未运行'}     主控连接：{'可连接' if data['online'] else '未连接'}")
        self.address.set('主控地址：' + data['url'])
        self.copy_button.configure(state='disabled' if data['role'] == 'node' else 'normal')
        self.table.delete(*self.table.get_children())
        for node in data['nodes']:
            hardware = node.get('hardware_info', {})
            software = node.get('software_info', {}).get('gromacs', {})
            state = {'online': '在线', 'offline': '离线', 'busy': '忙碌'}.get(node['status'], node['status'])
            self.table.insert('', 'end', values=(node['name'], state,
                              f"{hardware.get('cpu_percent', 0):.1f}%",
                              f"{hardware.get('ram_percent', 0):.1f}%",
                              ', '.join(g['name'] for g in hardware.get('gpus', [])) or '未检测到',
                              software.get('version') or '未检测到',
                              '已找到（未验证授权）' if node.get('software_info', {}).get('gaussian', {}).get('found') else '未检测到'))
        if not self.busy:
            self.notice.set(data['notice'] or (f"已纳管 {len(data['nodes'])} 个节点。" if data['nodes'] else '暂无节点。可启动本机后台，或在 Web 控制台创建邀请码。'))

    def pump(self):
        while not self.events.empty():
            kind, value, error = self.events.get_nowait()
            if callable(kind):
                try:
                    kind(value, error)
                except Exception as exc:
                    self.notice.set(str(exc))
            elif kind == 'action':
                self.busy = False
                for name in ['启动后台', '停止后台', '重启后台']:
                    self.buttons[name].configure(state='normal')
                self.notice.set(error or value)
                self.refresh()
            elif kind == 'snapshot':
                self.refreshing = False
                if error:
                    self.notice.set(error)
                else:
                    self.render(value)
        if not self.closed:
            self.timer = self.root.after(200, self.pump)
            # Refresh at most once every five seconds.
            import time
            now = time.monotonic()
            if now - getattr(self, 'last_refresh', 0) > 5:
                self.last_refresh = now
                self.refresh()

    def copy_key(self):
        key = backend.admin_key()
        if not key:
            self.notice.set('尚无本机密钥，请先启动主控后台。')
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(key)
        self.notice.set('管理员密钥已复制；请仅用于可信的本机控制台。')

    def open_web(self):
        url = backend.controller_url()
        if url.startswith(('http://', 'https://')):
            webbrowser.open(url)

    def open_folder(self, name):
        path = Path(name).resolve()
        path.mkdir(exist_ok=True)
        os.startfile(str(path))

    def close(self):
        self.closed = True
        if self.timer:
            self.root.after_cancel(self.timer)
        self.root.destroy()


def run_console(smoke_test: bool = False) -> int:
    root = tk.Tk()
    if smoke_test:
        root.withdraw()
    console = Console(root)
    if smoke_test:
        # Exercise the real packaged Tk runtime and layout without changing host state.
        root.update_idletasks()
        console.render({'role': 'both', 'running': False, 'online': False, 'url': 'http://127.0.0.1:8000', 'notice': 'Smoke test', 'nodes': []})
        assert console.buttons['启动后台'].winfo_exists()
        assert console.table.winfo_exists()
        assert len(console.notebook.tabs()) == 6
        assert console.workspace.task_tree.winfo_exists()
        root.after(100, console.close)
    root.mainloop()
    if smoke_test:
        print('PASS: native desktop console widgets, render, event loop and clean close')
    return 0
