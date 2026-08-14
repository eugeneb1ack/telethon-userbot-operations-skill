# Installation

This package installs exactly one Hermes skill: **`userbot`**. It never replaces an existing local userbot project.

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

For the default Hermes profile:

```bash
PACKAGE="$HOME/telethon-userbot-skill"
TARGET="$HOME/.hermes/skills/openclaw-imports/userbot"
BACKUPS="$HOME/.hermes/skill-backups"

if [ -e "$TARGET" ]; then
  mkdir -p "$BACKUPS"
  mv "$TARGET" "$BACKUPS/userbot-$(date +%Y%m%d-%H%M%S)"
fi
mkdir -p "$TARGET"
rsync -a --exclude '.git' --exclude '.DS_Store' "$PACKAGE/" "$TARGET/"

export USERBOT_SKILL_DIR="$TARGET"
hermes skills list | grep -E '(^|/)userbot'
```

The backup sits **outside** `~/.hermes/skills/`, so Hermes cannot discover a stale second copy. Start a new Hermes session or use `/reset` after installation.

For a named profile, choose its profile-local target instead:

```bash
PROFILE=<profile-name>
PACKAGE="$HOME/telethon-userbot-skill"
TARGET="$HOME/.hermes/profiles/$PROFILE/skills/openclaw-imports/userbot"
BACKUPS="$HOME/.hermes/profiles/$PROFILE/skill-backups"

if [ -e "$TARGET" ]; then
  mkdir -p "$BACKUPS"
  mv "$TARGET" "$BACKUPS/userbot-$(date +%Y%m%d-%H%M%S)"
fi
mkdir -p "$TARGET"
rsync -a --exclude '.git' --exclude '.DS_Store' "$PACKAGE/" "$TARGET/"

export USERBOT_SKILL_DIR="$TARGET"
hermes -p "$PROFILE" skills list | grep -E '(^|/)userbot'
```

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

cp accounts/main.env.example accounts/main.env
chmod 600 accounts/main.env
# Fill API_ID, API_HASH and PHONE_NUMBER locally in accounts/main.env.

./run.sh --account main
```

Telegram code and 2FA are entered **only by the owner in that local terminal**. Never paste them, the `.session`, API hash, phone number, or `accounts/main.env` into an agent chat or GitHub.

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
