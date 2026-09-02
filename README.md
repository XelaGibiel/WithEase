# WithEase

**Modular accessibility assistant for people with motor impairments.**

## Why WithEase?

**A journey toward independence.**

As someone personally affected by motor impairments, I spent a long time navigating the digital world using a patchwork of different tools and complex workarounds. While these solutions helped, they were often fragmented and difficult to manage as a cohesive system. They created more "noise."

I wanted something different: a unified, modular home for accessibility features—one place where everything works together seamlessly.

To build this, I embraced the power of AI-assisted "Vibe Coding." For me, it wasn't just a way to write code; it was the catalyst that allowed me to turn my personal need into a usable reality for others. WithEase is the result of that journey—it's not just an application, but a step toward true digital independence for everyone who needs it.

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
- Key delay: prevents accidental fast double-strikes (adjustable minimum gap)
- Hold = one keystroke: a held key counts only once – no unwanted auto-repeat
- Per-key exception lists for both, picked as one-tap presets or custom keys
- Sticky Keys: Shift, Ctrl, Alt, Win – press once, stays active until next non-modifier key
- Live modifier status display in the GUI

### Macros Module
- Macro mode: press trigger key → press second key → action executes → macro mode ends
- Actions: type text, send a key combination, launch an app/script, or a mouse/keyboard sequence
- In-macro-mode command overlay grouped by category, with favourites and a usage counter
- Sort the overlay manually, alphabetically, or by frequency; assign categories and reorder
- Import / export macros as a file to back them up or share them

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
  Groq, or local on your PC in the source version); optional AI dictionary
  actions via Ollama or LM Studio; one-click setup of its components. 🧪
  *Still in an early beta – it may not work flawlessly right away.*

### General
- Emergency stop: one key disables everything instantly (also in tray menu)
- Autostart, light / dark / high-contrast themes, adjustable font size, language
- In-app **module library**: browse and one-click install add-on modules
- Own app logo shown in the taskbar, window and tray

### ⚙️ Action Manager: Your Custom Control Center

Instead of forcing users to navigate complex configurations or learn hidden paths, WithEase uses an Action-based logic. The system is designed to be device-agnostic; it doesn't care how you trigger a command, only that the action is performed.

1. **The Actions:** A comprehensive list of what can be done (e.g., "Center Mouse", "Key Delay", "Launch App").
2. **The Trigger:** Simply map any input to an Action. There is no learning curve; you just assign your preferred trigger—be it a standard key, a mouse button, a foot pedal, or a macro pad—to the specific action you want it to perform.

By decoupling "what" from "how," WithEase allows users to integrate specialized hardware into their workflow instantly without ever needing to touch the core code.

---

## Download & Run (no installation needed)

Open the [**Releases**](https://github.com/XelaGibiel/WithEase/releases) page
and download the archive for your system. What is new in each version is
written up per release under
[`website/src/content/changelog/`](website/src/content/changelog) (German and
English); [CHANGELOG.md](CHANGELOG.md) keeps the archive up to 0.5.0.

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
