"""Local contribution preferences; saving does not restart or cancel work."""
from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import psutil

from chemcompute.contribution import ContributionSettings, load_settings, save_settings
from chemcompute.desktop_theme import BG, BLUE, FONT, INK, MUTED, Panel, icon


class BudgetSlider(tk.Canvas):
    def __init__(self, parent, variable, maximum, command):
        super().__init__(parent, height=32, background='white', highlightthickness=0, takefocus=True)
        self.variable, self.maximum, self.command = variable, maximum, command
        self.bind('<Configure>', self.draw)
        self.bind('<Button-1>', self.move)
        self.bind('<B1-Motion>', self.move)
        self.bind('<Left>', lambda _: self.set(variable.get() - 1))
        self.bind('<Right>', lambda _: self.set(variable.get() + 1))
        self.bind('<Home>', lambda _: self.set(1))
        self.bind('<End>', lambda _: self.set(maximum))
        self.bind('<FocusIn>', self.draw)
        self.bind('<FocusOut>', self.draw)
        variable.trace_add('write', self.draw)

    def set(self, value):
        self.variable.set(max(1, min(self.maximum, round(value))))
        self.command()

    def move(self, event):
        self.focus_set()
        self.set(1 + (event.x - 12) / max(1, self.winfo_width() - 24) * (self.maximum - 1))

    def draw(self, *_):
        self.delete('all')
        end = max(12, self.winfo_width() - 12)
        x = 12 + (end - 12) * (self.variable.get() - 1) / (self.maximum - 1)
        self.create_line(12, 16, end, 16, width=6, fill='#e1ebfb', capstyle='round')
        self.create_line(12, 16, x, 16, width=6, fill=BLUE, capstyle='round')
        self.create_oval(x-10, 6, x+10, 26, fill=BLUE, outline='#a5caff' if self.focus_get() == self else 'white', width=3)


class ContributionPanel:
    def __init__(self, console):
        self.console = console
        self.path = Path('config/node.yaml')
        self.frame = console.workspace.tab(console.notebook, '算力贡献')
        self.frame.configure(style='Shell.TFrame', padding=16)
        for child in self.frame.winfo_children():
            child.destroy()  # The page title is already in the persistent header.
        self.icons = [icon(console.root, name) for name in ('cpu', 'memory-stick', 'gpu')]
        self.cpu = tk.DoubleVar(value=50)
        self.memory = tk.DoubleVar(value=50)
        self.gpu = tk.BooleanVar(value=False)
        self.paused = tk.BooleanVar(value=False)
        self.summary = tk.StringVar()
        self.status = tk.StringVar(value='设置只影响本机。保存后下一个任务生效，当前任务继续使用原来的预算。')
        ttk.Label(self.frame, text='给研究留一份算力，也给日常工作留出空间', style='Shell.TLabel', font=(FONT, 16, 'bold')).pack(anchor='w', pady=(0, 14))
        presets = ttk.Frame(self.frame, style='Shell.TFrame')
        presets.pack(fill='x', pady=(0, 14))
        for label, cpu, ram in [('轻量 · 25%', 25, 25), ('均衡 · 50%', 50, 50), ('专注计算 · 100%', 100, 80)]:
            ttk.Button(presets, text=label, command=lambda c=cpu, r=ram: self.preset(c, r)).pack(side='left', padx=(0, 10))
        cards = tk.Frame(self.frame, background=BG)
        cards.pack(fill='x')
        for index, (title, variable, limit, note) in enumerate([
            ('CPU 核心贡献', self.cpu, 100, '按逻辑核心数分配，不是 CPU 瞬时占用率限流。'),
            ('内存调度预算', self.memory, 90, '用于任务准入，不是操作系统内存硬上限。'),
        ]):
            cards.columnconfigure(index, weight=1, uniform='budgets')
            panel = Panel(cards, height=128, padding=16)
            panel.grid(row=0, column=index, sticky='ew', padx=(0, 12 if index == 0 else 0))
            ttk.Label(panel.body, text='  ' + title, image=self.icons[index], compound='left', font=(FONT, 12, 'bold')).pack(anchor='w')
            BudgetSlider(panel.body, variable, limit, self.preview).pack(fill='x', pady=8)
            ttk.Label(panel.body, text=note, foreground=MUTED, font=(FONT, 9)).pack(anchor='w')
        ttk.Label(self.frame, textvariable=self.summary, style='Shell.TLabel', foreground=BLUE, font=(FONT, 13, 'bold')).pack(anchor='w', pady=16)
        options = ttk.Frame(self.frame, padding=14)
        options.pack(fill='x')
        ttk.Checkbutton(options, text='允许本机 GPU 参与计算', variable=self.gpu, command=self.preview).pack(anchor='w', pady=4)
        ttk.Checkbutton(options, text='暂停接收新任务（已开始的任务继续运行）', variable=self.paused, command=self.preview).pack(anchor='w', pady=4)
        page = self.frame.master.master
        actions = ttk.Frame(page, padding=(16, 8), style='Shell.TFrame')
        actions.grid(row=2, column=0, columnspan=2, sticky='ew')
        self.save_button = ttk.Button(actions, text='保存算力设置', style='Accent.TButton', command=self.save)
        self.save_button.pack(side='left', padx=(0, 10))
        ttk.Button(actions, text='重新读取', command=self.reload).pack(side='left')
        ttk.Label(page, textvariable=self.status, wraplength=900, padding=(16, 0, 16, 10), style='Shell.TLabel', foreground=INK).grid(row=3, column=0, columnspan=2, sticky='ew')
        self.reload()

    def preset(self, cpu, memory):
        self.cpu.set(cpu)
        self.memory.set(memory)
        self.preview()

    def preview(self, *_):
        cpu, ram = round(self.cpu.get()), round(self.memory.get())
        cores = max(1, math.ceil((psutil.cpu_count() or 1) * cpu / 100))
        memory_gb = psutil.virtual_memory().total * ram / 100 / 1024**3
        state = '暂停接单' if self.paused.get() else '允许接单'
        self.summary.set(f'{cpu}% · {cores} 个逻辑核心    /    {ram}% · {memory_gb:.1f} GB 内存预算    /    {state}')

    def reload(self):
        try:
            settings = load_settings(self.path)
            self.cpu.set(settings.cpu_percent)
            self.memory.set(settings.memory_percent)
            self.gpu.set(settings.gpu_enabled)
            self.paused.set(settings.paused)
            self.preview()
        except (OSError, ValueError) as exc:
            self.status.set(f'无法读取设置：{exc}')

    def save(self):
        try:
            settings = ContributionSettings(cpu_percent=round(self.cpu.get()), memory_percent=round(self.memory.get()), gpu_enabled=self.gpu.get(), paused=self.paused.get())
            save_settings(self.path, settings)
            self.status.set('已保存。节点在下一次接单时读取；主控资源显示会在下一次心跳后更新。当前计算不会中断。')
        except (OSError, ValueError) as exc:
            self.status.set(f'保存失败：{exc}')
