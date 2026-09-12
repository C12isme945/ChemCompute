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
        from chemcompute.desktop_theme import (
            BG,
            FONT,
            INK,
            MUTED,
            SIDEBAR,
            apply_theme,
            asset,
            molecules,
        )
        root.geometry(f'{min(1380, root.winfo_screenwidth()-80)}x{min(880, root.winfo_screenheight()-100)}')
        root.minsize(min(1180, root.winfo_screenwidth()-80), min(780, root.winfo_screenheight()-100))
        root.configure(background=BG)
        self.style = apply_theme(root)
        self.logo = asset(root, 'logo.png', 18)
        self.servers = asset(root, 'servers.png', 6)
        root.iconphoto(True, self.logo)
        sidebar = tk.Frame(root, background=SIDEBAR, width=234, padx=16, pady=22)
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)
        tk.Label(sidebar, image=self.logo, background=SIDEBAR).pack(anchor='w')
        tk.Label(sidebar, text='ChemCompute', font=('Segoe UI', 20, 'bold'), background=SIDEBAR, foreground=INK).pack(anchor='w', pady=(2, 4))
        tk.Label(sidebar, text='桌面操控台 / 私人化学计算网络', font=(FONT, 8), background=SIDEBAR, foreground=MUTED).pack(anchor='w')
        tk.Label(sidebar, text=f'v{__version__} · 预发布', font=(FONT, 9), background=SIDEBAR, foreground=MUTED).pack(anchor='w', pady=(6, 25))
        self.navigation = tk.Frame(sidebar, background=SIDEBAR)
        self.navigation.pack(fill='x')
        tk.Label(sidebar, text='Compute for\na Better Chemistry', justify='left', background=SIDEBAR, foreground=MUTED, font=('Segoe UI', 10)).pack(side='bottom', anchor='w', pady=8)
        motif = tk.Canvas(sidebar, width=220, height=140, background=SIDEBAR, highlightthickness=0)
        motif.pack(side='bottom')
        molecules(motif)
        frame = ttk.Frame(root, padding=(22, 16), style='Shell.TFrame')
        frame.pack(fill='both', expand=True)
        header = tk.Frame(frame, background=BG, height=108)
        header.pack(fill='x', pady=(0, 12))
        motif_header = tk.Canvas(header, width=235, height=108, background=BG, highlightthickness=0)
        motif_header.pack(side='right')
        molecules(motif_header)
        self.page_title = tk.StringVar(value='本机与节点概览')
        tk.Label(header, textvariable=self.page_title, background=BG, foreground=INK, font=(FONT, 25, 'bold')).pack(anchor='w', pady=(10, 4))
        tk.Label(header, text='连接你的化学计算世界 · 从本地到集群', background=BG, foreground=MUTED, font=(FONT, 11)).pack(anchor='w')
        self.status = tk.StringVar(value='正在读取本机状态…')
        self.address = tk.StringVar()
        self.notice = tk.StringVar(value='关闭窗口后后台继续运行。')
        ttk.Label(frame, textvariable=self.status, style='Shell.TLabel').pack(anchor='w')
        ttk.Label(frame, textvariable=self.address, style='Shell.TLabel').pack(anchor='w', pady=(4, 12))
        actions = ttk.Frame(frame, style='Shell.TFrame')
        actions.pack(fill='x', pady=(0, 16))
        self.buttons = {}
        for text, action in [('启动后台', lambda: self.perform(backend.start)),
                             ('停止后台', lambda: self.confirm_stop(False)),
                             ('重启后台', lambda: self.confirm_stop(True)),
                             ('打开 Web 控制台', self.open_web),
                             ('刷新', self.refresh)]:
            button = ttk.Button(actions, text=text, command=action, style='Accent.TButton' if text == '启动后台' else 'TButton')
            button.pack(side='left', padx=(0, 8))
            self.buttons[text] = button
        ttk.Button(actions, text='在线更新', command=lambda: self.notebook.select(self.updates.frame)).pack(side='right')
        self.notebook = ttk.Notebook(frame)
        self.notebook.pack(fill='both', expand=True)
        overview = ttk.Frame(self.notebook, padding=14, style='Shell.TFrame')
        self.notebook.add(overview, text='节点概览')
        ttk.Label(overview, text='算力一览', style='Shell.TLabel', font=('Microsoft YaHei UI', 17, 'bold')).pack(anchor='w', pady=(0, 4))
        ttk.Label(overview, text='节点状态来自实时心跳；软件探测不代表许可证与科学模型已验证。', style='Shell.TLabel').pack(anchor='w', pady=(0, 16))
        cards = tk.Frame(overview, background=BG)
        cards.pack(fill='x', pady=(0, 18))
        self.metrics = {}
        for index, (key, title) in enumerate([('online', '在线节点'), ('gromacs', 'GROMACS 可用节点'), ('gaussian', 'Gaussian 已探测'), ('gpu', '已探测 GPU')]):
            from chemcompute.desktop_theme import Panel
            surface = Panel(cards, height=116)
            surface.grid(row=0, column=index, sticky='nsew', padx=(0, 10 if index < 3 else 0))
            card = surface.body
            cards.columnconfigure(index, weight=1, uniform='metrics')
            value = tk.StringVar(value='—')
            self.metrics[key] = value
            tk.Label(card, text=title, background='#ffffff', foreground='#516d80', font=('Microsoft YaHei UI', 10)).pack(anchor='w')
            tk.Label(card, textvariable=value, background='#ffffff', foreground='#246cf5', font=('Segoe UI', 26, 'bold')).pack(anchor='w', pady=(4, 0))
        grid = ttk.Frame(overview)
        grid.pack(fill='both', expand=True)
        columns = ('name', 'state', 'cpu', 'ram', 'gpu', 'gromacs', 'gaussian')
        self.table = ttk.Treeview(grid, columns=columns, show='headings', height=5)
        for key, title, width in zip(columns,
                                    ['节点名称', '状态', 'CPU 占用', '内存占用', 'GPU', 'GROMACS', 'Gaussian'],
                                    [160, 80, 85, 85, 190, 135, 155]):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=60)
        scroll_y = ttk.Scrollbar(grid, orient='vertical', command=self.table.yview)
        scroll_x = ttk.Scrollbar(grid, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.table.grid(row=0, column=0, sticky='nsew')
        scroll_y.grid(row=0, column=1, sticky='ns')
        scroll_x.grid(row=1, column=0, sticky='ew')
        grid.rowconfigure(0, weight=1)
        grid.columnconfigure(0, weight=1)
        self.empty = tk.StringVar(value='等待连接主控…')
        ttk.Label(overview, textvariable=self.empty, style='Shell.TLabel').pack(anchor='w', pady=(10, 0))
        notice = ttk.Label(frame, textvariable=self.notice, wraplength=900, style='Shell.TLabel')
        notice.pack(side='bottom', fill='x', pady=8, before=self.notebook)
        footer = ttk.Frame(frame, style='Shell.TFrame')
        footer.pack(side='bottom', fill='x', before=notice)
        self.copy_button = ttk.Button(footer, text='复制本机管理员密钥', command=self.copy_key)
        self.copy_button.pack(side='left', padx=(0, 8))
        for text, folder in [('配置文件', 'config'), ('运行日志', 'logs')]:
            ttk.Button(footer, text=text, command=lambda f=folder: self.open_folder(f)).pack(side='left', padx=(0, 8))
        ttk.Label(footer, text='关闭窗口后后台继续运行', style='Shell.TLabel').pack(side='right')
        from chemcompute.desktop_workspace import Workspace
        self.workspace = Workspace(self, self.notebook)
        from chemcompute.updates_ui import UpdatesPanel
        self.updates = UpdatesPanel(self)
        self.nav_buttons = []
        labels = ['⌂  本机与节点概览', '▤  任务中心', '▣  提交计算包', '◇  节点与邀请码', '◎  连接与 AI 设置', '⚙  依赖安装', '↻  在线更新']
        for i, label in enumerate(labels):
            button = ttk.Button(self.navigation, text=label, style='Nav.TButton', command=lambda index=i: self.notebook.select(index))
            button.pack(fill='x', pady=4)
            self.nav_buttons.append(button)
        self.notebook.bind('<<NotebookTabChanged>>', self.page_changed)
        self.page_changed()
        self.empty_art = tk.Label(grid, image=self.servers, background='#ffffff')
        self.empty_art.place(relx=.5, rely=.64, anchor='center')
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.timer = root.after(100, self.pump)
        self.refresh()

    def page_changed(self, event=None):
        selected = self.notebook.index(self.notebook.select())
        for i, button in enumerate(self.nav_buttons):
            button.configure(style='Selected.Nav.TButton' if i == selected else 'Nav.TButton')
        self.page_title.set(['本机与节点概览', '任务中心', '提交计算包', '节点与邀请码', '连接与 AI 设置', '依赖安装', '在线更新'][selected])

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
        nodes = data['nodes']
        if nodes:
            self.empty_art.place_forget()
        else:
            self.empty_art.place(relx=.5, rely=.64, anchor='center')
        online = [n for n in nodes if n.get('status') in {'online', 'busy'}]
        counts = {'online': len(online),
                  'gromacs': sum(bool(n.get('software_info', {}).get('gromacs', {}).get('found')) for n in online),
                  'gaussian': sum(bool(n.get('software_info', {}).get('gaussian', {}).get('found')) for n in online),
                  'gpu': sum(len(n.get('hardware_info', {}).get('gpus', [])) for n in online)}
        for key, value in counts.items():
            self.metrics[key].set(str(value) if data['online'] else '—')
        self.empty.set('暂无节点。启动本机后台，或在“节点与邀请码”中邀请其他电脑。' if not nodes else f'共 {len(nodes)} 个节点 · 每 5 秒刷新')
        for node in data['nodes']:
            hardware = node.get('hardware_info', {})
            software = node.get('software_info', {}).get('gromacs', {})
            state = {'online': '在线', 'offline': '离线', 'busy': '忙碌'}.get(node['status'], node['status'])
            self.table.insert('', 'end', values=(node['name'], state,
                              f"{hardware['cpu_percent']:.1f}%" if hardware.get('cpu_percent') is not None else '—',
                              f"{hardware['ram_percent']:.1f}%" if hardware.get('ram_percent') is not None else '—',
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
        self.updates.close()
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
        assert len(console.notebook.tabs()) == 7
        assert console.updates.check_button.winfo_exists()
        assert console.workspace.task_tree.winfo_exists()
        root.after(100, console.close)
    root.mainloop()
    if smoke_test:
        print('PASS: native desktop console widgets, render, event loop and clean close')
    return 0
