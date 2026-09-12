"""
Generate test_bundle.zip for verification and testing.
"""

import zipfile
from pathlib import Path

DIR = Path(__file__).resolve().parent


def make_bundle():
    zip_path = DIR / "test_bundle.zip"
    files = ["min.mdp", "conf.gro", "topol.top"]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in files:
            p = DIR / fname
            if p.exists():
                zf.write(p, arcname=fname)
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    print(f"[OK] Generated {zip_path} with {len(files)} files.")


if __name__ == "__main__":
    make_bundle()
