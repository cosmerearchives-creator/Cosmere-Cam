"""Render the real Qt interface for review without touching user settings/devices."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dynamic_teleprompter'))


def main():
    with tempfile.TemporaryDirectory(prefix='cosmere-ui-') as folder:
        local = Path(folder)
        os.environ.update(QT_QPA_PLATFORM='offscreen', COSMERE_TEST_MODE='1', USERPROFILE=str(local),
            HOME=str(local), LOCALAPPDATA=str(local / 'local'), APPDATA=str(local / 'roaming'))
        from PySide6.QtCore import QSettings, QPoint
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication, QTabWidget, QMessageBox, QFileDialog, QColorDialog
        import app as module
        from theme import apply_theme
        application = QApplication([])
        for font in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf', 'arial.ttf', 'seguisym.ttf', 'seguiemj.ttf'):
            path = Path('C:/Windows/Fonts') / font
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
        apply_theme(application)
        module.QSettings = lambda *args: QSettings(str(local / 'reader.ini'), QSettings.Format.IniFormat)
        module.MainWindow.prompt_model_if_missing = lambda self: None
        module.MainWindow.arrange_windows = lambda self: None
        module.lip_microphone.start = lambda *args, **kwargs: None
        window = module.MainWindow()
        # Exercise the relocated toolbar's actual signal connections and the
        # confirmation gate with synthetic canvas frames, without audio devices.
        import time
        from unittest.mock import patch
        from unittest.mock import Mock
        from processing_ui import ProcessingChoices
        window.voice_worker = Mock(ready_for_recording=True)
        window.voice_worker.isRunning.return_value = True
        original_directory = window.record_dir
        window.record_microphone.setChecked(False)
        for discard in (False, True):
            destination = local / ('discard' if discard else 'save')
            window.record_dir = str(destination)
            def fail_dialog(parent, title, message, *args):
                raise RuntimeError(title + ': ' + message)
            with patch.object(QMessageBox, 'warning', side_effect=fail_dialog):
                window.record_toggle.click()
            recorder = window.recorder
            assert recorder is not None and not recorder.error
            assert window.record_caption.text() == 'Stop / Save'
            if discard:
                with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Cancel):
                    window.record_discard.click()
                assert not recorder.stopping and not recorder.cancelled
                with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Discard):
                    window.record_discard.click()
                assert recorder.cancelled
            else:
                def choose():
                    assert recorder.stopping and not recorder._finish_started
                    return {}
                with patch.object(ProcessingChoices, 'options', side_effect=choose):
                    window.record_toggle.click()
                assert recorder.stopping and not recorder.cancelled
            deadline = time.monotonic() + 20
            with patch.object(QMessageBox, 'information'), patch.object(QMessageBox, 'warning'):
                while window.recorder is not None and time.monotonic() < deadline:
                    application.processEvents()
                    time.sleep(.02)
            assert window.recorder is None and not recorder.error, recorder.error
            assert bool(list(destination.glob('*.mp4'))) != discard
            assert window.record_toggle.isEnabled() and not window.record_discard.isEnabled()
        window.record_dir = original_directory
        window.record_microphone.setChecked(True)
        assert window.start_voice_following.isChecked()
        window.start_voice_following.setChecked(False)
        defaults = window.settings()
        window.start_voice_following.setChecked(True)
        window.apply_settings_values(defaults)
        assert not window.start_voice_following.isChecked()
        window.start_voice_following.setChecked(True)
        window.resize(int(os.environ.get('COSMERE_UI_WIDTH', '1280')), int(os.environ.get('COSMERE_UI_HEIGHT', '900')))
        window.show()
        output = ROOT / 'build/ui-preview'
        output.mkdir(parents=True, exist_ok=True)
        for previous in output.glob('*.png'):
            previous.unlink()
        top = window.centralWidget().findChild(QTabWidget)
        counter = 0
        def capture(name):
            nonlocal counter
            application.processEvents()
            window.grab().save(str(output / ('%02d-' % counter + name + '.png')))
            counter += 1
        for index in range(top.count()):
            top.setCurrentIndex(index)
            capture(top.tabText(index).replace(' ', '-'))
            page = top.widget(index)
            nested_tabs = ([page] if isinstance(page, QTabWidget) else []) + page.findChildren(QTabWidget)
            for nested in nested_tabs:
                for child in range(nested.count()):
                    nested.setCurrentIndex(child)
                    capture(top.tabText(index).replace(' ', '-') + '-' + nested.tabText(child).replace(' ', '-').replace('&', ''))
        window.reader.set_script('Welcome to Cosmere Video Recording Suite. Read naturally while the teleprompter follows your speech. ' * 8)
        window.reader.resize(640, 720)
        application.processEvents()
        window.reader.grab().save(str(output / 'reader.png'))
        editor = module.VisualLayerEditor(window)
        editor.resize(1300, 800)
        editor.show()
        application.processEvents()
        editor.grab().save(str(output / 'visual-layer-editor.png'))
        editor.close()
        from processing_ui import ProcessingChoices, AlignmentReview
        choices = ProcessingChoices(window, True, True)
        choices.show()
        application.processEvents()
        choices.grab().save(str(output / 'processing-choices.png'))
        choices.close()
        from recording_processing import align_script
        review = AlignmentReview(window, align_script('All script words stay visible.', [], 4), local/'review.json')
        review.show()
        application.processEvents()
        review.grab().save(str(output / 'alignment-review.png'))
        review.close()
        window._show_menu_temporarily()
        for action in window.menuBar().actions():
            menu = action.menu()
            menu.popup(QPoint(30, 30))
            application.processEvents()
            menu.grab().save(str(output / ('menu-' + action.text().replace('&', '') + '.png')))
            menu.hide()
        for name, dialog in (('confirmation', QMessageBox(window)), ('files', QFileDialog(window)), ('colors', QColorDialog(window))):
            if name == 'confirmation':
                dialog.setWindowTitle('Discard this recording?')
                dialog.setText('Permanently discard this take without saving or processing it?')
                dialog.setStandardButtons(QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
                dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
            dialog.show()
            application.processEvents()
            dialog.grab().save(str(output / (name + '.png')))
            dialog.close()
        window.voice_worker = None
        window._saved_project_snapshot = window._project_snapshot()
        window.close()
        application.processEvents()
        print('Playback toolbar Save/Discard/confirmation and startup defaults verified.')
        print('Captured', len(list(output.glob('*.png'))), 'real Qt UI views in', output)


if __name__ == '__main__':
    main()
