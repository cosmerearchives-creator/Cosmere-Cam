"""Qt-safe background preparation and full-state capture for native VST2 effects."""
import threading
from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt
from PySide6.QtWidgets import QMessageBox
import vst2_host


class PluginController(QObject):
    completed = Signal(object)
    states = Signal(dict)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.preparing = 0
        self.error = ''
        self.polling = False
        self.stopping = False
        self.completed.connect(self.finish_job)
        self.states.connect(self.apply_states)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.poll_states)
        self.timer.start()

    def job(self, work, done=None):
        self.preparing += 1
        self.availability()
        def run():
            try:
                result, error = work(), ''
            except Exception as exc:
                result, error = None, str(exc)
            self.completed.emit((done, result, error))
        threading.Thread(target=run, daemon=True).start()

    @Slot(object)
    def finish_job(self, payload):
        self.preparing -= 1
        done, result, error = payload
        self.error = error
        if done and not error:
            done(result)
        self.availability()

    def availability(self):
        window = self.window
        if hasattr(window, 'record_toggle'):
            recorder = window.recorder
            window.record_toggle.setEnabled((recorder is not None and not recorder.stopping) or
                (recorder is None and not self.preparing))
        if hasattr(window, 'monitor_toggle'):
            window.monitor_toggle.setEnabled(not self.preparing)
        if hasattr(window, 'vst3_status'):
            window._refresh_vst3_status()

    def prepare_chain(self):
        entries = [e for e in self.window._audio_chain_snapshot() if e['type'] == 'vst2']
        if not entries and not vst2_host.has_hosts():
            return
        def work():
            vst2_host.reconcile(entries)
            snapshots = {}
            failures = []
            for entry in entries:
                try:
                    host = vst2_host.prepare(entry)
                    snapshots[entry['instance_id']] = host.capture()
                except Exception as exc:
                    failures.append(entry['path'] + ': ' + str(exc))
            return snapshots, failures
        def ready(result):
            snapshots, failures = result
            before = self.window._project_snapshot()
            unchanged = before == getattr(self.window, '_saved_project_snapshot', None)
            self.apply_states(snapshots)
            if unchanged:
                # A plugin may canonicalize its opaque state on load. This is
                # not a user edit; genuine edits during preparation stay dirty.
                self.window._saved_project_snapshot = self.window._project_snapshot()
            if failures:
                self.error = '; '.join(failures)
                # Explicitly bypass effects that could not prepare; recording
                # must not spend its first audio blocks repeatedly loading them.
                for row in range(self.window.audio_chain.count()):
                    item = self.window.audio_chain.item(row)
                    entry = item.data(Qt.ItemDataRole.UserRole)
                    if entry['type'] == 'vst2' and entry['instance_id'] not in snapshots:
                        item.setCheckState(Qt.CheckState.Unchecked)
        self.job(work, ready)

    @Slot(dict)
    def apply_states(self, snapshots):
        if self.stopping:
            return
        changed = False
        for row in range(self.window.audio_chain.count()):
            item = self.window.audio_chain.item(row)
            entry = dict(item.data(Qt.ItemDataRole.UserRole))
            state = snapshots.get(entry.get('instance_id'))
            if state and state != entry.get('state'):
                entry['state'] = state
                item.setData(Qt.ItemDataRole.UserRole, entry)
                changed = True
        if changed:
            self.window._update_live_record_settings()

    def poll_states(self):
        if self.polling or self.preparing or self.stopping or self.window.recorder is not None:
            return
        entries = self.window._audio_chain_snapshot()
        if not any(e['type'] == 'vst2' for e in entries):
            return
        self.polling = True
        def run():
            try:
                snapshots = vst2_host.capture_active(entries)
                self.states.emit(snapshots)
            except Exception:
                pass  # The normal plugin-status path reports failed hosts.
            finally:
                self.polling = False
        threading.Thread(target=run, daemon=True).start()

    def capture_for_save(self):
        self.apply_states(vst2_host.capture_active(self.window._audio_chain_snapshot()))

    def open_editor(self, entry):
        if self.window.recorder is not None:
            QMessageBox.information(self.window, 'Recording in progress',
                'Finish the recording before editing plugins. You can tune them while monitoring before a take.')
            return
        self.job(lambda: vst2_host.prepare(entry).editor())

    def shutdown(self):
        self.stopping = True
        self.timer.stop()
        vst2_host.close_all()
