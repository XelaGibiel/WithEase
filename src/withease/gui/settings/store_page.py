"""Module store page – browse and one-click install curated modules.

Curated model: the page lists only official modules from the WithEase index
(:mod:`withease.core.module_store`).  Installing downloads the module into
%APPDATA%/WithEase/modules/ and prompts for a restart (modules are loaded at
startup).  No third-party URLs, no code the user has to write.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from withease.core import module_store
from withease.core.i18n import tr
from withease.gui import theme

if TYPE_CHECKING:
    from withease.gui.main_window import MainWindow


class _Bridge(QObject):
    index_ready = Signal(object)          # list[StoreModule] | None
    install_done = Signal(str, bool, str)  # module_id, ok, error


class StorePage(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self._window = window
        self._bridge = _Bridge()
        self._bridge.index_ready.connect(self._populate)
        self._bridge.install_done.connect(self._on_install_done)
        self._cards: dict[str, QWidget] = {}
        self._dirty = False   # something changed → a restart is needed
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel(tr("settings.store.title"))
        title.setStyleSheet(theme.title_style())
        header.addWidget(title)
        header.addStretch()
        self._refresh_btn = QPushButton(tr("settings.store.refresh"))
        self._refresh_btn.clicked.connect(self._load)
        header.addWidget(self._refresh_btn)
        outer.addLayout(header)

        hint = QLabel(tr("settings.store.hint"))
        hint.setStyleSheet(theme.hint_style())
        hint.setWordWrap(True)
        outer.addWidget(hint)

        # Restart bar – hidden until an install/remove makes it necessary.
        self._restart_bar = QFrame()
        self._restart_bar.setObjectName("restartBar")
        self._restart_bar.setStyleSheet(
            "#restartBar { background-color: rgba(230,126,34,40);"
            " border: 1px solid palette(mid); border-radius: 6px; }")
        rb = QHBoxLayout(self._restart_bar)
        rb.setContentsMargins(12, 8, 12, 8)
        self._restart_label = QLabel(tr("settings.store.restart_hint"))
        self._restart_label.setWordWrap(True)
        rb.addWidget(self._restart_label, 1)
        restart_btn = QPushButton(tr("settings.store.restart_now"))
        restart_btn.clicked.connect(self._on_restart)
        rb.addWidget(restart_btn)
        self._restart_bar.hide()
        outer.addWidget(self._restart_bar)

        self._status = QLabel(tr("settings.store.loading"))
        self._status.setStyleSheet(theme.hint_style())
        outer.addWidget(self._status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(10)
        self._list_layout.addStretch()
        scroll.setWidget(self._list_host)
        outer.addWidget(scroll, 1)

    # ------------------------------------------------------------------
    # Index loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        self._status.setText(tr("settings.store.loading"))
        self._status.setStyleSheet(theme.hint_style())
        self._status.show()
        self._refresh_btn.setEnabled(False)
        module_store.fetch_index_async(self._bridge.index_ready.emit)

    def _populate(self, modules: object) -> None:
        self._refresh_btn.setEnabled(True)
        # Clear existing cards (keep the trailing stretch).
        for card in self._cards.values():
            card.deleteLater()
        self._cards.clear()

        if modules is None:
            self._status.setText(tr("settings.store.offline"))
            self._status.setStyleSheet(theme.warn_style())
            self._update_badge()
            return
        if not modules:
            self._status.setText(tr("settings.store.empty"))
            self._update_badge()
            return

        self._status.hide()
        for module in modules:   # type: ignore[assignment]
            card = self._build_card(module)
            self._cards[module.id] = card
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)
        self._update_badge()

    def update_count(self) -> int:
        """Number of installed modules with an available update."""
        return sum(1 for c in self._cards.values()
                   if c._module.update_available)    # type: ignore[attr-defined]

    def _update_badge(self) -> None:
        """Tell the window how many installed modules have an update, so the
        'Module' nav entry can show a count."""
        self._window.set_store_badge(self.update_count())

    # ------------------------------------------------------------------
    # One module card
    # ------------------------------------------------------------------

    def _build_card(self, module: "module_store.StoreModule") -> QWidget:
        card = QFrame()
        card.setObjectName("storeCard")
        card.setStyleSheet(
            "#storeCard { border: 1px solid palette(mid); border-radius: 8px; }")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        header = QHBoxLayout()
        name = QLabel(module.name)
        name.setStyleSheet("font-weight: bold; font-size: larger;")
        header.addWidget(name)
        header.addStretch()
        meta = QLabel(tr("settings.store.meta",
                         author=module.author, version=module.version))
        meta.setStyleSheet(theme.hint_style())
        header.addWidget(meta)
        lay.addLayout(header)

        desc = QLabel(module.description)
        desc.setWordWrap(True)
        lay.addWidget(desc)

        row = QHBoxLayout()
        status = QLabel(self._status_text(module))
        status.setStyleSheet(self._status_style(module))
        row.addWidget(status)
        row.addStretch()

        progress = QProgressBar()
        progress.setRange(0, 0)      # indeterminate
        progress.setMaximumWidth(120)
        progress.hide()
        row.addWidget(progress)

        btn = QPushButton()
        # Connected once; the action dispatches on the module's live state, so
        # the button never needs disconnecting/reconnecting.
        btn.clicked.connect(lambda _=False, mid=module.id: self._on_action(mid))
        self._refresh_button(btn, module)
        row.addWidget(btn)
        lay.addLayout(row)

        # Stash references for state updates after install/remove.
        card._status_label = status      # type: ignore[attr-defined]
        card._button = btn               # type: ignore[attr-defined]
        card._progress = progress        # type: ignore[attr-defined]
        card._module = module            # type: ignore[attr-defined]
        return card

    def _status_text(self, m: "module_store.StoreModule") -> str:
        if not m.compatible:
            return tr("settings.store.status.incompatible",
                      version=m.min_app_version)
        if m.update_available:
            return tr("settings.store.status.update",
                      version=m.installed_version)
        if m.installed:
            return tr("settings.store.status.installed")
        return tr("settings.store.status.available")

    def _status_style(self, m: "module_store.StoreModule") -> str:
        if not m.compatible:
            return theme.warn_style()
        if m.installed and not m.update_available:
            return f"color: {theme.ok_color()};"
        return theme.hint_style()

    def _refresh_button(self, btn: QPushButton,
                        m: "module_store.StoreModule") -> None:
        """Set the button's label/enabled state from the module's live state.
        The click handler is wired once in _build_card and dispatches here."""
        if not m.compatible:
            btn.setText(tr("settings.store.install"))
            btn.setEnabled(False)
            return
        btn.setEnabled(True)
        if m.update_available:
            btn.setText(tr("settings.store.update"))
        elif m.installed:
            btn.setText(tr("settings.store.remove"))
        else:
            btn.setText(tr("settings.store.install"))

    def _on_action(self, module_id: str) -> None:
        card = self._cards.get(module_id)
        if card is None:
            return
        m = card._module            # type: ignore[attr-defined]
        # Installed and up to date → the button removes; otherwise it installs
        # (fresh install or update).
        if m.installed and not m.update_available:
            self._remove(m)
        else:
            self._install(m)

    # ------------------------------------------------------------------
    # Install / remove
    # ------------------------------------------------------------------

    def _install(self, module: "module_store.StoreModule") -> None:
        card = self._cards.get(module.id)
        if card is not None:
            card._button.setEnabled(False)          # type: ignore[attr-defined]
            card._progress.show()                    # type: ignore[attr-defined]
            card._status_label.setText(              # type: ignore[attr-defined]
                tr("settings.store.installing"))
            card._status_label.setStyleSheet(theme.hint_style())  # type: ignore[attr-defined]

        def run() -> None:
            try:
                module_store.install(module)
                self._bridge.install_done.emit(module.id, True, "")
            except Exception as exc:
                self._bridge.install_done.emit(module.id, False, str(exc)[:300])

        threading.Thread(target=run, daemon=True, name="store-install").start()

    def _on_install_done(self, module_id: str, ok: bool, err: str) -> None:
        card = self._cards.get(module_id)
        if card is None:
            return
        card._progress.hide()                        # type: ignore[attr-defined]
        module = card._module                        # type: ignore[attr-defined]
        status = card._status_label                  # type: ignore[attr-defined]
        btn = card._button                           # type: ignore[attr-defined]
        if ok:
            # Reflect the just-installed version and require a restart.
            module.installed_version = module.version
            status.setText(tr("settings.store.installed_ok"))
            status.setStyleSheet(f"color: {theme.ok_color()};")
            self._refresh_button(btn, module)
            self._update_badge()
            self._mark_restart()
        else:
            status.setText(tr("settings.store.install_failed", err=err))
            status.setStyleSheet(theme.warn_style())
            btn.setEnabled(True)

    def _remove(self, module: "module_store.StoreModule") -> None:
        from PySide6.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            self, tr("settings.store.remove.confirm.title"),
            tr("settings.store.remove.confirm.text", name=module.name))
        if answer != QMessageBox.StandardButton.Yes:
            return
        module_store.uninstall(module.id)
        module.installed_version = None
        card = self._cards.get(module.id)
        if card is not None:
            status = card._status_label              # type: ignore[attr-defined]
            btn = card._button                       # type: ignore[attr-defined]
            status.setText(tr("settings.store.removed"))
            status.setStyleSheet(theme.hint_style())
            self._refresh_button(btn, module)
        self._update_badge()
        self._mark_restart()

    def _mark_restart(self) -> None:
        self._dirty = True
        self._restart_bar.show()

    def _on_restart(self) -> None:
        from withease.core import updater
        updater.restart_app()
