# DND AI Arahort
Independent self-host edition. Work only inside this directory; opt/dnd is the unchanged production reference.
Development sources are in .private/app and MUST NOT enter the public repository, image, release archive or public logs.
The public distribution contains compiled application files, installer, Compose, updater, instructions and license notices.
Author: Arahort, arahort.pro, alex@arahort.pro. Do not add generator/assistant attribution.
Preserve the current visual design and assets. New UI uses semantic elements; do not introduce h2/h3/h4/h5.
Match existing TypeScript style: no semicolons, no extra blank separator lines.
Gemini ONLY: 3.8 Flash High by default; configurable low/medium/high. No Claude/Codex provider, no PTY.
Edge TTS only. No Silero, Ollama, vectors or pgvector. Memory uses PostgreSQL Russian full-text search.
Use real HttpOnly session authentication for HTTP and Socket.IO; never trust client-supplied userId for identity.
Registration requires a one-use invitation issued by an administrator. No corporate email restriction or master password.
Installer asks Russia/proxy first, then domain/IP mode and administrator login/password. Ubuntu 24.04 x86_64.
Public GitHub distribution must not contain existing secrets, accounts, parties, uploads, old logs or development history.
Export/import is a new versioned format, implemented after the main self-host functionality; no legacy ZIP compatibility required.
Updates must preserve data and authentication, drain in-flight jobs, back up before migrations, check health and handle rollback compatibility.
Do not expose Docker socket to the web application. A narrow host updater handles allowed installation operations.
Use MCP git for supported Git actions and Context7 for current library documentation.
MCP Git directory staging may bypass ignore rules. Stage explicit public filenames only; inspect the staged list. Never stage the repository root or .private/app. Enable the public guard with git config core.hooksPath .githooks.
Parallel workers own separate files, must accommodate others and must not revert their edits. Review actual files and appropriate checks after each worker completes.
