# WithEase – Module entwickeln

WithEase ist modular aufgebaut: Jede Funktion (Maus, Tastatur, Makros, …)
ist ein Modul. Externe Module werden beim Start automatisch geladen und
erscheinen als eigene Kategorie in den Einstellungen – ohne Änderungen am
Hauptprogramm.

## Installation eines Moduls (für Nutzer)

Modulordner nach `%APPDATA%\WithEase\modules\` kopieren und WithEase neu
starten. Fertig. Ein fehlerhaftes Modul wird übersprungen und im Log
(`%APPDATA%\WithEase\withease.log`) vermerkt – es kann die App nicht zum
Absturz bringen.

> ⚠️ **Sicherheit:** Module sind ausführbarer Python-Code mit Zugriff auf
> Tastatur und Maus. Installiere nur Module aus Quellen, denen du vertraust.

## Aufbau eines Moduls

```
modules/
  mein_modul/
    manifest.json
    module.py
```

**manifest.json**

```json
{
  "name":        "Mein Modul",
  "version":     "1.0.0",
  "author":      "Jane Doe",
  "description": "Was das Modul tut",
  "entry":       "module.py",
  "class":       "MeinModul"
}
```

**module.py** enthält eine Klasse, die von `BaseModule` erbt. Ein
lauffähiges Minimalbeispiel liegt unter [`examples/example_module/`](../examples/example_module/) –
am besten als Vorlage kopieren.

## Die BaseModule-Schnittstelle

```python
from withease.modules.base import BaseModule

class MeinModul(BaseModule):
    MODULE_ID = "mein_modul"        # eindeutig, klein, ohne Leerzeichen
    DISPLAY_NAME = "Mein Modul"     # Name in der Seitenleiste
    DESCRIPTION = "Kurzbeschreibung"

    def start(self) -> None: ...    # Modul wurde aktiviert
    def stop(self) -> None: ...     # Modul wurde deaktiviert (idempotent!)
    def get_settings_widget(self):  # PySide6-Widget für die Einstellungsseite
        ...
    def load_settings(self, settings: dict) -> None: ...
    def dump_settings(self) -> dict: ...
```

Wichtige Regeln:

- `stop()` muss **alles aufräumen**: Hooks abmelden, Threads stoppen,
  Overlays verstecken, injizierte Tasten freigeben. Es wird u. a. beim
  Notfall-Stop aufgerufen.
- `start()`/`stop()` sollen `bus.publish("module.started"/"module.stopped",
  module_id=MODULE_ID)` senden – daran hängen Tray, Profil-Speicherung und
  Live-Aktualisierung der Einstellungsseiten.
- Einstellungen sind ein einfaches, JSON-serialisierbares Dict. WithEase
  speichert es **pro Profil** automatisch, sobald das Modul
  `bus.publish("module.settings_changed", module_id=MODULE_ID)` sendet.

## Die Plugin-API

### Event-Bus (`withease.core.event_bus.bus`)

Module kommunizieren ausschließlich über Events – nie über direkte Referenzen.

```python
from withease.core.event_bus import bus
bus.subscribe("mouse.centered", callback)      # kwargs-basiert
bus.publish("mein_modul.irgendwas", wert=42)
```

Exceptions in Subscribern werden abgefangen und geloggt.

### Action-Manager (`withease.core.action_manager.action_manager`)

Aktionen registrieren statt Hotkeys hart verdrahten – dadurch bekommt das
Modul gratis: Hotkey-Zuweisung per `HotkeyEdit`, Konfliktwarnungen,
Favoriten-Overlay und die Übersicht auf der Seite „Aktionen".

```python
from withease.core.action_manager import Action, action_manager
action_manager.register(Action(id="mein_modul.tu_was",
                               label="Tu was", callback=self._tu_was))
# Hotkey (aus den eigenen Einstellungen) zuweisen – leerer String = aus:
action_manager.assign_trigger("mein_modul.tu_was",
                              self._settings.get("hotkey", ""))
```

### Tastatur: NUR der geteilte Hook!

**Niemals** einen eigenen `WH_KEYBOARD_LL`-Hook oder einen
`pynput.keyboard.Listener` installieren – mehrere Low-Level-Hooks blockieren
sich gegenseitig, und pynputs Listener zerstört AltGr/Tote Tasten. Stattdessen:

```python
from withease.core.win_keyboard_hook import (
    shared_keyboard_hook,   # der eine Hook für alle
    vk_to_combo_str,        # VK-Code → Hotkey-String ("'a'", "Key.f9", …)
    current_combo_str,      # inkl. gehaltener Modifier ("ctrl+shift+'m'")
    is_altgr_fake_lctrl,    # das synthetische LCtrl von AltGr erkennen
)

def _on_key(vk, scan, extended, injected, is_press) -> bool:
    if injected:                       # eigene injizierte Tasten ignorieren
        return False
    if is_altgr_fake_lctrl(vk, scan):  # gehört zu AltGr, nie als Ctrl werten
        return False
    ...
    return False   # True = Taste verschlucken (sparsam einsetzen!)

# in start():
shared_keyboard_hook.subscribe(self._on_key)
# in stop():
shared_keyboard_hook.unsubscribe(self._on_key)
```

Der Callback läuft im Hook-Thread: **schnell zurückkehren, nie blockieren**.
Aufwendiges per `threading.Timer(0.05, …)` verzögern und GUI-Arbeit immer
über Qt-Signale in den Main-Thread holen.

### Hotkey-Eingabefelder

`withease.gui.widgets.hotkey_edit.HotkeyEdit` wiederverwenden (speichert im
kompatiblen Format, inkl. Kombinationen, Numpad und Konfliktprüfung – dazu
`action_id` übergeben).

### GUI-Konventionen

- Einstellungsseite: `QScrollArea`, `CollapsibleSection`
  (`withease.gui.widgets.collapsible_section`) pro Werkzeug mit
  Beschreibungstext.
- Alles, was aus Threads kommt, per Qt-Signal in den Main-Thread bringen –
  `QTimer` u. ä. dürfen nur dort laufen.

## Häufige Fehler

| Fehler | Folge |
|---|---|
| Eigener Keyboard-Hook / pynput-Listener | AltGr kaputt, andere Hooks verhungern |
| `stop()` räumt nicht auf | Hängende Tasten/Overlays nach Notfall-Stop |
| Blockierender Hook-Callback | Windows wirft den Hook raus, Eingaben ruckeln |
| Nicht-serialisierbare Settings | Profil-Speicherung schlägt fehl |
| `QTimer`/Widgets aus fremdem Thread | Stille Fehlfunktion oder Absturz |
