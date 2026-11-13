"""Native Qt desktop GUI — Dorado Relay for Windows 11 and Ubuntu.

A single installed application selects self-hosted or (when provisioned) Cloud.
It remembers the last-used mode and identity but NEVER stores passwords or
device/session bearer tokens in a settings JSON file.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import os
import sys
import threading
import webbrowser

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QUrl, QLockFile
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .agent import DEVICE_NAME_PATTERN, pair_with_relay, run_agent, suggested_device_name, validate_relay_url
from .desktop_cloud import CloudAPI, CloudAccessError, cloud_api_url
from .desktop_state import (
    CredentialUnavailable, DesktopPreferences, NativeSecretStore,
    desktop_config_path, load_preferences, save_preferences, secret_name,
)

APP_TITLE = "Dorado Relay"


STYLE = """
QWidget {
    background: #0b0e14;
    color: #e9eef8;
    font-family: "Inter", "Segoe UI", "Noto Sans", sans-serif;
    font-size: 13px;
}
QLabel { background: transparent; }
QWidget#ownGroup, QWidget#cloudGroup { background: transparent; }
QLabel#logo {
    color: #ffe187;
    font-size: 23px;
    font-weight: 900;
    background: #332b12;
    border: 1px solid #7b6323;
    border-radius: 12px;
}
QLabel#brand { font-size: 17px; font-weight: 700; }
QLabel#subtitle { font-size: 11px; color: #758398; }
QLabel#eyebrow { color: #e4bb55; font-size: 11px; font-weight: 800; }
QLabel#title { font-size: 27px; font-weight: 800; }
QLabel#description { font-size: 13px; color: #97a6bd; }
QLabel#muted { color: #97a6bd; }
QLabel#small { color: #76869b; font-size: 11px; }
QLabel#stateConnected { color: #48e0aa; font-size: 23px; font-weight: 750; }
QLabel#stateOffline { color: #f6d16f; font-size: 23px; font-weight: 750; }
QLabel#error {
    color: #ffb7bf;
    background: #331920;
    border: 1px solid #793844;
    border-radius: 10px;
    padding: 11px;
}
QFrame#card {
    background: #141b28;
    border: 1px solid #293449;
    border-radius: 16px;
}
QFrame#details {
    background: #101620;
    border: 1px solid #243046;
    border-radius: 12px;
}
QLineEdit, QComboBox {
    background: #0c111a;
    color: #eef2fa;
    border: 1px solid #374357;
    border-radius: 9px;
    min-height: 37px;
    padding: 4px 12px;
    selection-background-color: #eac555;
    selection-color: #17202d;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #eac555; }
QPushButton {
    background: #202a3b;
    color: #f0f4fa;
    border: 1px solid #3b485c;
    border-radius: 10px;
    padding: 11px 17px;
    font-weight: 650;
}
QPushButton:hover { background: #29364a; border-color: #53657a; }
QPushButton:disabled { color: #758398; background: #161d29; }
QPushButton#primary {
    background: #ffd43a;
    border: 1px solid #ffe480;
    color: #12171e;
    font-weight: 800;
}
QPushButton#primary:hover { background: #ffe16d; }
QPushButton#primary:disabled { background: #9e8d55; color: #454236; }
QPushButton#mode { background: #111824; border-color: #2d394b; text-align: left; }
QPushButton#mode:checked { background: #3c3218; border: 1px solid #f0c94e; color: #ffe699; }
QCheckBox { spacing: 9px; color: #b2bece; background: transparent; }
QCheckBox::indicator { width: 18px; height: 18px; }
"""


class BackgroundTask(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    pairing = Signal(str, str)

    def __init__(self, task, parent=None) -> None:
        super().__init__(parent)
        self.task = task
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            result = self.task(self.pairing.emit, self.cancel)
            if not self.cancel.is_set():
                self.succeeded.emit(result)
        except Exception as exc:
            if not self.cancel.is_set():
                self.failed.emit(str(exc)[:400])


class AgentThread(QThread):
    status_changed = Signal(str)
    problem = Signal(str)

    def __init__(self, relay: str, token: str, name: str, permission: str,
                 parent=None) -> None:
        super().__init__(parent)
        self.relay, self.token, self.name, self.permission = relay, token, name, permission
        self.stop_event = threading.Event()

    def run(self) -> None:
        # The agent uses its local permission profile; no MCP caller can elevate it.
        os.environ["DORADO_AGENT_PERMISSION"] = self.permission
        # A locally enforced DORADO_READ_ONLY=1 must retain precedence.
        # Never clear an existing OS-level security restriction.
        try:
            asyncio.run(run_agent(
                self.relay, self.token, self.name,
                stop_event=self.stop_event, on_status=self.status_changed.emit,
            ))
        except Exception as exc:
            self.problem.emit(str(exc)[:300])

    def stop(self) -> None:
        self.stop_event.set()


class DesktopWindow(QMainWindow):
    def __init__(self, *, prefs: DesktopPreferences | None = None,
                 vault=None, cloud_factory=CloudAPI, autostart: bool = True) -> None:
        super().__init__()
        # Qt must dispose its entire QWidget/QObject tree before QApplication;
        # otherwise native Qt ownership can outlive the Python wrapper.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.prefs = prefs or load_preferences()
        self.vault = vault or NativeSecretStore()
        self.cloud_factory = cloud_factory
        self.agent_thread: AgentThread | None = None
        self.pending: BackgroundTask | None = None
        self.cloud_recheck: BackgroundTask | None = None
        self.active_mode: str = ""
        self.selected_mode = self.prefs.last_mode
        self.relay: str = ""
        self.account: str = ""
        self._connecting = False
        self._closing = False
        self._build_ui()
        self._select_mode(self.selected_mode)
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(5 * 60 * 1000)
        self.status_timer.timeout.connect(self._verify_cloud_session)
        if autostart and self.prefs.auto_connect:
            QTimer.singleShot(350, self.resume_previous_connection)

    def _text(self, content: str, name: str = "") -> QLabel:
        widget = QLabel(content)
        if name:
            widget.setObjectName(name)
        widget.setWordWrap(True)
        return widget

    def _field(self, hint: str, *, password: bool = False) -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText(hint)
        if password:
            field.setEchoMode(QLineEdit.EchoMode.Password)
        return field

    def _layout(self, parent: QWidget, gap=15) -> QVBoxLayout:
        column = QVBoxLayout(parent)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(gap)
        return column

    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(25, 24, 25, 24)
        layout.setSpacing(13)
        return card, layout

    def _build_ui(self) -> None:
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(720, 680)
        self.resize(880, 740)
        self.setStyleSheet(STYLE)
        root = QWidget(self)
        self.setCentralWidget(root)
        master = QVBoxLayout(root)
        master.setContentsMargins(34, 23, 34, 22)
        master.setSpacing(24)

        header = QHBoxLayout()
        header.setSpacing(12)
        brand_icon = self._text("D", "logo")
        brand_icon.setFixedSize(45, 45)
        brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(brand_icon)
        brand = QVBoxLayout()
        brand.setSpacing(1)
        brand.addWidget(self._text("Dorado Relay", "brand"))
        brand.addWidget(self._text("Tu equipo, tu conexión, tus permisos.", "subtitle"))
        header.addLayout(brand)
        header.addStretch()
        self.header_status = self._text("ESCRITORIO · 0.5 RC", "small")
        header.addWidget(self.header_status)
        master.addLayout(header)

        self.pages = QStackedWidget()
        master.addWidget(self.pages, 1)
        self._build_setup_page()
        self._build_status_page()
        footer = self._text(
            "El servidor propio es gratuito. Cloud requiere una cuenta con un plan activo.",
            "small",
        )
        footer.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        master.addWidget(footer)

    def _build_setup_page(self) -> None:
        page = QWidget()
        main = self._layout(page, 16)
        main.addWidget(self._text("CONFIGURA TU CONEXIÓN", "eyebrow"))
        main.addWidget(self._text("¿Cómo quieres conectarte?", "title"))
        main.addWidget(self._text(
            "Elige una vez. Dorado recordará la última conexión y la recuperará al abrir.",
            "description",
        ))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.own_btn = QPushButton("TU SERVIDOR\nGratis · en casa o tu VPS")
        self.cloud_btn = QPushButton("DORADO CLOUD\nTu cuenta y plan activo")
        for widget in (self.own_btn, self.cloud_btn):
            widget.setObjectName("mode")
            widget.setCheckable(True)
            widget.setMinimumHeight(71)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row.addWidget(widget)
        self.own_btn.clicked.connect(lambda: self._select_mode("own"))
        self.cloud_btn.clicked.connect(lambda: self._select_mode("cloud"))
        main.addLayout(row)

        card, inside = self._card()
        self.mode_intro = self._text("", "description")
        inside.addWidget(self.mode_intro)
        self.own_group = QWidget()
        self.own_group.setObjectName("ownGroup")
        own_layout = self._layout(self.own_group, 10)
        own_layout.addWidget(self._text("URL HTTPS de tu relay"))
        self.own_url = self._field("https://mi-relay.duckdns.org")
        self.own_url.setText(self.prefs.own_relay)
        own_layout.addWidget(self.own_url)
        own_layout.addWidget(self._text(
            "Alojamiento gratuito: tu PC con el relay + Caddy. La primera conexión se autoriza en el navegador.",
            "small",
        ))
        inside.addWidget(self.own_group)

        self.cloud_group = QWidget()
        self.cloud_group.setObjectName("cloudGroup")
        cloud_layout = self._layout(self.cloud_group, 10)
        cloud_layout.addWidget(self._text("Correo de tu cuenta Dorado Cloud"))
        self.cloud_email = self._field("correo@ejemplo.com")
        self.cloud_email.setText(self.prefs.cloud_email)
        cloud_layout.addWidget(self.cloud_email)
        cloud_layout.addWidget(self._text("Contraseña (solo se usa para iniciar sesión)"))
        self.cloud_password = self._field("Contraseña de la cuenta", password=True)
        cloud_layout.addWidget(self.cloud_password)
        self.cloud_note = self._text("", "small")
        cloud_layout.addWidget(self.cloud_note)
        inside.addWidget(self.cloud_group)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.addWidget(self._text("Nombre de este ordenador"), 0, 0)
        grid.addWidget(self._text("Permisos que tendrá el agente"), 0, 1)
        self.device_name = self._field("Mi equipo")
        self.device_name.setText(
            (self.prefs.own_device_name if self.prefs.last_mode == "own"
             else self.prefs.cloud_device_name)
            or self.prefs.device_name or suggested_device_name()
        )
        grid.addWidget(self.device_name, 1, 0)
        self.permission = QComboBox()
        self.permission.addItems([
            "Inspección · lectura",
            "Archivos · lectura y escritura",
            "Completo · comandos y procesos",
        ])
        self.permission.setCurrentIndex({"inspect": 0, "files": 1, "full": 2}.get(self.prefs.permission, 0))
        grid.addWidget(self.permission, 1, 1)
        inside.addLayout(grid)
        self.remember = QCheckBox("Reconectar automáticamente al abrir la aplicación")
        self.remember.setChecked(self.prefs.auto_connect)
        inside.addWidget(self.remember)
        self.connect_btn = QPushButton("Continuar y conectar")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.setMinimumHeight(46)
        self.connect_btn.clicked.connect(self.connect_clicked)
        inside.addWidget(self.connect_btn)
        self.error = self._text("", "error")
        self.error.hide()
        inside.addWidget(self.error)
        main.addWidget(card)
        main.addStretch(1)
        self.pages.addWidget(page)

    def _build_status_page(self) -> None:
        page = QWidget()
        main = self._layout(page, 18)
        main.addWidget(self._text("CONEXIÓN ACTUAL", "eyebrow"))
        self.connection_state = self._text("Conectando…", "stateOffline")
        main.addWidget(self.connection_state)
        self.connection_description = self._text(
            "Reconexión automática activada si se pierde la red.", "description",
        )
        main.addWidget(self.connection_description)

        card, content = self._card()
        self.connection_mode = self._text("", "eyebrow")
        content.addWidget(self.connection_mode)
        self.connection_relay = self._text("", "description")
        self.connection_relay.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.addWidget(self.connection_relay)
        self.connection_device = self._text("", "muted")
        content.addWidget(self.connection_device)
        self.connection_account = self._text("", "muted")
        content.addWidget(self.connection_account)
        content.addWidget(self._text(
            "Puedes cerrar y abrir la aplicación: recordará el servidor y la cuenta anterior.",
            "small",
        ))
        main.addWidget(card)
        self.stop_btn = QPushButton("Desconectar")
        self.stop_btn.clicked.connect(self.disconnect_clicked)
        self.switch_btn = QPushButton("Cambiar de conexión o cuenta")
        self.switch_btn.clicked.connect(self.change_connection_clicked)
        self.logout_btn = QPushButton("Olvidar este dispositivo y cerrar sesión")
        self.logout_btn.clicked.connect(self.forget_connection_clicked)
        main.addWidget(self.stop_btn)
        main.addWidget(self.switch_btn)
        main.addWidget(self.logout_btn)
        main.addStretch(1)
        self.pages.addWidget(page)

    def _select_mode(self, mode: str) -> None:
        self.selected_mode = mode
        remembered_name = self.prefs.own_device_name if mode == "own" else self.prefs.cloud_device_name
        self.device_name.setText(remembered_name or self.prefs.device_name or suggested_device_name())
        self.own_btn.setChecked(mode == "own")
        self.cloud_btn.setChecked(mode == "cloud")
        self.own_group.setVisible(mode == "own")
        self.cloud_group.setVisible(mode == "cloud")
        self.mode_intro.setText(
            "Tu propio relay, sin cuotas ni servidor de pago."
            if mode == "own"
            else "Inicia sesión en Dorado Cloud. El servidor comprueba tu suscripción activa antes de conectar."
        )
        if mode == "cloud":
            available = bool(cloud_api_url())
            self.cloud_note.setText(
                "Servicio oficial no configurado todavía: se habilitará al desplegar Dorado Cloud."
                if not available else "La cuenta y el plan se verifican en el servidor; no se guardará la contraseña."
            )
            self.connect_btn.setEnabled(available)
        else:
            self.connect_btn.setEnabled(True)
        self._show_error("")

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))

    def _set_busy(self, busy: bool) -> None:
        self._connecting = busy
        self.connect_btn.setEnabled(not busy and (
            self.selected_mode == "own" or bool(cloud_api_url())
        ))
        self.own_btn.setEnabled(not busy)
        self.cloud_btn.setEnabled(not busy)

    def _run_task(self, fn, success) -> None:
        if self.pending and self.pending.isRunning():
            return
        self._set_busy(True)
        task = BackgroundTask(fn, self)
        self.pending = task
        task.succeeded.connect(lambda outcome: self._task_success(success, outcome))
        task.failed.connect(self._task_failed)
        task.pairing.connect(self._pairing_requested)
        task.finished.connect(lambda: self._set_busy(False))
        task.start()

    def _task_success(self, callback, outcome) -> None:
        try:
            callback(outcome)
        except (ValueError, CredentialUnavailable, OSError) as exc:
            self._task_failed(str(exc))

    def _task_failed(self, detail: str) -> None:
        self._show_error(detail)
        self.pages.setCurrentIndex(0)

    def _pairing_requested(self, url: str, code: str) -> None:
        self._show_error("Confirma el código " + code + " en el navegador para vincular este PC.")
        QDesktopServices.openUrl(QUrl(url))

    def _current_permission(self) -> str:
        return ("inspect", "files", "full")[self.permission.currentIndex()]

    def _prepare_fields(self) -> tuple[str, str]:
        name = self.device_name.text().strip()
        if not DEVICE_NAME_PATTERN.fullmatch(name):
            raise ValueError("El nombre debe tener entre 1 y 64 letras ASCII, números, guiones o guiones bajos.")
        return name, self._current_permission()

    def connect_clicked(self) -> None:
        if self._connecting:
            return
        self._show_error("")
        try:
            name, profile = self._prepare_fields()
            if self.selected_mode == "own":
                relay = self.own_url.text().strip().rstrip("/")
                validate_relay_url(relay)
                key = secret_name("own-device", relay, name)
                existing = self.vault.get(key)
                if existing:
                    self._connected_credentials("own", relay, name, profile, existing)
                    return

                def pair(notify, stop):
                    return pair_with_relay(
                        relay, name, open_browser=False,
                        on_pairing=notify, stop_event=stop,
                    )
                def succeeded(token):
                    self.vault.set(key, token)
                    self._connected_credentials("own", relay, name, profile, token)
                self._run_task(pair, succeeded)
                return
            if not cloud_api_url():
                raise ValueError("Cloud no está disponible aún. Puedes utilizar un servidor propio gratis.")
            email = self.cloud_email.text().strip().lower()
            password = self.cloud_password.text()
            if not email or not password:
                raise ValueError("Introduce correo y contraseña para tu cuenta Cloud.")
            # Never save the password and remove it from the Qt input immediately.
            self.cloud_password.clear()
            def login(notify, stop):
                return self.cloud_factory().login(email, password, name)
            self._run_task(login, lambda identity: self._cloud_login_success(identity, profile))
        except (ValueError, CredentialUnavailable, CloudAccessError) as exc:
            self._show_error(str(exc))

    def _cloud_login_success(self, identity, profile: str) -> None:
        if identity.device_name != self.device_name.text().strip():
            raise ValueError("Cloud devolvió un dispositivo distinto del solicitado.")
        api = cloud_api_url()
        session = secret_name("cloud-session", api, identity.device_name, identity.email)
        agent = secret_name("cloud-device", api, identity.device_name, identity.email)
        self.vault.set(session, identity.session_token)
        self.vault.set(agent, identity.agent_token)
        self.prefs.cloud_email = identity.email
        self.prefs.cloud_relay = identity.relay_url
        self._connected_credentials(
            "cloud", identity.relay_url, identity.device_name,
            profile, identity.agent_token, identity.email,
        )

    def _connected_credentials(self, mode: str, relay: str, name: str, profile: str,
                               token: str, account: str = "") -> None:
        if self.agent_thread and self.agent_thread.isRunning():
            self._stop_agent()
        self.prefs.last_mode = mode
        self.prefs.device_name = name
        if mode == "own":
            self.prefs.own_device_name = name
        else:
            self.prefs.cloud_device_name = name
        self.prefs.permission = profile
        self.prefs.auto_connect = self.remember.isChecked()
        if mode == "own":
            self.prefs.own_relay = relay
        else:
            self.prefs.cloud_email = account
            self.prefs.cloud_relay = relay
        save_preferences(self.prefs)
        self.active_mode, self.relay, self.account = mode, relay, account
        self.connection_mode.setText("SERVIDOR PROPIO" if mode == "own" else "DORADO CLOUD · PLAN ACTIVO")
        self.connection_relay.setText(relay)
        self.connection_device.setText("Ordenador: " + name + "  ·  Perfil: " + profile)
        self.connection_account.setText("Cuenta: " + account if account else "Sin cuenta de Dorado Cloud")
        self.connection_state.setObjectName("stateOffline")
        self.connection_state.setText("Conectando…")
        self.pages.setCurrentIndex(1)
        self.agent_thread = AgentThread(relay, token, name, profile, self)
        self.agent_thread.status_changed.connect(self._agent_status)
        self.agent_thread.problem.connect(self._agent_problem)
        self.agent_thread.start()
        if mode == "cloud":
            self.status_timer.start()
        else:
            self.status_timer.stop()

    def _agent_status(self, state: str) -> None:
        self.connection_state.setObjectName("stateConnected" if state == "connected" else "stateOffline")
        self.connection_state.style().unpolish(self.connection_state)
        self.connection_state.style().polish(self.connection_state)
        self.connection_state.setText({
            "connected": "Conectado",
            "connecting": "Conectando…",
            "disconnected": "Sin conexión · reintentando",
        }.get(state, state))

    def _agent_problem(self, message: str) -> None:
        self.connection_state.setText("No se pudo iniciar el agente")
        self.connection_description.setText(message)

    def _stop_agent(self) -> None:
        self.status_timer.stop()
        thread = self.agent_thread
        if thread and thread.isRunning():
            thread.stop()
            if not thread.wait(5000):
                raise RuntimeError("El agente aún se está desconectando. Inténtalo de nuevo.")
        self.agent_thread = None

    def disconnect_clicked(self) -> None:
        try:
            self._stop_agent()
            self.connection_state.setText("Desconectado")
            self.stop_btn.setText("Conectar de nuevo")
            self.stop_btn.clicked.disconnect()
            self.stop_btn.clicked.connect(self.resume_previous_connection)
        except RuntimeError as exc:
            QMessageBox.warning(self, APP_TITLE, str(exc))

    def change_connection_clicked(self) -> None:
        try:
            self._stop_agent()
        except RuntimeError as exc:
            QMessageBox.warning(self, APP_TITLE, str(exc))
            return
        self.stop_btn.setText("Desconectar")
        try:
            self.stop_btn.clicked.disconnect()
        except RuntimeError:
            pass
        self.stop_btn.clicked.connect(self.disconnect_clicked)
        self.pages.setCurrentIndex(0)
        self._select_mode(self.prefs.last_mode)

    def forget_connection_clicked(self) -> None:
        reply = QMessageBox.question(
            self, "Olvidar conexión",
            "Se eliminarán las credenciales recordadas en este ordenador. "
            "También debes revocar este dispositivo desde el panel del relay si quieres impedir su acceso futuro.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._stop_agent()
            if self.active_mode == "own":
                self.vault.delete(secret_name("own-device", self.relay, self.prefs.device_name))
            elif self.active_mode == "cloud":
                api = cloud_api_url()
                skey = secret_name("cloud-session", api, self.prefs.device_name, self.account)
                token = self.vault.get(skey)
                revoked = False
                if token:
                    try:
                        revoked = self.cloud_factory().logout(token)
                    except CloudAccessError:
                        pass  # Local sign-out must still wipe the stored token.
                self.vault.delete(skey)
                self.vault.delete(secret_name("cloud-device", api, self.prefs.device_name, self.account))
                if not revoked:
                    QMessageBox.warning(
                        self, APP_TITLE,
                        "Se ha cerrado la sesión local, pero no se pudo confirmar "
                        "la revocación del dispositivo en Cloud. Revócalo desde tu panel.",
                    )
            self.prefs.auto_connect = False
            save_preferences(self.prefs)
            self.remember.setChecked(False)
            self.change_connection_clicked()
        except (RuntimeError, CredentialUnavailable, OSError) as exc:
            QMessageBox.warning(self, APP_TITLE, str(exc))

    def resume_previous_connection(self) -> None:
        if self._closing or (self.agent_thread and self.agent_thread.isRunning()):
            return
        name = ((self.prefs.own_device_name if self.prefs.last_mode == "own"
                 else self.prefs.cloud_device_name) or self.prefs.device_name)
        if not name:
            return
        self._select_mode(self.prefs.last_mode)
        self.device_name.setText(name)
        self.permission.setCurrentIndex({"inspect": 0, "files": 1, "full": 2}[self.prefs.permission])
        if self.prefs.last_mode == "own":
            try:
                token = self.vault.get(secret_name("own-device", self.prefs.own_relay, name))
                if token and self.prefs.own_relay:
                    self._connected_credentials("own", self.prefs.own_relay, name, self.prefs.permission, token)
            except (ValueError, CredentialUnavailable) as exc:
                self._show_error(str(exc))
            return
        if not cloud_api_url() or not self.prefs.cloud_email or not self.prefs.cloud_relay:
            return
        api_url = cloud_api_url()
        skey = secret_name("cloud-session", api_url, name, self.prefs.cloud_email)
        dkey = secret_name("cloud-device", api_url, name, self.prefs.cloud_email)
        try:
            session = self.vault.get(skey)
            device = self.vault.get(dkey)
            if not session or not device:
                return
        except CredentialUnavailable as exc:
            self._show_error(str(exc))
            return
        email, previous_relay = self.prefs.cloud_email, self.prefs.cloud_relay
        def verify(notify, cancel):
            return self.cloud_factory().resume(session)
        def valid(identity):
            if (identity.email != email or identity.device_name != name
                    or identity.relay_url != previous_relay):
                raise ValueError("La conexión Cloud ha cambiado. Inicia sesión de nuevo.")
            self._connected_credentials("cloud", previous_relay, name, self.prefs.permission, device, email)
        self._run_task(verify, valid)

    def _verify_cloud_session(self) -> None:
        if self.active_mode != "cloud" or not self.agent_thread or not self.agent_thread.isRunning():
            return
        if self.cloud_recheck and self.cloud_recheck.isRunning():
            return
        try:
            skey = secret_name("cloud-session", cloud_api_url(), self.prefs.device_name, self.account)
            session = self.vault.get(skey)
        except CredentialUnavailable as exc:
            self._suspend_cloud(str(exc))
            return
        if not session:
            self._suspend_cloud("Sesión Cloud caducada.")
            return
        job = BackgroundTask(lambda n, c: self.cloud_factory().resume(session), self)
        self.cloud_recheck = job
        job.succeeded.connect(self._cloud_revalidated)
        job.failed.connect(self._suspend_cloud)
        job.start()

    def _cloud_revalidated(self, identity) -> None:
        if (identity.email != self.account or identity.relay_url != self.relay
                or identity.device_name != self.prefs.device_name):
            self._suspend_cloud("La sesión Cloud ya no corresponde a esta conexión.")

    def _suspend_cloud(self, explanation: str) -> None:
        try:
            self._stop_agent()
        except RuntimeError:
            pass
        self.connection_state.setText("Cloud suspendido")
        self.connection_description.setText(explanation)

    def closeEvent(self, event) -> None:
        self._closing = True
        if self.pending and self.pending.isRunning():
            self.pending.cancel.set()
            self.pending.wait(15_000)
        if self.cloud_recheck and self.cloud_recheck.isRunning():
            self.cloud_recheck.cancel.set()
            self.cloud_recheck.wait(15_000)
        try:
            self._stop_agent()
        except RuntimeError:
            event.ignore()
            self._closing = False
            return
        event.accept()


def main() -> None:
    # PyInstaller --windowed on Windows has no console streams. The existing
    # agent reports connection events with print(); give it safe null streams
    # rather than crashing the GUI on its very first connection attempt.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    parser = argparse.ArgumentParser(description="Dorado Relay native desktop application")
    parser.add_argument("--no-auto-connect", action="store_true", help="Start without reconnecting")
    parser.add_argument("--smoke-test", action="store_true",
                        help="Verify native Qt renders without accounts or network access and exit")
    args = parser.parse_args()
    app = QApplication([])
    if args.smoke_test:
        from PySide6.QtCore import QCoreApplication, QEvent
        class EmptyVault:
            def get(self, key): return None
            def set(self, key, value): raise RuntimeError("Smoke test must not save tokens")
            def delete(self, key): return None
        test = DesktopWindow(prefs=DesktopPreferences(), vault=EmptyVault(), autostart=False)
        test.show()
        app.processEvents()
        test._select_mode("cloud")
        app.processEvents()
        test._select_mode("own")
        app.processEvents()
        test.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        print("Dorado Relay Desktop: native Qt smoke test passed", flush=True)
        return
    app.setApplicationName(APP_TITLE)
    app.setStyle("Fusion")
    config = desktop_config_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(config.parent / "desktop.lock"))
    lock.setStaleLockTime(20_000)
    if not lock.tryLock(250):
        QMessageBox.information(None, APP_TITLE, "Dorado Relay ya está abierto.")
        return
    try:
        window = DesktopWindow(autostart=not args.no_auto_connect)
    except (ValueError, OSError) as exc:
        QMessageBox.critical(None, APP_TITLE, str(exc))
        return
    window.show()
    try:
        app.exec()
    finally:
        lock.unlock()


if __name__ == "__main__":
    main()
