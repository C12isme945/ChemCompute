# Release procedure

1. Run `ruff check .` and `python -m pytest -q` on the final source.
2. Build with `scripts/build.ps1` using Python 3.12 and official Inno Setup 6.7.3.
3. Run `python scripts/smoke_exe.py dist/ChemCompute/ChemCompute.exe`.
4. On a test Windows account or isolated machine, install, launch, inspect the console, and uninstall. Confirm runtime data is retained. Record which checks actually ran.
5. Commit the final source and update versions in `chemcompute/__init__.py`, `pyproject.toml`, and `installer/ChemCompute.iss`.
6. Push main, inspect CI, then create and push a version tag (`v0.1.0`). The workflow creates a GitHub prerelease with the installer and SHA-256 checksums.

The initial installer is unsigned. Signing requires the maintainer's certificate and is not included. Build tool versions are pinned; OS/SDK and transitive dependencies can still vary. No guarantee of bit-for-bit reproducibility.

Do not bundle runtime YAML files, SQLite databases, credentials, scientific datasets, or unrelated repository files. Network changes are opt-in scripts, never a build or test step.
