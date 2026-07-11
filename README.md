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
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey)
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

### Add-on Modules (install from the in-app library)
- **Drink break** – reminds you to drink at a configurable interval, as a
  discreet pop-up or a full-screen rain overlay
- **Dictation** – speech to text via Whisper (cloud: OpenRouter / OpenAI /
  Groq, or local on your PC in the source version). 🧪 *Still in an early
  beta – it may not work flawlessly right away.*

### General
- Emergency stop: one key disables everything instantly (also in tray menu)
- Autostart, light / dark / high-contrast themes, adjustable font size, language
- In-app **module library**: browse and one-click install add-on modules
- Own app logo shown in the taskbar, window and tray

### Action Manager
No hardcoded shortcuts. Instead:
1. Define an **Action** (e.g. "Center mouse")
2. Assign any **trigger** to it (keyboard key, mouse button, macro pad, foot switch, voice command, gamepad, eye tracker …)

This makes WithEase input-device agnostic – new devices can be supported without touching the core logic.

---

## Download & Run (no installation needed)

Open the [**Releases**](https://github.com/XelaGibiel/WithEase/releases) page
and download the archive for your system.

### Windows

1. Download `WithEase-<version>-win64.zip`.
2. Unpack the ZIP (right-click → *Extract All …*).
3. Open the `WithEase` folder and double-click **`WithEase.exe`**.

> Windows SmartScreen may warn about the unsigned app – click *More info* →
> *Run anyway*.

### Linux

1. Download `WithEase-<version>-linux64.tar.gz`.
2. Unpack it: `tar -xzf WithEase-<version>-linux64.tar.gz`.
3. Run **`./WithEase/WithEase`** (or make it executable: `chmod +x WithEase/WithEase`).

> **Linux is a new beta.** Use an **X11/Xorg** session for full functionality –
> under Wayland, global keyboard handling is blocked by the system. Because
> Linux does not allow selectively *swallowing* a key, features that rely on
> suppression are limited there: Sticky Keys and key-delay pass the original
> key through, and precision mode does not change the pointer speed. Everything
> else (tray, settings, profiles, cursor centring, highlight, hotkeys, macros)
> works. Feedback is very welcome.

WithEase then runs in the system tray. Double-click the tray icon for the
settings; a single click opens the menu. No Python required.

### Run from source (for developers)

> Requires Python 3.11 or newer.

```bash
git clone https://github.com/XelaGibiel/WithEase.git
cd WithEase
pip install -r requirements.txt
python -m withease
```

To build the standalone app yourself: run `BUILD_EXE.bat` on Windows or
`./BUILD_LINUX.sh` on Linux (both need `pip install pyinstaller`); the result
is in `dist/WithEase/`. The GitHub Actions *Build & attach release binaries*
workflow builds both automatically for every `v*` tag.

---

## Extending WithEase

Every feature is a module. Optional add-ons live in [`examples/`](examples/)
and are loaded from `%APPDATA%/WithEase/modules/` – see
[docs/MODULE_GUIDE.md](docs/MODULE_GUIDE.md). The in-app **module library**
installs the official add-ons for you.

---

## Contributing

Contributions are very welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.

---

## License

MIT License – see [LICENSE](LICENSE).
