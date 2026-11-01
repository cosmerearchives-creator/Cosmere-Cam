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
        window.resize(1280, 900)
        window.show()
        output = ROOT / 'build/ui-preview'
        output.mkdir(parents=True, exist_ok=True)
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
        window._saved_project_snapshot = window._project_snapshot()
        window.close()
        application.processEvents()
        print('Captured', len(list(output.glob('*.png'))), 'real Qt UI views in', output)


if __name__ == '__main__':
    main()
