# Changelog

All notable changes to WithEase are documented in this file. The format is based
on [Keep a Changelog](https://keepachangelog.com/).

## [0.5.0] - 2026-08-14

### Mouse
- **Highlight cursor** can now show a permanent, softly translucent **circle**
  that follows the pointer, so the cursor stays easy to spot the whole time
  (adjustable size and opacity) — separate from the existing hotkey pulse.

### Dictation add-on (module 1.3.0)
- **Local recognition now works in the packaged app (.exe) too.** One click sets
  up a small, dedicated speech-recognition runtime next to the app (downloads a
  slim Python plus faster-whisper, and the NVIDIA CUDA components on GPU
  machines). The source-code version is no longer required for local dictation.
- New option **"Pause media while dictating"** (under *Speech recognition*, right
  below the microphone): playing music or video is paused when recording starts
  and resumes automatically once dictation is finished — including after the
  recognition has finished processing the audio.

## [0.4.0] - 2026-08-07

### Dictation add-on (module 1.1.0)
- Zero-friction setup on a fresh machine: the add-on no longer fails to load when
  `audioop` is missing (removed from the standard library in Python 3.13), and a
  one-click **"Install automatically"** button installs the missing components
  (`sounddevice`, `requests`, and `audioop-lts` on Python ≥ 3.13), with progress
  and a manual-steps fallback.
- The microphone selection now lives directly in the **Speech recognition**
  group instead of a hidden advanced section.
- **LM Studio** is supported as a local AI provider alongside Ollama, and the AI
  model is now an editable **dropdown** populated live from the running provider
  (Ollama / LM Studio) instead of free text.

### Keyboard
- New **"hold = one keystroke"** protection: holding a key down counts as a
  single keystroke (no unwanted auto-repeat), with a user-editable exception list
  (e.g. arrow keys / Backspace keep repeating).
- Exception keys are now picked as one-tap **preset toggles** for the common
  editing/navigation keys, plus custom recording for anything else.

### Macros
- A **command overlay** appears while macro mode is active, listing every macro
  and its key **grouped by category**, with a ★ favourites group on top. It
  disappears when macro mode ends.
- Macros can be assigned **categories** in the editor and shown in the table.
- **Sorting** for the overlay (manual / alphabetical / by frequency) that reorders
  the favourites, the category groups, and the macros within them.
- **Manual reordering** of macros in the table (▲/▼).
- **Usage frequency** is tracked, persisted, shown as a table column, and shown
  next to each command in the overlay.
- **Import / export** macros as a JSON file to back them up or share them.

### Module library
- Each module now shows a larger **preview icon** before its name.

### Fixed
- The macro / step editor dialogs are window-modal, so the dictation window stays
  reachable — you can dictate into the Name/category fields and click "insert".
- The About-page logo no longer overlaps the text under display scaling.

## [0.3.0] - 2026

- Dictation add-on (live word-by-word recognition, voice commands, correction
  window, vocabulary, Dragon-style features), English manual, and many fixes.
- See the git history for details.
