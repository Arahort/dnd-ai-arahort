# DND AI Arahort
Independent self-host edition. Work inside this directory; opt/dnd is the separate production source. Its transfer exporter is maintained only under explicit user authorization.
Development sources are in .private/app and MUST NOT enter the public repository, image, release archive or public logs.
The public distribution contains compiled application files, installer, Compose, updater, instructions and license notices.
Author: Arahort, arahort.pro, alex@arahort.pro. Do not add generator/assistant attribution.
Preserve the current visual design and assets. New UI uses semantic elements; do not introduce h2/h3/h4/h5.
Match existing TypeScript style: no semicolons, no extra blank separator lines.
Gemini ONLY: 3.8 Flash High by default; configurable low/medium/high. No Claude/Codex provider, no PTY.
Edge TTS only. No Silero, Ollama, vectors or pgvector. Memory uses PostgreSQL Russian full-text search.
Use real HttpOnly session authentication for HTTP and Socket.IO; never trust client-supplied userId for identity.
Registration requires a one-use invitation issued by an administrator. No corporate email restriction or master password.
Passwords may be any nonempty value within the existing 256-character input bound. Never impose a minimum length in installer, CLI, API, registration or password-change/reset UI. Preserve password whitespace and Unicode exactly.
Installer asks Russia/proxy first, then domain/IP mode and administrator login/password. Support Ubuntu 22.04, 24.04 and 26.04 LTS x86_64; Python 3.10.12+ with tarfile.data_filter. Preserve existing Docker installations and running VPN services.
The author's update repository is embedded in release-source.json by publication; never ask end users for owner/repository. An empty repository is valid only for local testing without remote updates.
README is an end-user guide: recommend one-shots and preparing the world, characters and plot with AI before creating a party. Author build instructions live in docs/publishing.md.
Public GitHub distribution must not contain existing secrets, accounts, parties, uploads, old logs or development history.
Archive v2 transfers application account password hashes and stable source identity. Reuse an exact existing login without overwriting its password/privileges/disabled state; create absent accounts as ordinary users. Preserve source creator through mapping. Original opt/dnd now produces v2 through its separate password-confirmed admin transfer export; previous readable ZIPs are not imported. Archive v1 retains manual user mapping.
Updates must preserve data and authentication, drain in-flight jobs, back up before migrations, check health and handle rollback compatibility.
Do not expose Docker socket to the web application. A narrow host updater handles allowed installation operations.
Use MCP git for supported Git actions and Context7 for current library documentation.
MCP Git directory staging may bypass ignore rules. Stage explicit public filenames only; inspect the staged list. Never stage the repository root or .private/app. Enable the public guard with git config core.hooksPath .githooks.
Parallel workers own separate files, must accommodate others and must not revert their edits. Review actual files and appropriate checks after each worker completes.
