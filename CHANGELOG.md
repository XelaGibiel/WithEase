# Changelog

**Release notes have moved to the WithEase website.**

They are written there for the people who use the program rather than for
developers, in German and English, one page per version:

    website/src/content/changelog/{de,en}/<version>.md

Rendered, that is the "Änderungen" / "Changes" page of the site. Until the site
is published, read those files directly - they are plain Markdown.

Why the move: this file was English-only, written in developer language, and had
fallen four releases behind (0.6.0, 0.6.1, 0.6.2 and 0.7.0 were never added).
Keeping two change logs in step is exactly the kind of duplicate bookkeeping
that stops happening after a while, so there is now one.

## Adding a release

1. Write the release commit message as before - that text is the raw material.
2. Add `website/src/content/changelog/de/<version>.md` and the English twin.
   Front matter: `version`, `date`, `lang`, `summary`, and `current: true` on
   the newest one.
3. `npm --prefix website run data` picks up the new version number from
   `pyproject.toml`; the "Aktuell" badge follows it on its own.

---

# Archive (up to 0.5.0)

Everything below is the historical record from before the move. It is kept
because 0.3.0 and 0.4.0 are documented nowhere else. Nothing new is added here.

The format was based on [Keep a Changelog](https://keepachangelog.com/).

## Dictation module 1.5.0 - 2026-08-16

Delivered via the in-app Module Library (no app update needed).

### Hallucination filter
- New **"Hallucination filter"** setting (Off / Normal / Strong) under the
  advanced dictation options, for the text Whisper sometimes invents on the
  silence at the end of a clip. The confidence/non-speech segment drop that
  removes it now runs on the **normal** (press-speak-release) path too — it
  previously ran only during live streaming, which is why end-of-clip
  hallucinations still slipped through. **Normal** (default) is conservative
  and only drops near-certain junk; **Strong** filters more aggressively and
  also scrutinises the very last segment; **Off** disables the check. Applies to
  local recognition in both the source app and the packaged .exe.

## Dictation module 1.4.0 - 2026-08-15

Delivered via the in-app Module Library (no app update needed).

### Target-app selection
- Picking a new target app is now deliberate: the button (or the "Ziel-App
  wählen" command) **hides the dictation window**, shows a **chip** that you are
  in target-app mode, and you **switch to the app you want and press Space** to
  select it (Esc cancels) — with the Space hint shown right under the chip.
  Previously it silently grabbed the first window you switched to.

### Fixed
- Correcting a word via the correction window no longer hijacks the paste
  target: while that window is open the remembered target app is kept, so you
  don't have to reselect your app after every correction.

## [0.5.0] - 2026-08-14

### Mouse
- **Highlight cursor** can now show a permanent, softly translucent **circle**
  that follows the pointer, so the cursor stays easy to spot the whole time
  (adjustable size and opacity) — separate from the existing hotkey pulse.

### Dictation add-on (module 1.3.3)
- **Local recognition now works in the packaged app (.exe) too.** One click sets
  up a small, dedicated speech-recognition runtime next to the app (downloads a
  slim Python plus faster-whisper, and the NVIDIA CUDA components on GPU
  machines). The source-code version is no longer required for local dictation.
  Both the live and the normal (press-speak-release) paths now run through that
  runtime when frozen, and the worker gets a sanitised environment so it never
  loads the app's bundled DLLs.
- New option **"Pause media while dictating"** (under *Speech recognition*, right
  below the microphone): every media player that is currently playing (Spotify,
  YouTube, …) is paused when recording starts and resumed automatically once
  dictation is finished — including after the recognition has finished
  processing the audio. Uses the Windows media session API (SMTC), so multiple
  players are all paused and exactly those are resumed; a paused player is never
  accidentally started. Falls back to the media Play/Pause key (guarded by a
  WASAPI "is audio playing" check) where SMTC is unavailable.

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
