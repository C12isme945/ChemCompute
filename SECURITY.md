# Security model

Use loopback, a controlled encrypted Tailscale network, or a configured HTTPS Cloudflare Tunnel/reverse proxy. The app has no built-in TLS and its origin must remain loopback-only for public tunnel deployments. Administrator access can schedule compute processes and must be limited to trusted operators. No embedded admin password or network auth key exists in the installer.

Invites expire, are single-use, and are stored as SHA-256 hashes. Per-node random bearer tokens are stored hashed by the controller. The node retains its token locally in YAML. The runtime admin token remains in a local secret file and is not logged. Local account compromise exposes these credentials. Runtime directories are restricted to the current Windows user and administrators. Revoking/deleting a node invalidates its token; re-enroll using a fresh invite.

API writes require explicit bearer/admin headers, not ambient cookies. Cross-origin credential access is not enabled. The web client renders untrusted fields using escaping. Node telemetry should be treated as self-reported data, not proof of hardware performance.

The GROMACS adapter is an allowlist, not an OS sandbox. Only trusted input files and a trusted GROMACS installation should be used. Desktop AI and remote-admin credentials use current-user Windows DPAPI; scientific data and other runtime files are not encrypted at rest. ZIP uploads are bounded and checked before extraction. Task claims are transactional, but no distributed lease recovery, rate limiting, or external security audit is claimed. DeepSeek output can select only an eligible node, never executable code. Do not expose to the public internet.

Report vulnerabilities privately through the repository owner's GitHub contact or private vulnerability reporting when enabled. Do not include actual tokens, scientific inputs, or private logs in public issues.
