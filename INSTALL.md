# Installation Guide

<p align="center">
  <a href="INSTALL.md"><strong>English</strong></a> ·
  <a href="INSTALL.ru.md">Русский</a>
</p>

This guide installs the **`userbot`** skill for an AI coding agent and creates a separate local Telethon runtime for the account owner. It is written so you can give the entire document to an agent and let it complete every non-secret step.

> [!CAUTION]
> **Installing this skill gives an AI agent the technical ability to operate your Telegram account.** The repository is secret-free, but the runtime created later contains a powerful Telegram session. The agent must never request, read, type, copy, upload, or commit your API hash, phone number, login code, 2FA password, account env file, or `.session` file.

## The installation boundary

There are three separate locations:

| Location | Suggested path | Purpose |
|---|---|---|
| Source checkout | `~/telethon-userbot-skill` | Git clone used for validation and updates |
| Installed Codex skill | `~/.agents/skills/userbot` for current Codex Desktop | Files discovered by Codex |
| Private runtime | `~/Documents/telethon-userbot` | Account profile, session, logs, event DB, media |

Never place account or runtime files in the source checkout or installed skill.

## Give this guide to an agent

Copy the following block into a coding agent, or send the agent a link to this file after the repository is public:

```text
Install Telegram Userbot Skill from:
https://github.com/eugeneb1ack/telethon-userbot-operations-skill

Follow INSTALL.md from that repository. The desired result is:
1. A validated source checkout at ~/telethon-userbot-skill.
2. The skill installed as userbot in the documented skill directory for this agent.
3. A new private runtime at ~/Documents/telethon-userbot, but only if that path does not already exist.
4. A project virtual environment with requirements installed.
5. Offline package, bootstrap, and session-metadata checks completed.

Safety requirements:
- Inspect existing source, skill, and runtime paths before writing.
- Do not overwrite, merge into, delete, or bootstrap over an existing runtime.
- If an installed userbot skill already exists, inspect it and ask before replacing or backing it up.
- Do not ask for or inspect API_ID, API_HASH, phone number, Telegram code, 2FA password, account env contents, or .session contents.
- Do not perform the first Telegram login for the user.
- If the owner does not have an API ID/API hash yet, guide them through section 0: provide the exact official link, explain where to click, and distinguish these credentials from a BotFather token. Never ask the owner to send the resulting values.
- Stop after dependencies are installed and tell the owner to run the two owner-only commands from INSTALL.md in their own local terminal.
- After the owner confirms login, run the offline verifier. Run the online authorization check only with explicit approval.
- Do not make any Telegram write as part of installation.
- Report the exact installed paths and every verification command that passed.
```

The rest of this document is the deterministic procedure the agent should follow.

## Requirements

- macOS or Linux. Windows users should use WSL; native Windows is not currently validated.
- Python 3.10 or newer.
- Git.
- Network access for cloning the repository and installing Python dependencies.
- A Telegram account owned by the person performing the local login.
- An API ID and API hash created by the owner at [my.telegram.org/apps](https://my.telegram.org/apps).

The API hash is a secret. The owner enters it only into the local interactive setup script.

## 0. Get a Telegram API ID and API hash

These are **Telegram application** credentials that allow Telethon to connect to your personal account. They are not a Telegram bot token and do not come from BotFather.

1. Open the official [my.telegram.org/apps](https://my.telegram.org/apps) page. Use the `my.telegram.org` subdomain, not the main `telegram.org` website.
2. Enter the phone number of your Telegram account in international format, such as `+12025550123`, and select `Next`.
3. Telegram sends the confirmation code through Telegram, not by SMS. Enter that code only on `my.telegram.org`.
4. After signing in, open `API development tools`.
5. If Telegram asks you to register an application, complete the required fields. The exact form may change. Neutral values suitable for a local userbot include:
   - `App title`: for example, `Local Telethon Userbot`;
   - `Short name`: a short Latin name such as `localuserbot`;
   - `Platform`: `Desktop`;
   - `URL`: leave it blank if the field is optional;
   - `Description`: for example, `Local Telegram client for my own account`.
6. After the application is created, the page displays `App api_id` and `App api_hash`. The `api_id` is a number; the `api_hash` is a 32-character hexadecimal string.
7. Do not send these values to an agent or paste them into a GitHub issue, chat, or README. Keep the page open and later enter both values yourself into the local `setup_account.py` prompt in step 8.

Do not use an API ID/API hash copied from an example or somebody else's repository. If an application is already registered for this number, `API development tools` displays its existing credentials; you do not need a new Telegram account for this installation.

When an agent is guiding the installation, it must explain this section one action at a time and wait for a confirmation such as “I have the API ID and API hash.” The agent does not need the values themselves.

## 1. Clone the source

For a fresh installation, the destination must not already exist:

```bash
export USERBOT_SKILL_SOURCE="$HOME/telethon-userbot-skill"

git clone https://github.com/eugeneb1ack/telethon-userbot-operations-skill.git \
  "$USERBOT_SKILL_SOURCE"
cd "$USERBOT_SKILL_SOURCE"
```

If that path already exists, do not clone over it. Verify that it is this repository, inspect `git status`, and preserve any local work before updating.

## 2. Validate before installing

These checks do not connect to Telegram:

```bash
cd "$USERBOT_SKILL_SOURCE"

python3 scripts/validate_package.py
python3 scripts/test_bootstrap_userbot_project.py
python3 scripts/test_interactive_account_setup.py
python3 scripts/test_session_checker.py
```

Expected success markers include:

```text
package_validation=ok
bootstrap_userbot_project_test=ok
Ran 3 tests ... OK
session_checker_test=ok
```

Do not continue if package validation reports forbidden runtime paths or potential credentials.

## 3. Install the skill in Codex

Current Codex Desktop uses `~/.agents/skills` for user-installed skills. This is separate from bundled system skills under `.codex/skills/.system`; do not copy this repository there. If your Codex version explicitly reports a different user-skill directory, use the path it reports instead of guessing.

```bash
export CODEX_USER_SKILLS_DIR="$HOME/.agents/skills"
export USERBOT_SKILL_DIR="$CODEX_USER_SKILLS_DIR/userbot"

test ! -e "$USERBOT_SKILL_DIR"
mkdir -p "$USERBOT_SKILL_DIR"
rsync -a --exclude '.git' --exclude '.DS_Store' \
  "$USERBOT_SKILL_SOURCE/" "$USERBOT_SKILL_DIR/"

test -f "$USERBOT_SKILL_DIR/SKILL.md"
rg -n '^name: userbot$' "$USERBOT_SKILL_DIR/SKILL.md"
python3 "$USERBOT_SKILL_DIR/scripts/validate_package.py"
```

If `test ! -e` fails, an installed skill already exists. Stop, inspect it, and ask the owner before changing it. A safe replacement uses a timestamped backup outside the active `skills/` directory; never silently overwrite a customized skill.

Start a new Codex task after installation so skill discovery refreshes. The skill can be invoked explicitly as `$userbot` and can also activate for relevant Telegram requests.

## 4. Install for another agent

If the agent supports `SKILL.md` packages, use the skills directory documented by that product and install the repository under the exact name `userbot`. Do not guess the directory.

```bash
export AGENT_SKILLS_DIR="<directory documented by the agent>"
export USERBOT_SKILL_DIR="$AGENT_SKILLS_DIR/userbot"

test ! -e "$USERBOT_SKILL_DIR"
mkdir -p "$USERBOT_SKILL_DIR"
rsync -a --exclude '.git' --exclude '.DS_Store' \
  "$USERBOT_SKILL_SOURCE/" "$USERBOT_SKILL_DIR/"
```

If the agent has no skill system but can run shell commands, it can still use the runtime's JSON CLI. It must call the same runtime rather than opening another copy of the Telegram session.

## 5. Decide whether a runtime already exists

The recommended private runtime path is:

```bash
export USERBOT_ROOT="$HOME/Documents/telethon-userbot"
```

If this path already contains a working userbot, **do not run the bootstrap script**. Point the skill to that runtime and validate it separately. The bootstrap intentionally refuses existing destinations because it is not an upgrade or merge tool.

Continue to the next step only when `USERBOT_ROOT` does not exist.

## 6. Bootstrap a new private runtime

First print the plan:

```bash
python3 "$USERBOT_SKILL_DIR/scripts/bootstrap_userbot_project.py" \
  --destination "$USERBOT_ROOT"
```

Review the JSON. It must report `dry_run: true` and `destination_exists: false`. Then create the project:

```bash
python3 "$USERBOT_SKILL_DIR/scripts/bootstrap_userbot_project.py" \
  --destination "$USERBOT_ROOT" --execute
```

The template creates source code and `accounts/main.env.example`. It does not contain credentials, a Telegram session, runtime databases, logs, or media.

## 7. Create the Python environment

```bash
cd "$USERBOT_ROOT"

python3 -m venv venv
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m compileall -q . -x '/(venv|\.git|__pycache__)/'
venv/bin/python -m unittest discover -s tests -v
```

At this point the agent must stop. Account setup and first login belong to the owner.

## 8. Owner-only account setup and first login

Run these commands yourself in a trusted local terminal, not through an agent:

```bash
cd "$HOME/Documents/telethon-userbot"
python3 scripts/setup_account.py --account main
./run.sh --account main
```

The setup script asks locally for API ID, hidden API hash, phone number, and session name. The Telethon launcher then asks for the Telegram login code and, if enabled, the 2FA password.

If you do not have the API ID/API hash yet, return to [step 0](#0-get-a-telegram-api-id-and-api-hash). Do not enter a BotFather bot token here; it is a different credential type.

- Type the code and password yourself.
- Do not paste them into an agent chat.
- Do not let the agent read the terminal, account file, or session database.
- After `Юзербот запущен!` appears, the session is registered. You may press `Ctrl+C`.

The expected session path for account `main` is:

```text
~/Documents/telethon-userbot/runtime/main/sessions/main.session
```

Do not create an empty session manually and do not edit its SQLite tables.

## 9. Verify the session safely

The agent may perform the offline check after the owner confirms that local login is complete:

```bash
export USERBOT_ROOT="$HOME/Documents/telethon-userbot"
export USERBOT_PY="$USERBOT_ROOT/venv/bin/python"

"$USERBOT_PY" "$USERBOT_SKILL_DIR/scripts/verify_userbot_session.py" \
  --project-root "$USERBOT_ROOT" --account main
```

The offline verifier checks paths, SQLite readability, and file permissions. It does not print session contents and does not connect to Telegram.

With explicit owner approval, verify authorization online:

```bash
"$USERBOT_PY" "$USERBOT_SKILL_DIR/scripts/verify_userbot_session.py" \
  --project-root "$USERBOT_ROOT" --account main --online
```

The online check is read-only: it calls `connect()` and `is_user_authorized()` and refuses interactive login.

## 10. Smoke-test the on-demand gateway

```bash
cd "$USERBOT_ROOT"

venv/bin/python scripts/userbotctl.py --account main status
venv/bin/python scripts/userbotctl.py --account main recent-dms --limit 3
venv/bin/python scripts/userbotctl.py --account main events list --unread
```

`userbotctl.py` reuses a live Unix socket or starts a gateway-only process. The on-demand owner exits after 60 seconds without local RPC activity. One account lock prevents another process from opening the same session concurrently.

Installation is complete when:

- the skill is discoverable as `userbot`;
- package and bootstrap tests pass;
- the private runtime has its own `venv`;
- the owner completed local login without sharing secrets;
- offline verification passes;
- optional online verification reports `authorized: true`;
- `userbotctl.py --account main status` returns valid JSON.

## Updating safely

Update the secret-free source checkout first:

```bash
cd "$USERBOT_SKILL_SOURCE"
git status --short
git pull --ff-only

python3 scripts/validate_package.py
python3 scripts/test_bootstrap_userbot_project.py
python3 scripts/test_interactive_account_setup.py
python3 scripts/test_session_checker.py
```

Do not pull when the checkout has unexplained local changes. After validation, update the installed skill deliberately. Preserve a customized installed copy by backing it up outside the active skills directory first.

Never re-run bootstrap against an existing runtime. Runtime source updates must preserve `accounts/`, `runtime/`, sessions, logs, and local data and should be reviewed like normal code changes.

## Troubleshooting

| Symptom | Correct response |
|---|---|
| Repository clone fails | Confirm the repository is public and the HTTPS URL is correct; do not ask for GitHub credentials in chat. |
| `python3` is too old | Install Python 3.10+ and recreate the virtual environment. |
| Installed `userbot` directory already exists | Inspect it and ask before backup or replacement; do not overwrite it. |
| Runtime destination already exists | Do not bootstrap. Determine whether it is a working runtime and preserve it. |
| `session.present: false` | The owner must run the local first-login flow. The agent must not perform it. |
| `safe_permissions: false` | Run `chmod 600` on the reported account/session file without printing its contents. |
| `authorized: false` | The owner should create a new local session; do not delete the previous session impulsively. |
| `PermissionError` under runtime logs in a sandbox | Request narrowly scoped access for the exact runtime command. Docker does not bypass the host sandbox. |
| Session lock is busy | Reuse the gateway or stop the verified idle owner; never launch a second Telethon client against the same session. |

For session import and recovery, read [references/session-bootstrap.md](references/session-bootstrap.md). For the complete security contract, read [SECURITY.md](SECURITY.md).
