# Installation

## 1. Clone the private repository

SSH is the cleanest route when GitHub SSH access is already configured:

```bash
git clone git@github.com:eugeneb1ack/telethon-userbot-operations-skill.git \
  "$HOME/telethon-userbot-operations-skill"
```

## 2. Validate before installing

```bash
cd "$HOME/telethon-userbot-operations-skill"
python3 scripts/validate_package.py
```

This validates only the package structure and Python syntax. It does not connect to Telegram.

## 3. Install into the default Hermes profile

```bash
mkdir -p "$HOME/.hermes/skills/social-media"
rm -rf "$HOME/.hermes/skills/social-media/telethon-userbot-operations"
cp -R "$HOME/telethon-userbot-operations-skill" \
  "$HOME/.hermes/skills/social-media/telethon-userbot-operations"
rm -rf "$HOME/.hermes/skills/social-media/telethon-userbot-operations/.git"
```

Start a new Hermes session or use `/reset` so the new skill index is loaded.

## 4. Install into a named profile

Use a profile-local skill directory instead:

```bash
PROFILE=<profile-name>
mkdir -p "$HOME/.hermes/profiles/$PROFILE/skills/social-media"
rm -rf "$HOME/.hermes/profiles/$PROFILE/skills/social-media/telethon-userbot-operations"
cp -R "$HOME/telethon-userbot-operations-skill" \
  "$HOME/.hermes/profiles/$PROFILE/skills/social-media/telethon-userbot-operations"
rm -rf "$HOME/.hermes/profiles/$PROFILE/skills/social-media/telethon-userbot-operations/.git"
```

Verify from that exact profile:

```bash
hermes -p "$PROFILE" skills list | grep telethon-userbot-operations
```

## 5. Configure the userbot project location

The package defaults to:

```text
$HOME/Documents/telethon-userbot
```

If the userbot lives elsewhere, export it before use or configure the calling environment:

```bash
export USERBOT_ROOT=/absolute/path/to/telethon-userbot
export USERBOT_PY="$USERBOT_ROOT/venv/bin/python"
```

## Upgrade

```bash
cd "$HOME/telethon-userbot-operations-skill"
git pull --ff-only
python3 scripts/validate_package.py
# Repeat the matching copy step above.
```

Do not copy `.git`, `.env`, `accounts/`, `runtime/`, sessions, downloaded media, or generated chat archives into a Hermes skill directory.
