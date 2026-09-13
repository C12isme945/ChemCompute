"""Exercise the real Tk control-to-file path on Windows in an isolated home."""
import os
from types import SimpleNamespace

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows desktop acceptance')
def test_save_reload_and_keyboard_range(tmp_path, monkeypatch):
    import tkinter as tk
    from tkinter import ttk

    from chemcompute.contribution import load_settings
    from chemcompute.contribution_ui import BudgetSlider, ContributionPanel
    from chemcompute.desktop_theme import apply_theme
    from chemcompute.desktop_workspace import Workspace

    monkeypatch.chdir(tmp_path)
    root = tk.Tk()
    root.withdraw()
    try:
        apply_theme(root)
        notebook = ttk.Notebook(root)
        console = SimpleNamespace(root=root, notebook=notebook)
        console.workspace = SimpleNamespace(tab=lambda book, name: Workspace.tab(None, book, name))
        panel = ContributionPanel(console)
        panel.preset(25, 30)
        panel.gpu.set(True)
        panel.paused.set(True)
        panel.save_button.invoke()
        stored = load_settings(panel.path)
        assert (stored.cpu_percent, stored.memory_percent, stored.gpu_enabled, stored.paused) == (25, 30, True, True)
        panel.preset(100, 80)
        panel.reload()
        assert panel.cpu.get() == 25
        variable = tk.DoubleVar(value=50)
        slider = BudgetSlider(root, variable, 90, lambda: None)
        slider.set(200)
        assert variable.get() == 90
        slider.set(-20)
        assert variable.get() == 1
        assert not (tmp_path / 'config/node.yaml').exists(), 'Contribution settings must not overwrite enrollment'
    finally:
        root.destroy()
