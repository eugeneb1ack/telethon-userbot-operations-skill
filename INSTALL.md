# Installation

This package contains one agent-neutral skill named **`userbot`** and a source template for an on-demand Telethon runtime. The Telegram session belongs to one shared runtime; each agent calls the same local JSON CLI.

## 1. Clone and validate

```bash
git clone git@github.com:eugeneb1ack/telethon-userbot-operations-skill.git \
  "$HOME/telethon-userbot-skill"
cd "$HOME/telethon-userbot-skill"

python3 scripts/validate_package.py
python3 scripts/test_bootstrap_userbot_project.py
python3 scripts/test_session_checker.py
```

These checks have no Telegram network I/O. Keep this checkout as the canonical source for every agent:

```bash
export USERBOT_SKILL_SOURCE="$HOME/telethon-userbot-skill"
```

Never copy Telegram account files, session databases, or runtime data into the skill checkout.

## 2. Install in Codex

```bash
PACKAGE="$USERBOT_SKILL_SOURCE"
TARGET="$HOME/.codex/skills/userbot"
BACKUPS="$HOME/.codex/skill-backups"

if [ -e "$TARGET" ]; then
  mkdir -p "$BACKUPS"
  mv "$TARGET" "$BACKUPS/userbot-$(date +%Y%m%d-%H%M%S)"
fi
mkdir -p "$TARGET"
rsync -a --exclude '.git' --exclude '.DS_Store' "$PACKAGE/" "$TARGET/"

export USERBOT_SKILL_DIR="$TARGET"
test -f "$TARGET/SKILL.md"
rg -n '^name: userbot$' "$TARGET/SKILL.md"
```

The backup sits outside `~/.codex/skills/`, so Codex cannot discover a stale copy. Start a new Codex task after installation. Normal reads then go through the local Unix socket instead of creating a new Telegram connection.

## 3. Install in another agent

If the agent supports `SKILL.md` packages, copy the same source into the skills directory documented by that product under the name `userbot`. Do not guess the location because it differs between agents.

```bash
AGENT_SKILLS_DIR="<directory documented by your agent>"
mkdir -p "$AGENT_SKILLS_DIR/userbot"
rsync -a --exclude '.git' --exclude '.DS_Store' \
  "$USERBOT_SKILL_SOURCE/" "$AGENT_SKILLS_DIR/userbot/"

export USERBOT_SKILL_DIR="$AGENT_SKILLS_DIR/userbot"
export USERBOT_ROOT="$HOME/Documents/telethon-userbot"
export USERBOT_PY="$USERBOT_ROOT/venv/bin/python"
```

If the agent has no skill system but can run shell commands, no additional integration is required. Give it this stable contract:

```text
For Telegram, run <USERBOT_ROOT>/venv/bin/python
<USERBOT_ROOT>/scripts/userbotctl.py --account main ... and parse JSON.
Read-only gateway operations may run directly. Before a Telegram write,
show the exact target/action/text and wait for explicit approval; then use
the existing guarded module with --execute and verify the final state.
```

An optional MCP/plugin adapter should wrap `userbotctl.py`; it must not open its own copy of `userbot.session`.

## 4. Existing runtime: do not bootstrap

If a working project already exists, point the skill at it:

```bash
export USERBOT_ROOT="$HOME/Documents/telethon-userbot"
export USERBOT_PY="$USERBOT_ROOT/venv/bin/python"
```

Do not run the bootstrap script against an existing path. It intentionally refuses to overwrite or merge a working project.

## 5. New computer: bootstrap once

Choose a path that does not exist:

```bash
export USERBOT_ROOT="$HOME/Documents/telethon-userbot"
export USERBOT_PY="$USERBOT_ROOT/venv/bin/python"

python3 "$USERBOT_SKILL_DIR/scripts/bootstrap_userbot_project.py" \
  --destination "$USERBOT_ROOT"
python3 "$USERBOT_SKILL_DIR/scripts/bootstrap_userbot_project.py" \
  --destination "$USERBOT_ROOT" --execute
```

The source template excludes all account, session and runtime material.

## 6. Configure the account and session

```bash
cd "$USERBOT_ROOT"
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
python3 scripts/setup_account.py --account main
./run.sh --account main
```

`setup_account.py` collects API ID, hidden API hash and visible phone number only in the local terminal and writes `accounts/main.env` with mode `600`. Telegram code and 2FA are also entered only in that terminal. Never paste them, `.session`, API hash, phone number, or account env content into an agent chat or GitHub.

Verify without exposing account material:

```bash
"$USERBOT_PY" "$USERBOT_SKILL_DIR/scripts/verify_userbot_session.py" \
  --project-root "$USERBOT_ROOT" --account main
```

See [references/session-bootstrap.md](references/session-bootstrap.md) for session import and recovery.

## 7. On-demand gateway and webhook

The gateway is enabled by default. Configure the optional signed outbound webhook locally:

```bash
cd "$USERBOT_ROOT"
venv/bin/python scripts/setup_gateway.py
```

For normal agent work, run `userbotctl.py` directly. It starts the gateway when
needed and the process exits 60 seconds after the last local RPC. On-demand mode
creates no autostart entry and refuses interactive login:

```bash
./run.sh --account main

# Manual lifecycle commands are only for diagnosis or a longer batch.
venv/bin/python scripts/userbotd.py --account main start
venv/bin/python scripts/userbotd.py --account main status
venv/bin/python scripts/userbotd.py --account main stop
```

Verify through the local socket:

```bash
venv/bin/python scripts/userbotctl.py --account main status
venv/bin/python scripts/userbotctl.py --account main recent-dms --limit 3
venv/bin/python scripts/userbotctl.py --account main events list --unread
```

See [references/gateway-webhooks.md](references/gateway-webhooks.md) for the event schema, HMAC verification, privacy controls and retry behavior.

An optional `scripts/install_gateway_service.py` can supervise continuous event
or webhook monitoring for the current login session. Its plist stays under
`runtime/`; it never writes to `~/Library/LaunchAgents` and therefore is not
login autostart. Do not run it for normal agent requests.

## Update

```bash
cd "$USERBOT_SKILL_SOURCE"
git pull --ff-only
python3 scripts/validate_package.py
python3 scripts/test_bootstrap_userbot_project.py
python3 scripts/test_session_checker.py
```

Repeat the matching agent installation step. Apply source updates to an existing runtime deliberately; never replace its `accounts/` or `runtime/` directories.
