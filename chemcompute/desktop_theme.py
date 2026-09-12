"""Small native Tk components for the blue ChemCompute workspace."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

BG = '#edf5ff'
SIDEBAR = '#dfecfc'
INK = '#102653'
MUTED = '#5f769c'
BLUE = '#246cf5'
WHITE = '#ffffff'
FONT = 'Microsoft YaHei UI'


def apply_theme(root):
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=(FONT, 10), foreground=INK)
    for name, color in [('TFrame', WHITE), ('Shell.TFrame', BG)]:
        style.configure(name, background=color)
    style.configure('TLabel', background=WHITE, foreground=INK)
    style.configure('Shell.TLabel', background=BG, foreground=MUTED)
    style.configure('TButton', padding=(12, 9), background='#f5f9ff', foreground=INK, borderwidth=1, bordercolor='#d3e3fb', focusthickness=2, focuscolor='#9ac3ff')
    style.map('TButton', background=[('active', '#e0edff'), ('disabled', '#f0f4fa')], foreground=[('disabled', '#899ab4')])
    style.configure('Accent.TButton', background=BLUE, foreground=WHITE, bordercolor='#5596ff')
    style.map('Accent.TButton', background=[('disabled', '#d3dff4'), ('active', '#1554d9')], foreground=[('disabled', '#8298be'), ('!disabled', WHITE)])
    style.configure('Nav.TButton', anchor='w', padding=(16, 13), background=SIDEBAR, borderwidth=0, foreground='#49658d')
    style.map('Nav.TButton', background=[('active', '#ccdefa')])
    style.configure('Selected.Nav.TButton', background='#347bf8', foreground=WHITE)
    style.map('Selected.Nav.TButton', background=[('active', '#246cf5')], foreground=[('!disabled', WHITE)])
    style.configure('TNotebook', background=BG, borderwidth=0, tabmargins=0)
    style.layout('TNotebook.Tab', [])
    style.layout('TNotebook', [('Notebook.client', {'sticky': 'nswe'})])
    style.layout('Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
    style.configure('Treeview', rowheight=42, background=WHITE, fieldbackground=WHITE, foreground=INK, borderwidth=0)
    style.configure('Treeview.Heading', background='#eff5ff', foreground=INK, padding=(8, 12), font=(FONT, 10, 'bold'))
    style.map('Treeview', background=[('selected', '#dbeaff')], foreground=[('selected', INK)])
    style.configure('TLabelframe', background=WHITE, bordercolor='#d9e6fa')
    style.configure('TLabelframe.Label', background=WHITE, foreground=INK, font=(FONT, 10, 'bold'))
    style.configure('TCheckbutton', background=WHITE)
    style.configure('TEntry', padding=6, fieldbackground='#fbfdff', bordercolor='#ccdff9')
    style.configure('TCombobox', padding=6, fieldbackground='#fbfdff')
    style.configure('Horizontal.TProgressbar', background=BLUE, troughcolor='#e3edfb', borderwidth=0)
    root.option_add('*Text.Font', (FONT, 10))
    root.option_add('*Text.Background', '#f7faff')
    root.option_add('*Text.Foreground', INK)
    root.option_add('*Text.selectBackground', '#cce0ff')
    return style


def asset(root, name, factor):
    # Display scaling only. The user-provided source artwork remains unchanged.
    return tk.PhotoImage(master=root, file=str(Path(__file__).with_name('assets') / name)).subsample(factor)


class Panel(tk.Canvas):
    """Rounded white panel with a native frame and a resize-aware border."""
    def __init__(self, parent, *, height=110, background=BG, fill=WHITE, padding=16):
        super().__init__(parent, height=height, background=background, highlightthickness=0, borderwidth=0)
        self.fill, self.padding = fill, padding
        self.body = tk.Frame(self, background=fill)
        self.window = self.create_window(padding, padding, window=self.body, anchor='nw')
        self.bind('<Configure>', self.layout)

    def layout(self, event=None):
        w, h, r = self.winfo_width()-2, self.winfo_height()-3, 18
        self.delete('surface')
        points = [r, 1, w-r, 1, w, 1, w, r, w, h-r, w, h, w-r, h, r, h, 1, h, 1, h-r, 1, r, 1, 1]
        self.create_polygon(points, smooth=True, splinesteps=24, fill=self.fill, outline='#d3e3fa', tags='surface')
        self.tag_lower('surface')
        self.itemconfigure(self.window, width=max(1, w-self.padding*2), height=max(1, h-self.padding*2))


def molecules(canvas):
    """Code-drawn molecular motif: decoration only, never a node status."""
    points = [(28, 60, 13), (83, 88, 23), (133, 47, 17), (178, 100, 28), (218, 57, 11)]
    for i, j in [(0, 1), (1, 2), (2, 3), (3, 4), (1, 3)]:
        x, y, _ = points[i]
        xx, yy, _ = points[j]
        canvas.create_line(x, y, xx, yy, fill='#bdd8fa', width=7)
        canvas.create_line(x, y-2, xx, yy-2, fill='#f5fbff', width=2)
    for x, y, r in points:
        canvas.create_oval(x-r, y-r, x+r, y+r, fill='#b4d5ff', outline='#f7fbff', width=2)
        canvas.create_oval(x-r*.55, y-r*.62, x-r*.1, y-r*.17, fill='#eef8ff', outline='')
