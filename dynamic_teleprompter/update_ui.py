"""Qt integration: background checks/downloads; deployment only after clean exit."""
import os
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QPushButton,
                             QProgressBar, QInputDialog, QLineEdit)
from update_core import (GitHub, find_update, stage_update, read_json, install_root)
from version import VERSION, RELEASE_NOTES, APP_NAME


def credential_store():
    # Explicit OS vault; never accept a fallback plaintext keyring backend.
    from keyring.backends.Windows import WinVaultKeyring
    return WinVaultKeyring()


def read_token():
    token = os.environ.get('COSMERE_GITHUB_TOKEN', '')
    if token:
        return token
    try:
        return credential_store().get_password('Cosmere-Cam releases', 'github') or ''
    except Exception:
        return ''


def export_in_progress(window):
    from PySide6.QtWidgets import QDialog
    for dialog in window.findChildren(QDialog):
        worker=getattr(dialog,'worker',None)
        timer=getattr(dialog,'timer',None)
        if (worker is not None and worker.isRunning()) or (timer is not None and timer.isActive()):
            return True
    return False


def critical_operation(window):
    controller = vars(window).get('plugin_controller')
    return ((export_in_progress(window) if isinstance(window,QObject) else False) or bool(controller and controller.preparing) or window.recorder is not None or window.audio_monitor is not None or
            any(worker is not None and worker.isRunning() for worker in (
                getattr(window, 'voice_worker', None), getattr(window, 'test_voice_worker', None),
                getattr(window, 'downloader', None))))


class Updates(QObject):
    message = Signal(str)
    progress = Signal(int)
    downloaded = Signal(dict)
    completed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.root = Path(os.environ.get('COSMERE_INSTALL_ROOT', str(install_root())))
        self.busy = False
        self.closing = False
        self.restart_requested = False
        self.pending = read_json(self.root / 'pending.json')
        self.page = QWidget()
        layout = QVBoxLayout(self.page)
        heading = QLabel('Updates & what’s new')
        heading.setObjectName('sectionTitle')
        layout.addWidget(heading)
        layout.addWidget(QLabel(APP_NAME + ' • ' + VERSION))
        notes = QLabel(RELEASE_NOTES)
        notes.setTextFormat(Qt.TextFormat.PlainText)
        notes.setWordWrap(True)
        layout.addWidget(notes)
        self.status = QLabel('Checks run at launch and every 6 hours. Updates apply after closing.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.hide()
        layout.addWidget(self.bar)
        self.check_button = QPushButton('Check for updates')
        self.check_button.clicked.connect(self.check)
        layout.addWidget(self.check_button)
        self.access_button = QPushButton('Optional GitHub access…')
        self.access_button.setToolTip('Public updates need no sign-in. Optional access can help with GitHub rate limits.')
        self.access_button.clicked.connect(self.connect_access)
        layout.addWidget(self.access_button)
        self.restart_button = QPushButton('Restart & Update')
        self.restart_button.clicked.connect(self.restart)
        self.restart_button.setEnabled(bool(self.pending))
        layout.addWidget(self.restart_button)
        recovery = read_json(self.root / 'recovery.json', {})
        if recovery:
            warning = QLabel(str(recovery.get('message', '')))
            warning.setWordWrap(True)
            layout.addWidget(warning)
        layout.addStretch()
        self.message.connect(self.status.setText)
        self.progress.connect(self.set_progress)
        self.downloaded.connect(self.ready)
        self.completed.connect(self.idle)
        self.timer = QTimer(self)
        self.timer.setInterval(6 * 60 * 60 * 1000)
        self.timer.timeout.connect(self.check)
        if not os.environ.get('COSMERE_TEST_MODE'):
            self.timer.start()
            QTimer.singleShot(5000, self.check)

    def connect_access(self):
        token, accepted = QInputDialog.getText(self.window, 'Optional GitHub access',
            'Public updates work without sign-in. For higher request limits, you may enter\n'
            'a GitHub fine-grained token with Contents: read-only access to\n'
            'cosmerearchives-creator/Cosmere-Cam. Stored in Windows Credential Manager.\n'
            'Leave blank to forget the saved token.', QLineEdit.EchoMode.Password)
        if accepted:
            try:
                vault = credential_store()
                if token.strip():
                    vault.set_password('Cosmere-Cam releases', 'github', token.strip())
                else:
                    vault.delete_password('Cosmere-Cam releases', 'github')
                self.check()
            except Exception:
                self.status.setText('Could not change Windows Credential Manager access. No token was saved to a file.')

    def set_progress(self, value):
        self.bar.show()
        self.bar.setValue(value)

    def idle(self):
        self.busy = False
        self.check_button.setEnabled(True)

    def check(self):
        if self.busy or self.closing:
            return
        self.busy = True
        self.check_button.setEnabled(False)
        def work():
            try:
                token = read_token()
                self.message.emit('Checking for updates…')
                client = GitHub(token)
                update = find_update(client, VERSION)
                if not update:
                    self.message.emit('You are up to date (' + VERSION + ').')
                    return
                if self.pending and self.pending.get('sha256') == update['sha256']:
                    self.downloaded.emit(self.pending)
                    return
                self.message.emit('Downloading ' + update['version'] + ' in the background…')
                pending = stage_update(client, update, self.root,
                    lambda done, total: self.progress.emit(int(done * 100 / total)))
                self.downloaded.emit(pending)
            except Exception:
                # Do not expose tokens, signed redirect URLs, or request headers.
                self.message.emit('Updates are temporarily unavailable. Your installed version is unchanged. Retry later; optional GitHub access may help if a rate limit was reached.')
            finally:
                self.completed.emit()
        threading.Thread(target=work, daemon=True).start()

    def ready(self, pending):
        self.pending = pending
        self.restart_button.setEnabled(True)
        self.bar.hide()
        self.status.setText('Version ' + pending['version'] + ' is ready. It will install after closing.\n\n' + pending.get('notes', ''))

    def restart(self):
        if critical_operation(self.window):
            self.status.setText('Update ready. Finish recording/processing and stop audio monitoring or voice following, then choose Restart & Update.')
            return
        self.restart_requested = True
        # Existing closeEvent retains the unsaved-project prompt. Cancellation
        # leaves the app open; no installer runs until an accepted close.
        if not self.window.close():
            self.restart_requested = False

    def on_clean_close(self):
        self.closing = True
        self.timer.stop()
        pending = self.pending
        if not pending:
            return
        helper = self.root / 'CosmereCamLauncher.exe'
        if not helper.exists():
            helper = Path(pending['package']).parent / helper.name
        command = [str(helper), '--root', str(self.root), '--wait-pid', str(os.getpid())]
        if not self.restart_requested:
            command.append('--apply-only')
        subprocess.Popen(command, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
