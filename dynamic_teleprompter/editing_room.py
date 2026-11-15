"""Select and configure a video or saved take before opening Review & Cut."""
import hashlib
import json
import math
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)


def inspect_video(path):
    from imageio_ffmpeg import read_frames
    reader = read_frames(str(path), input_params=['-protocol_whitelist', 'file,pipe'])
    try:
        info = next(reader)
    finally:
        reader.close()
    duration, fps = float(info['duration']), float(info['fps'])
    width, height = info['source_size']
    if not all(math.isfinite(v) and v > 0 for v in (duration, fps, width, height)):
        raise ValueError('The video has no readable duration or frame size.')
    return duration, dict(original=str(path), scene=str(path), fps=fps, width=width,
                          height=height, audio=bool(info.get('audio_codec')))


class VideoProbe(QThread):
    ready = Signal(float, object)
    failed = Signal(str)

    def __init__(self, path, parent):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            duration, source = inspect_video(self.path)
            self.ready.emit(duration, source)
        except Exception as error:
            self.failed.emit(str(error))


class EditingRoom(QWidget):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.path = None
        self.saved = None
        self.probe = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Script for this edit'))
        self.script = QTextEdit()
        self.script.setAcceptRichText(False)
        self.script.setPlaceholderText('Paste a script to help identify restarts or align captions.')
        self.script.setMinimumHeight(300)
        layout.addWidget(self.script, 1)
        sources = QHBoxLayout()
        layout.addLayout(sources)
        self.import_button = QPushButton('Import Video…')
        self.import_button.clicked.connect(self.import_video)
        sources.addWidget(self.import_button)
        self.open_button = QPushButton('Open Saved Take…')
        self.open_button.clicked.connect(self.open_saved)
        sources.addWidget(self.open_button)
        self.selection = QLabel('Choose a video or a saved take.')
        self.selection.setWordWrap(True)
        layout.addWidget(self.selection)
        self.align = QCheckBox('Align script captions after editing')
        layout.addWidget(self.align)
        self.confirm = QPushButton('Continue to Edit')
        self.confirm.setEnabled(False)
        self.confirm.clicked.connect(self.continue_edit)
        layout.addWidget(self.confirm)

    def import_video(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Import Video', self.main.record_dir,
            'Video (*.mp4 *.mov *.mkv *.webm *.avi *.m4v);;All files (*)')
        if filename:
            self.path = Path(filename).resolve()
            self.saved = None
            self.align.setChecked(False)
            self.selection.setText(str(self.path))
            self.confirm.setEnabled(True)

    def open_saved(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Open Saved Take', self.main.record_dir,
                                                 'Saved take (*.cuts.json)')
        if not filename:
            return
        try:
            path = Path(filename).resolve()
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('schema') != 1 or data.get('kind') != 'cut_review':
                raise ValueError('Choose a saved Review & Cut file.')
            if not Path(data['source']['original']).is_file():
                raise ValueError('The original video referenced by this take is missing.')
            self.path, self.saved = path, data
            self.script.setPlainText(data['script'])
            self.align.setChecked(data.get('align_captions', True))
            self.selection.setText(str(path))
            self.confirm.setEnabled(True)
        except (OSError, ValueError, KeyError, TypeError) as error:
            QMessageBox.warning(self, 'Could not open saved take', str(error))

    def continue_edit(self):
        if self.align.isChecked() and not self.script.toPlainText().strip():
            QMessageBox.information(self, 'Script needed', 'Enter a script or turn off caption alignment.')
            return
        if self.saved is not None:
            self.open_review(dict(self.saved), self.path)
            return
        if self.align.isChecked() and QMessageBox.question(self, 'Add captions?',
                'New script captions will appear over this video. Any captions already in the picture will remain visible.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.setEnabled(False)
        self.selection.setText('Reading video information…')
        self.probe = VideoProbe(self.path, self)
        self.probe.ready.connect(self.import_ready)
        self.probe.failed.connect(self.import_failed)
        self.probe.finished.connect(self.probe_finished)
        self.probe.start()

    def probe_finished(self):
        self.probe.deleteLater()
        self.probe = None
        self.setEnabled(True)

    def import_failed(self, error):
        self.selection.setText(str(self.path))
        QMessageBox.warning(self, 'Could not read video', error)

    def import_ready(self, duration, source):
        self.selection.setText(str(self.path))
        folder = Path(self.main.record_dir).expanduser().resolve()
        key = hashlib.sha256(str(self.path).casefold().encode()).hexdigest()[:10]
        stem = folder / f'{self.path.stem}-import-{key}'
        review_path = Path(str(stem) + '.cuts.json')
        try:
            folder.mkdir(parents=True, exist_ok=True)
            data = dict(schema=1, kind='cut_review', script='', duration=duration, source=source,
                        output_stem=str(stem), processing_directory=str(stem) + '.processing',
                        render_project=self.main._project_payload(),
                        source_stamp=self.path.stat().st_mtime_ns)
            if review_path.is_file():
                previous = json.loads(review_path.read_text(encoding='utf-8'))
                if (previous.get('duration') == duration and previous.get('source_stamp') == data['source_stamp']
                        and previous.get('source', {}).get('original') == str(self.path)):
                    for field in ('sections', 'keep_start', 'keep_end', 'caption_review', 'script'):
                        if field in previous:
                            data[field] = previous[field]
            self.open_review(data, review_path)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.import_failed(str(error))

    def open_review(self, data, path):
        from app import MODEL_DIR, groups_from_script
        script = self.script.toPlainText()
        if data.get('align_captions', True) != self.align.isChecked():
            data.pop('caption_review', None)
        data['align_captions'] = self.align.isChecked()
        source = dict(data['source'])
        if data['align_captions'] and (not Path(source['scene']).is_file() or
                (source.get('foreground') and not Path(source['foreground']).is_file())):
            QMessageBox.warning(self, 'Editable tracks missing', 'Turn off caption alignment to edit the original video.')
            return
        if script != data['script']:
            data.pop('caption_review', None)
        data['script'] = script
        data['scanned'] = False
        data['model_path'] = self.main.local_settings.value('voice_model_path', '', type=str) or str(MODEL_DIR)
        data['pronunciations'] = list(self.main.pronunciation_entries)
        project = dict(data['render_project'])
        project['groups'] = groups_from_script(script, self.main.grouping.currentText(),
            self.main.group_limit.value(), self.main.chars.value(), self.main.lines.value())
        data['render_project'] = project
        self.main._open_cut_review(data, path)
