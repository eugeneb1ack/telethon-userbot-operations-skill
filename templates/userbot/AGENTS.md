# Repository Guidelines

## Project Structure & Module Organization
- `main.py`: entrypoint that starts `TelegramClient`, loads settings, and registers modules.
- `core/`: shared infrastructure.
- `core/config.py`: `.env` parsing and runtime settings validation.
- `core/module_loader.py`: dynamic loading of command modules from `modules/`.
- `modules/`: feature modules. Each module is a standalone command plugin (example: `modules/ping.py`).
- `.env.example`: required environment variables template.
- `requirements.txt`: runtime dependencies.

## Build, Test, and Development Commands
- `python3 -m venv venv && source venv/bin/activate`: create and activate local virtualenv.
- `pip install -r requirements.txt`: install dependencies.
- `cp .env.example .env`: initialize local config.
- `./run.sh`: run the userbot locally через `venv/bin/python`.
- `./run.sh --account <name>`: run isolated account profile from `accounts/<name>.env`.
- `python -m py_compile main.py core/*.py modules/*.py`: quick syntax check before commit.

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation and explicit type hints where practical.
- Use `snake_case` for functions, variables, and module filenames.
- Keep modules small and focused: one command/feature per file in `modules/`.
- Every module must expose `register(client)` so the loader can attach handlers.
- Prefer structured logging (`logging.getLogger(__name__)`) over ad-hoc prints for diagnostics.
- Before inventing a module or one-off, run `venv/bin/python scripts/userbot_module_registry.py --query '<natural request>'`; use the returned CLI when it exists. For genuinely new Telethon operations, follow `docs/TELETHON_MODULE_AUTHORING.md`. It defines the API lookup, dry-run, verification, and fake-client test contract.

## Testing Guidelines
- The offline regression suite is `venv/bin/python -m unittest discover -s tests -v`.
- Minimum validation for each change: compile all source, run the offline suite, run every module's `--help`, and load modules against a fake client so no Telegram session is touched.
- Do not use a real `.env` or Telegram network call as a default smoke test. Test authorized-session behavior only when the user explicitly requests live verification.
- Add focused `unittest` cases under `tests/` for new safety guards, parsers, pure planning logic, and mocked client behavior.

## Commit & Pull Request Guidelines
- Current history is mostly automated (`Shield: Hourly autosave`); use clear human-authored commits going forward.
- Recommended format: `type(scope): short imperative summary` (for example, `feat(modules): add .help command`).
- PRs should include: what changed and why, manual verification steps with command output snippets, linked issue/task (if available), and screenshots/log excerpts when behavior is user-visible.

## Security & Configuration Tips
- Never commit `.env`, session files, API credentials, or phone numbers.
- Keep `userbot.session` local only; rotate credentials if accidentally exposed.
- Only `main.py` / `./run.sh` may use interactive `client.start()` for the owner’s first local Telegram login. Direct helpers must use `connect()` + `is_user_authorized()` and refuse login.
- Never ask for, print, inspect, copy, upload, or commit Telegram login codes, 2FA passwords, `.session` contents, API hashes, or account env files. Session readiness is checked only through file metadata and optional `is_user_authorized()`.
