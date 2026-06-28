# AccessMate

**Modular accessibility assistant for people with motor impairments.**

AccessMate runs quietly in the system tray and helps users with limited motor control work more comfortably with mouse and keyboard. Every feature is a separate module that can be switched on or off individually – so the program never feels overloaded.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![Status](https://img.shields.io/badge/Status-Alpha-orange)

---

## Features

### Mouse Module
- Automatic cursor centering after inactivity (configurable delay + countdown tooltip)
- Centering abortable by any mouse movement or key press
- Manual centering via freely assignable hotkey
- Precision mode (slow, controlled cursor movement)
- Click-Lock (hold left button without physical press)
- Keyboard keys as left / right / double click
- Screen zones: jump cursor to predefined screen regions via hotkey

### Keyboard Module
- Key delay: prevents unintended repeated keystrokes when a key is held
- Per-key exception list
- Sticky Keys: Shift, Ctrl, Alt, Win – press once, stays active until next non-modifier key
- Live modifier status display in the GUI

### Macros Module
- Macro mode: press trigger key → press second key → action executes → macro mode ends
- Supported actions: type text, send key combination, launch app/script

### Profiles
- Create unlimited profiles (e.g. Work, Home, Gaming, Guest)
- Guest profile disables all assistance features
- One-click profile switching from the tray menu

### General
- Emergency stop: one key disables everything instantly (also in tray menu)
- Autostart, light/dark mode, language, tray settings

### Action Manager
No hardcoded shortcuts. Instead:
1. Define an **Action** (e.g. "Center mouse")
2. Assign any **trigger** to it (keyboard key, mouse button, macro pad, foot switch, voice command, gamepad, eye tracker …)

This makes AccessMate input-device agnostic – new devices can be supported without touching the core logic.

---

## Planned / Future Modules
- **AI Module** (v2, fully optional): Speech-to-text (local Whisper or cloud), text rewriting, translation, custom AI workflows
- **Plugin API** (v3): Third-party developers can add new modules (Eye Tracking, Tobii, Xbox Adaptive Controller, Stream Deck, MIDI, Sip-and-Puff, foot switches …)

---

## Installation

> Requires Python 3.11 or newer.

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/accessmate.git
cd accessmate

# Install dependencies
pip install -r requirements.txt

# Run
python -m accessmate
```

Or install as a package:

```bash
pip install -e .
accessmate
```

---

## Project Structure

```
src/accessmate/
├── __main__.py          # Entry point
├── app.py               # Application controller
├── tray.py              # System tray
├── core/
│   ├── config.py        # JSON settings & profiles
│   ├── action_manager.py # Trigger → Action mapping
│   └── event_bus.py     # Module communication
├── modules/
│   ├── base.py          # Base class for all modules
│   ├── mouse.py
│   ├── keyboard.py
│   └── macros.py
└── gui/
    └── main_window.py   # Settings window
```

---

## Contributing

Contributions are very welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.

---

## License

MIT License – see [LICENSE](LICENSE).
