# Security model

Use only on loopback or a controlled encrypted Tailscale network. The app has no built-in TLS. Administrator access can schedule compute processes and must be limited to trusted operators. No embedded admin password or network auth key exists in the installer.

Invites expire, are single-use, and are stored as SHA-256 hashes. Per-node random bearer tokens are stored hashed by the controller. The node retains its token locally in YAML. The runtime admin token remains in a local secret file and is not logged. Local account compromise exposes these credentials. Runtime directories are restricted to the current Windows user and administrators. Revoking/deleting a node invalidates its token; re-enroll using a fresh invite.

API writes require explicit bearer/admin headers, not ambient cookies. Cross-origin credential access is not enabled. The web client renders untrusted fields using escaping. Node telemetry should be treated as self-reported data, not proof of hardware performance.

The GROMACS adapter is an allowlist, not an OS sandbox. Only trusted input files and a trusted GROMACS installation should be used. No rate limiting, distributed transaction scheduler, encrypted storage, or external security audit is claimed. Do not expose to the public internet.

Report vulnerabilities privately through the repository owner's GitHub contact or private vulnerability reporting when enabled. Do not include actual tokens, scientific inputs, or private logs in public issues.
