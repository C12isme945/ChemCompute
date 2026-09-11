# Initial release validation — 2026-09-11

Verified locally on Windows x64 with Python 3.12.4 and Inno Setup 6.7.3:

- Ruff: passed.
- Pytest: 12 tests passed, including invite replay/expiry/revocation, admin and node authorization, heartbeat, credential hashing, path rejection, bounded output, and timeout termination.
- PyInstaller standalone executable: built successfully.
- Inno Setup installer: compiled successfully.
- Packaged application end-to-end: controller starts, local node enrolls and reports heartbeat, authenticated inventory returns an online node.
- Silent installer: installed to an isolated test directory, generated config, installed executable passed the same end-to-end test, uninstall removed executable and retained configuration.
- Browser: Chinese dashboard layout, login, and authenticated summary display inspected at 1280 × 720. Fixed missing summary authorization during this check.

Not verified: clean Windows VM without development tools, reboot/logoff persistence, a second physical remote computer, GPU compute execution, scientific correctness, third-party network installation, or code signing. Passing node discovery does not demonstrate GPU acceleration or scientific performance.

CI results are recorded independently in GitHub Actions; local results do not imply a successful hosted run. The installer starts in the current user's session and is not a native Windows Service.

## User-supplied implementation reference

Inspected the separate E-drive ChemCompute implementation without overwriting it. Adapted the WSL idea using direct `wsl.exe --cd ... --exec gmx` arguments instead of shell interpolation. Locally verified GROMACS 2023.3 Ubuntu, grompp and 100-step mdrun on the supplied two-water input; both exited 0, without overriding GROMACS warnings. GPU support is disabled in this installation. This is a CPU software smoke test, not evidence of scientific validity or production simulation readiness. No prior report claim of GPU acceleration was adopted.
