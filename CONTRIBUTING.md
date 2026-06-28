# Contributing to AccessMate

Thank you for your interest in AccessMate! Every contribution helps make computers more accessible.

## Ways to Contribute

- **Report a bug** – open an issue and use the Bug Report template
- **Suggest a feature** – open an issue and use the Feature Request template
- **Fix a bug or implement a feature** – open a pull request
- **Improve documentation** – even small fixes help
- **Test on different hardware** – especially assistive devices (foot switches, eye trackers, etc.)

## Development Setup

```bash
git clone https://github.com/YOUR_USERNAME/accessmate.git
cd accessmate
pip install -e ".[dev]"
```

Run tests:
```bash
pytest
```

Run linter:
```bash
ruff check src/
```

## Code Style

- Python 3.11+, type hints everywhere
- `ruff` for linting (config in `pyproject.toml`)
- No hardcoded strings visible to users – all user-facing text should later be translatable
- Every new module must inherit from `BaseModule` and implement `start()`, `stop()`, and `get_settings_widget()`
- Modules communicate only through the `EventBus` – never import each other directly

## Adding a New Module

1. Create `src/accessmate/modules/your_module.py`
2. Inherit from `BaseModule`, set `MODULE_ID`, `DISPLAY_NAME`, `DESCRIPTION`
3. Implement `start()`, `stop()`, `get_settings_widget()`, `load_settings()`, `dump_settings()`
4. Register your actions in `__init__()` via `action_manager.register()`
5. Add your module to the list in `app.py`
6. Add default settings to `DEFAULT_PROFILE` in `core/config.py`

## Pull Request Checklist

- [ ] Code passes `ruff check src/`
- [ ] New features have at least one test in `tests/`
- [ ] `get_settings_widget()` is implemented (even as a placeholder)
- [ ] Module settings are included in `load_settings()` and `dump_settings()`
- [ ] No hardcoded hotkeys – use the ActionManager

## Design Philosophy

> The user must never feel like they are using an overloaded program.

- Every feature must be a module that can be disabled
- Disabled modules must not appear anywhere in the UI
- The program must be fully operable without a mouse
- Accessibility features of the GUI itself are opt-in via settings, not forced on everyone
