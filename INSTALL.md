# Installation

This package installs exactly one Codex skill: **`userbot`**. It never replaces an existing local userbot project.

## 1. Clone and validate the private package

```bash
git clone git@github.com:eugeneb1ack/telethon-userbot-operations-skill.git \
  "$HOME/telethon-userbot-skill"
cd "$HOME/telethon-userbot-skill"

python3 scripts/validate_package.py
python3 scripts/test_bootstrap_userbot_project.py
python3 scripts/test_session_checker.py
```

These checks have no Telegram network I/O.

## 2. Install the one canonical skill

Install it in Codex's user skill directory:

```bash
PACKAGE="$HOME/telethon-userbot-skill"
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

The backup sits **outside** `~/.codex/skills/`, so Codex cannot discover a stale second copy. Start a new Codex task after installation so it loads the installed skill.

## 3. Existing userbot project: do not bootstrap

If you already have a working project, point the skill at it and stop there:

```bash
export USERBOT_ROOT="$HOME/Documents/telethon-userbot"
export USERBOT_PY="$USERBOT_ROOT/venv/bin/python"
```

Do **not** run the bootstrap script against this path. It is intentionally refused when the destination exists.

## 4. New computer: bootstrap a new project safely

Choose a path that does not yet exist:

```bash
export USERBOT_ROOT="$HOME/Documents/telethon-userbot"
export USERBOT_PY="$USERBOT_ROOT/venv/bin/python"

# Inspect only; creates nothing.
python3 "$USERBOT_SKILL_DIR/scripts/bootstrap_userbot_project.py" \
  --destination "$USERBOT_ROOT"

# Create exactly this new directory after reviewing the plan.
python3 "$USERBOT_SKILL_DIR/scripts/bootstrap_userbot_project.py" \
  --destination "$USERBOT_ROOT" --execute
```

The source template contains current userbot code and modules but excludes all account/session/runtime material.

## 5. Configure a local account and create a session

```bash
cd "$USERBOT_ROOT"
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt

python3 scripts/setup_account.py --account main

./run.sh --account main
```

`setup_account.py` asks for API ID, API hash and phone number only in the local terminal, validates their format, writes `accounts/main.env` with permissions `600`, hides the API hash, and leaves the phone number visible for the owner to verify. A Russian number may be entered as either `+79991234567` or `79991234567`; the latter is normalized to the former before it is saved. It refuses to overwrite an existing account profile unless the owner uses `--replace` and types `REPLACE` locally. The launcher reports Telegram's selected delivery route before asking for a code. Telegram code and 2FA are entered **only by the owner in that local terminal**. Never paste them, the `.session`, API hash, phone number, or `accounts/main.env` into an agent chat or GitHub.

Then verify safely:

```bash
"$USERBOT_PY" "$USERBOT_SKILL_DIR/scripts/verify_userbot_session.py" \
  --project-root "$USERBOT_ROOT" --account main

# Optional explicit network check: connect() + is_user_authorized(), never login.
"$USERBOT_PY" "$USERBOT_SKILL_DIR/scripts/verify_userbot_session.py" \
  --project-root "$USERBOT_ROOT" --account main --online
```

See [references/session-bootstrap.md](references/session-bootstrap.md) for session transfer and recovery rules.

## Update the package

```bash
cd "$HOME/telethon-userbot-skill"
git pull --ff-only
python3 scripts/validate_package.py
python3 scripts/test_bootstrap_userbot_project.py
python3 scripts/test_session_checker.py
```

Repeat the matching skill-installation step. Do not overwrite an existing userbot project; project-level updates must be reviewed and applied separately.
