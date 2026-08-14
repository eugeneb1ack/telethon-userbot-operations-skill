# Bootstrap note

This project was generated from the canonical Hermes `userbot` skill. Its source template contains the current verified modules, but no Telegram account configuration, session, runtime data, or credentials.

1. Create `venv` and install `requirements.txt`.
2. Copy `accounts/main.env.example` to `accounts/main.env` locally and fill it yourself.
3. Run `./run.sh --account main` and complete Telegram login in your own terminal.
4. Use the installed skill's `references/session-bootstrap.md` to verify readiness.

For feature work: run `scripts/userbot_module_registry.py` first. If no route exists, follow `docs/TELETHON_MODULE_AUTHORING.md`, add tests, then update the canonical source package intentionally.
