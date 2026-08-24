# Runtime access and sandbox preflight

Use this protocol before the first local userbot command when the private runtime is outside the harness's current workspace or its permissions are unknown. Choose a valid access route before execution instead of learning the boundary from an avoidable failure.

## Why a read request can require local writes

“Read-only” describes the Telegram side of an operation. It does not mean that the process only reads the local filesystem.

- `userbot_memory.py recall` purges expired items, updates `last_accessed_at`/`access_count`, and opens SQLite in WAL mode.
- A dialog-summary cache hit updates `last_validated_at` after the bounded Telegram tail check.
- SQLite may create or update `-wal` and `-shm` files even when the requested business result is a read.
- Gateway and direct-module routes may write bounded logs, lock/PID/socket files, progress state, and collector archives under `runtime/<account>/`.

A private runtime outside the current workspace can therefore be readable to the harness while still failing during SQLite, logging, socket, or lock initialization. This is an execution-boundary mismatch, not a missing cache, corrupt database, bad Telegram session, or wrong runtime path.

## First-call decision

Before executing anything:

1. Resolve the exact runtime, account, canonical CLI/module command, and expected local state paths. Do not inspect account env or session contents.
2. Read the harness-provided filesystem, network, Unix-socket, approval, and workspace capabilities when they are available to the agent. Do not run a write probe merely to discover an already-declared boundary.
3. If the required paths and socket are allowed, run the canonical command normally.
4. If they are outside the boundary and the harness offers scoped elevation, submit the exact canonical command through that mechanism on the first attempt with a narrow reason. Do not first issue a command that is expected to fail.
5. If the harness has no per-command elevation, ask for the smallest explicit workspace/profile grant needed by that operation. If access remains unavailable, stop without moving data or starting a competing Telegram client.

Do not narrate an expected sandbox transition as a Telegram or cache failure. If the harness auto-reviews the scoped request, continue silently. If owner approval is required, say exactly which runtime operation needs local state access and state that no Telegram write is included.

## Codex route

Codex `workspace-write` normally covers the current workspace, not a separate userbot runtime. Official Codex documentation distinguishes this sandbox boundary from the approval policy and supports additional `sandbox_workspace_write.writable_roots` plus per-command approval/elevation.

For an occasional userbot request, prefer the exact canonical command with `require_escalated` on the first call. Keep the requested scope to that command; do not approve the generic Python interpreter, a shell, or all of `$HOME`. An automatically reviewed escalation still preserves the sandbox boundary for unrelated commands.

For repeated memory-only access, the owner may explicitly opt into a custom profile whose additional writable root is the exact account data directory, for example `/absolute/path/to/telethon-userbot/runtime/main/data`. Full Telegram collection also needs operational logs/locks/socket state, so use scoped command elevation instead of broadly making the whole runtime writable. Never add `accounts/` or `runtime/<account>/sessions/` merely to remove approval prompts.

Do not edit `~/.codex/config.toml`, a permissions profile, or persistent rules without an explicit owner request. If the owner chooses a persistent setting, validate the exact active profile in a new task because permissions are established per task/session.

Official references:

- [Codex sandbox and approvals](https://learn.chatgpt.com/docs/sandboxing)
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)

## Other harnesses

Use the equivalent declared capability in the active harness: workspace roots, mount allowlists, command approvals, tool permissions, or an already-running trusted gateway. Do not assume Codex-specific parameter names exist elsewhere.

If a harness exposes no safe local-filesystem route, the correct integration is a guarded userbot gateway/MCP tool that returns bounded JSON—not copying the private SQLite/session into the agent workspace. A transport grant authorizes access to the tool only; Telegram mutations still require the normal exact preview and explicit owner approval.

## Failure handling

If a supposedly permitted canonical command still returns `PermissionError`, `Operation not permitted`, a blocked Unix socket, or denied DNS/network access:

1. classify it as a harness boundary;
2. retry the same command at most once through the narrow native access route if one remains available;
3. do not switch to raw SQLite, an ad-hoc Telethon client, Docker, `/tmp`, or a duplicate session owner;
4. report the exact denied path/capability only if the narrow retry is denied or unavailable.
