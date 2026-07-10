# WithEase

**Modular accessibility assistant for people with motor impairments.**

> **Built with vibe coding — and proud of it.**
> I'll be upfront: WithEase was created with AI-assisted "vibe coding". I don't
> think that's anything to be ashamed of nowadays. Quite the opposite — it's
> what let me turn my own idea into a program that people can actually use, and
> share it instead of keeping it to myself. Passing up that chance would have
> been the worse choice. Feedback and contributions are very welcome.

WithEase runs quietly in the system tray and helps users with limited motor control work more comfortably with mouse and keyboard. Every feature is a separate module that can be switched on or off individually – so the program never feels overloaded.

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

### Cursor Highlight
- Pulsing rings around the pointer to find it instantly – choose the open,
  logo-style ring or a closed circle; colour, size and duration configurable
- Optional direction arrow from the screen centre toward the cursor
- Automatically hides over fullscreen videos and games

### Add-on Modules (install from the in-app store)
- **Drink break** – reminds you to drink at a configurable interval, as a
  discreet pop-up or a full-screen rain overlay
- **Dictation** – speech to text via Whisper (cloud: OpenRouter / OpenAI /
  Groq, or local on your PC in the source version)

### General
- Emergency stop: one key disables everything instantly (also in tray menu)
- Autostart, light / dark / high-contrast themes, adjustable font size, language
- In-app **module store**: browse and one-click install add-on modules
- Own app logo shown in the taskbar, window and tray

### Action Manager
No hardcoded shortcuts. Instead:
1. Define an **Action** (e.g. "Center mouse")
2. Assign any **trigger** to it (keyboard key, mouse button, macro pad, foot switch, voice command, gamepad, eye tracker …)

This makes WithEase input-device agnostic – new devices can be supported without touching the core logic.

---

## Download & Run (no installation needed)

1. Open the [**Releases**](https://github.com/XelaGibiel/WithEase/releases) page and download `WithEase-<version>-win64.zip`.
2. Unpack the ZIP (right-click → *Extract All …*).
3. Open the `WithEase` folder and double-click **`WithEase.exe`**.

WithEase then runs in the system tray (bottom right). Double-click the tray
icon for the settings; a single click opens the menu. No Python required.

> Windows SmartScreen may warn about the unsigned app – click *More info* →
> *Run anyway*.

### Run from source (for developers)

> Requires Python 3.11 or newer.

```bash
git clone https://github.com/XelaGibiel/WithEase.git
cd WithEase
pip install -r requirements.txt
python -m withease
```

To build the standalone `WithEase.exe` yourself, run `BUILD_EXE.bat`
(needs `pip install pyinstaller`); the result is in `dist/WithEase/`.

---

## Extending WithEase

Every feature is a module. Optional add-ons live in [`examples/`](examples/)
and are loaded from `%APPDATA%/WithEase/modules/` – see
[docs/MODULE_GUIDE.md](docs/MODULE_GUIDE.md). The in-app **module store**
installs the official add-ons for you.

---

## Contributing

Contributions are very welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.

---

## License

MIT License – see [LICENSE](LICENSE).
