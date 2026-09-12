"""
Calculation Input Templates and Samples REST API endpoints.
Provides standard templates (GROMACS, ORCA) for users, complete with downloadable
sample bundles and file structure specifications.
"""

import io
import json
import zipfile
from pathlib import Path
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

router = APIRouter(prefix="/api/templates", tags=["templates"])

# Sample bundles source definitions
TEMPLATES_CATALOG = [
    {
        "id": "gromacs-water-smoke",
        "name": "GROMACS 水分子能量极小化与短程动力学范本 (Water Smoke Test)",
        "adapter": "gromacs",
        "category": "分子动力学 (MD)",
        "recommended_hardware": "CPU (1-4核) 或 GPU 加速节点",
        "description": "包含 2 个水分子在周期性盒子中的初始坐标 (conf.gro)、SPC/E 力场拓扑 (topol.top)、以及 100 步 Verlet 截断能量极小化参数 (min.mdp)。可作为任何 GROMACS 分子体系的标准搭建范本。",
        "files_tree": [
            {"path": "min.mdp", "desc": "分子动力学控制参数文件 (integrator, nsteps, cutoff)"},
            {"path": "conf.gro", "desc": "体系初始原子坐标文件 (Water Box 结构)"},
            {"path": "topol.top", "desc": "分子拓扑结构与力场定义 (#include forcefield)"},
            {"path": "chemcompute.json", "desc": "自动化执行工作流 (grompp 预处理 + mdrun 模拟)"}
        ],
        "file_previews": {
            "min.mdp": """; Minimal GROMACS test MDP for ChemCompute verification
integrator               = md
nsteps                   = 100
dt                       = 0.002
nstxout-compressed       = 10
nstlog                   = 10
cutoff-scheme            = Verlet
nstlist                  = 10
rlist                    = 0.9
coulombtype              = cut-off
rcoulomb                 = 0.9
vdwtype                  = cut-off
rvdw                     = 0.9
tcoupl                   = no
pcoupl                   = no
constraints              = none
""",
            "conf.gro": """Water Box Test
    6
    1SOL     OW    1   0.126   1.624   1.679  0.1234 -0.4567  0.3456
    1SOL    HW1    2   0.190   1.701   1.704 -0.2345  0.1234 -0.4567
    1SOL    HW2    3   0.177   1.568   1.613  0.3456 -0.2345  0.1234
    2SOL     OW    4   1.126   0.624   0.679 -0.1234  0.4567 -0.3456
    2SOL    HW1    5   1.190   0.701   0.704  0.2345 -0.1234 -0.4567
    2SOL    HW2    6   1.177   0.568   0.613 -0.3456  0.2345  0.1234
   2.00000   2.00000   2.00000
""",
            "topol.top": """; Simple SPC/E water topology
#include "oplsaa.ff/forcefield.itp"
#include "oplsaa.ff/spce.itp"

[ system ]
Two SPC/E Water Molecules for ChemCompute Test

[ molecules ]
SOL         2
""",
            "chemcompute.json": """{
  "name": "Two water molecules - 100 step smoke test",
  "steps": [
    {
      "subcommand": "grompp",
      "arguments": ["-f", "min.mdp", "-c", "conf.gro", "-p", "topol.top", "-o", "run.tpr"]
    },
    {
      "subcommand": "mdrun",
      "arguments": ["-s", "run.tpr", "-deffnm", "run", "-nt", "1"]
    }
  ]
}"""
        }
    },
    {
        "id": "orca-water-sp",
        "name": "ORCA 水分子 B3LYP 几何优化范本 (Water DFT Optimization)",
        "adapter": "orca",
        "category": "量子化学 (DFT)",
        "recommended_hardware": "CPU (4-16核)",
        "description": "标准密度泛函 (DFT) 量化计算范本，使用 B3LYP/def2-SVP 泛函基组对水分子执行基态几何结构优化与单点能计算。",
        "files_tree": [
            {"path": "water.inp", "desc": "ORCA 核心输入控制文件 (泛函、基组、电荷自旋与笛卡尔坐标)"},
            {"path": "chemcompute.json", "desc": "自动化执行工作流 (调用 orca water.inp)"}
        ],
        "file_previews": {
            "water.inp": """! B3LYP def2-SVP Opt TightSCF
* xyz 0 1
O   0.000000   0.000000   0.117300
H   0.000000   0.757200  -0.469200
H   0.000000  -0.757200  -0.469200
*
""",
            "chemcompute.json": """{
  "name": "ORCA Water B3LYP Geometry Optimization",
  "steps": [
    {
      "subcommand": "orca",
      "arguments": ["water.inp"]
    }
  ]
}"""
        }
    }
]


@router.get("", response_model=List[Dict[str, Any]])
def list_templates():
    """List all available calculation input templates."""
    res = []
    for t in TEMPLATES_CATALOG:
        item = {k: v for k, v in t.items() if k != "file_previews"}
        item["available_files"] = list(t.get("file_previews", {}).keys())
        res.append(item)
    return res


@router.get("/{template_id}", response_model=Dict[str, Any])
def get_template(template_id: str):
    """Get full details of a specific calculation template."""
    for t in TEMPLATES_CATALOG:
        if t["id"] == template_id:
            return t
    raise HTTPException(status_code=404, detail="Template not found")


@router.get("/{template_id}/download")
def download_template_bundle(template_id: str):
    """Download the ready-to-run calculation input zip bundle for the given template."""
    matched = None
    for t in TEMPLATES_CATALOG:
        if t["id"] == template_id:
            matched = t
            break

    if not matched:
        raise HTTPException(status_code=404, detail="Template not found")

    # If it is gromacs-water-smoke and local release bundle exists, we can use it or generate it
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for filename, content in matched["file_previews"].items():
            z.writestr(filename, content)

    buf.seek(0)
    filename = f"ChemCompute-Template-{template_id}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
