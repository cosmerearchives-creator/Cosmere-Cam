"""Review a take, mark removable ranges and prepare full-script captions."""
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QShortcut, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QListWidget,
    QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from manual_edit import frame_time, kept_ranges, prepare_captions, suggest
from recorder import resolve_processing_ffmpeg
from review_media import WAVEFORM_RATE, build_preview, build_waveform
from update_core import atomic_json


class PreviewCanvas(QWidget):
    """Center the video surface inside the available space."""
    def __init__(self, width, height, parent):
        super().__init__(parent)
        self.aspect = width / height
        self.video = QVideoWidget(self)
        self.video.setStyleSheet('background-color: #211b2b;')
        self.setMinimumHeight(220)

    def resizeEvent(self, event):
        width = min(self.width(), round(self.height() * self.aspect))
        height = min(self.height(), round(width / self.aspect))
        self.bounds = QRectF((self.width() - width) // 2, (self.height() - height) // 2,
                             width, height)
        self.video.setGeometry(self.bounds.adjusted(1, 1, -1, -1).toRect())
        super().resizeEvent(event)

    def paintEvent(self, event):
        if hasattr(self, 'bounds'):
            painter = QPainter(self)
            painter.fillRect(self.bounds, QColor('#66547f'))


class Waveform(QLabel):
    seek = Signal(float)

    def __init__(self, duration, has_audio, parent):
        super().__init__(parent)
        self.duration = duration
        self.position = 0.
        self.columns = []
        self.peaks = None
        self.message = 'Reading audio levels…' if has_audio else 'No recorded audio'
        self.setFixedHeight(64)
        self.setAccessibleName('Audio waveform')

    def set_peaks(self, peaks):
        self.peaks = peaks
        self.rebuild_columns()

    def resizeEvent(self, event):
        self.rebuild_columns()
        super().resizeEvent(event)

    def rebuild_columns(self):
        if self.peaks is not None:
            width = max(1, self.width() - 16)
            bins = self.duration * WAVEFORM_RATE
            self.columns = []
            for x in range(width):
                start = int(x * bins / width)
                end = max(start + 1, int((x + 1) * bins / width))
                self.columns.append(max(self.peaks[start:end], default=0.))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        width = max(1, self.width() - 16)
        center = self.height() // 2
        painter.fillRect(QRectF(8, 0, width, self.height()), QColor('#302943'))
        if self.peaks is None:
            painter.setPen(QColor('#f1e8ff'))
            painter.drawText(QRectF(8, 0, width, self.height()), Qt.AlignmentFlag.AlignCenter, self.message)
        else:
            painter.setPen(QColor('#c6adff'))
            for x, peak in enumerate(self.columns):
                height = round(peak ** .6 * (center - 3))
                painter.drawLine(x + 8, center - height, x + 8, center + height)
        x = 8 + self.position * width / self.duration
        painter.fillRect(QRectF(x, 0, 1, self.height()), QColor('#ffffff'))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.seek_at(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.seek_at(event)

    def seek_at(self, event):
        fraction = (event.position().x() - 8) / max(1, self.width() - 16)
        self.seek.emit(self.duration * max(0., min(1., fraction)))


class PreviewWorker(QThread):
    peaks_ready = Signal(object)
    preview_ready = Signal(str)
    waveform_failed = Signal()
    preview_failed = Signal()

    def __init__(self, source, directory, parent):
        super().__init__(parent)
        self.source = source
        self.directory = directory
        self.cancel = threading.Event()

    def run(self):
        from processing_session import ProcessingSession, active
        ffmpeg = resolve_processing_ffmpeg()
        source = self.source['original']
        try:
            session = ProcessingSession(Path(self.directory) / 'diagnostics')
            with active(session):
                self.prepare(ffmpeg, source, session)
        except Exception:
            if not self.cancel.is_set():
                self.preview_failed.emit()
                if self.source['audio']:
                    self.waveform_failed.emit()

    def prepare(self, ffmpeg, source, session):
        if self.source['audio']:
            session.begin('Reading audio levels')
            try:
                self.peaks_ready.emit(build_waveform(ffmpeg, source, self.directory, self.cancel))
            except Exception:
                if not self.cancel.is_set():
                    self.waveform_failed.emit()
        if self.cancel.is_set():
            return
        try:
            session.begin('Preparing faster seeking')
            preview = build_preview(ffmpeg, source, self.directory, self.cancel)
            if not self.cancel.is_set():
                self.preview_ready.emit(str(preview))
        except Exception:
            if not self.cancel.is_set():
                self.preview_failed.emit()


class Timeline(QLabel):
    seek = Signal(float)
    trim = Signal(int, float)

    def __init__(self, duration, parent):
        super().__init__(parent)
        self.duration = duration
        self.position = 0.
        self.selection = None
        self.cuts = []
        self.suggestions = []
        self.edges = (0., duration)
        self.drag_edge = None
        self.setMinimumHeight(45)
        self.setAccessibleName('Recording timeline')

    def paintEvent(self, event):
        painter = QPainter(self)
        scale = (self.width() - 16) / self.duration
        painter.fillRect(QRectF(8, 12, self.width() - 16, 20), QColor('#52466c'))
        for ranges, color in ((self.suggestions, '#dba758'), (self.cuts, '#a6485c')):
            for cut in ranges:
                painter.fillRect(QRectF(8 + cut['start'] * scale, 12,
                                        (cut['end'] - cut['start']) * scale, 20), QColor(color))
        if self.selection:
            start, end = self.selection
            painter.setPen(QColor('#eee0ff'))
            painter.drawRect(QRectF(8 + start * scale, 9, (end - start) * scale, 26))
        for edge in self.edges:
            painter.fillRect(QRectF(5 + edge * scale, 7, 6, 30), QColor('#ded0fa'))
        painter.fillRect(QRectF(7 + self.position * scale, 4, 2, 37), QColor('#ffffff'))

    def _time(self, event):
        return self.duration * max(0., min(1., (event.position().x() - 8) / (self.width() - 16)))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        time = self._time(event)
        closest = min(range(2), key=lambda edge: abs(self.edges[edge] - time))
        if abs(self.edges[closest] - time) * (self.width() - 16) / self.duration <= 8:
            self.drag_edge = closest
        else:
            self.seek.emit(time)

    def mouseMoveEvent(self, event):
        if self.drag_edge is not None:
            self.trim.emit(self.drag_edge, self._time(event))
        elif event.buttons() & Qt.MouseButton.LeftButton:
            self.seek.emit(self._time(event))

    def mouseReleaseEvent(self, event):
        if self.drag_edge is not None:
            self.trim.emit(self.drag_edge, self._time(event))
            self.drag_edge = None


class EditWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, data, cuts, work, parent):
        super().__init__(parent)
        self.data, self.cuts, self.work = data, cuts, work
        self.cancel = threading.Event()

    def run(self):
        from processing_session import ProcessingSession, active
        try:
            ffmpeg = resolve_processing_ffmpeg()
            if not ffmpeg:
                raise RuntimeError('FFmpeg is unavailable.')
            if self.cuts is None:
                session = ProcessingSession(Path(self.data['processing_directory']) / 'suggestions')
                session.duration = self.data['duration']
                session.begin('Finding suggested cuts')
                try:
                    with active(session):
                        result = suggest(self.data, ffmpeg, self.work, self.cancel)
                    session.finish('cancelled' if self.cancel.is_set() else 'complete')
                except Exception:
                    session.finish('cancelled' if self.cancel.is_set() else 'failed')
                    raise
            else:
                session = ProcessingSession(self.data['processing_directory'])
                session.begin_export()
                session.duration = sum(b - a for a, b in kept_ranges(self.data, self.cuts))
                def stage(name):
                    session.begin(name)
                    self.progress.emit(name)
                try:
                    with active(session):
                        result = prepare_captions(self.data, self.cuts, ffmpeg, self.work, self.cancel, stage)
                    session.finish('review')
                except Exception:
                    session.finish('cancelled' if self.cancel.is_set() else 'failed')
                    raise
            if not self.cancel.is_set():
                self.ready.emit(result)
        except Exception as error:
            if not self.cancel.is_set():
                self.failed.emit(str(error))


class ReviewCut(QDialog):
    def __init__(self, parent, data, path):
        super().__init__(parent)
        self.data, self.path = data, Path(path)
        self.duration = float(data['duration'])
        self.sections = list(data.get('sections', []))
        self.suggestions = list(data.get('suggestions', []))
        self.worker = None
        self.work = None
        self.closing = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('Review & Cut')
        self.resize(1000, 720)
        layout = QVBoxLayout(self)
        source = data['source']
        canvas = PreviewCanvas(source['width'], source['height'], self)
        layout.addWidget(canvas, 1)
        self.fast_preview = None
        self.preview_active = False
        self.restore_position = None
        self.requested_position = None
        self.seek_timer = QTimer(self)
        self.seek_timer.setSingleShot(True)
        self.seek_timer.timeout.connect(self.apply_scrub)
        self.preview_note = QLabel('Preparing faster seeking…')
        layout.addWidget(self.preview_note)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(canvas.video)
        self.player.errorOccurred.connect(lambda _, error: self.status.setText(error))
        self.timeline = Timeline(self.duration, self)
        layout.addWidget(self.timeline)
        self.timeline.seek.connect(self.scrub)
        self.timeline.trim.connect(self.drag_trim)
        self.waveform = Waveform(self.duration, source['audio'], self)
        layout.addWidget(self.waveform)
        self.waveform.seek.connect(self.scrub)
        playback = QHBoxLayout()
        layout.addLayout(playback)
        self.button(playback, 'Play / Pause', self.toggle_play)
        self.button(playback, 'Previous Frame', lambda: self.step(-1))
        self.button(playback, 'Next Frame', lambda: self.step(1))
        self.skip = QCheckBox('Skip removed sections')
        self.skip.setChecked(True)
        playback.addWidget(self.skip)
        self.position_label = QLabel()
        playback.addWidget(self.position_label)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setFixedWidth(100)
        self.level.setTextVisible(False)
        self.level.setAccessibleName('Audio level')
        self.level.setToolTip('Recorded audio peak at the playhead')
        self.level.setVisible(source['audio'])
        playback.addWidget(self.level)
        trim = QHBoxLayout()
        layout.addLayout(trim)
        self.start = self.spin(trim, 'Keep from', data.get('keep_start', 0.))
        self.end = self.spin(trim, 'Keep until', data.get('keep_end', self.duration))
        self.button(trim, 'Set Start', lambda: self.set_trim(0))
        self.button(trim, 'Set End', lambda: self.set_trim(1))
        self.start.valueChanged.connect(self.refresh)
        self.end.valueChanged.connect(self.refresh)
        selection = QHBoxLayout()
        layout.addLayout(selection)
        self.cut_start = self.spin(selection, 'Cut from', 0.)
        self.cut_end = self.spin(selection, 'Cut until', 0.)
        self.cut_start.valueChanged.connect(self.selection_changed)
        self.cut_end.valueChanged.connect(self.selection_changed)
        self.button(selection, 'Mark In', lambda: self.cut_start.setValue(self.position()))
        self.button(selection, 'Mark Out', lambda: self.cut_end.setValue(self.position()))
        self.button(selection, 'Remove Section', self.remove_section)
        self.button(selection, 'Preview Join', self.preview_join)
        lists = QHBoxLayout()
        layout.addLayout(lists)
        suggestions = QVBoxLayout()
        lists.addLayout(suggestions, 1)
        suggestions.addWidget(QLabel('Suggested cuts'))
        self.candidates = QListWidget()
        self.candidates.setMaximumHeight(100)
        suggestions.addWidget(self.candidates)
        self.candidates.currentRowChanged.connect(self.select_candidate)
        self.button(suggestions, 'Next Suggestion', self.next_suggestion)
        self.button(suggestions, 'Ignore Suggestion', self.ignore_suggestion)
        removals = QVBoxLayout()
        lists.addLayout(removals, 1)
        removals.addWidget(QLabel('Removed sections'))
        self.removed = QListWidget()
        self.removed.setMaximumHeight(100)
        removals.addWidget(self.removed)
        self.button(removals, 'Restore Selected Section', self.restore_section)
        self.status = QLabel('Set the ends, or mark and remove a section.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        layout.addLayout(actions)
        self.scan = self.button(actions, 'Find Suggestions', self.find_suggestions)
        self.scan.setEnabled(data['source']['audio'])
        self.export = self.button(actions, 'Review Captions && Export', self.prepare_export)
        self.cancel = self.button(actions, 'Cancel Analysis', self.cancel_work)
        self.cancel.hide()
        self.button(actions, 'Close', self.close)
        for key, handler in (('Space', self.toggle_play), ('I', lambda: self.cut_start.setValue(self.position())),
                             ('O', lambda: self.cut_end.setValue(self.position()))):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(handler)
        self.player.positionChanged.connect(self.position_changed)
        self.playback_clock = QTimer(self)
        self.playback_clock.timeout.connect(lambda: self.position_changed(self.player.position()))
        self.playback_clock.start(25)
        self.player.mediaStatusChanged.connect(self.preview_status)
        self.player.playbackStateChanged.connect(self.playback_state_changed)
        self.player.setSource(QUrl.fromLocalFile(source['original']))
        self.preview_work = tempfile.TemporaryDirectory(prefix='suite_preview_', ignore_cleanup_errors=True)
        self.preview_worker = PreviewWorker(dict(source), self.preview_work.name, self)
        self.preview_worker.peaks_ready.connect(self.waveform.set_peaks)
        self.preview_worker.waveform_failed.connect(self.waveform_unavailable)
        self.preview_worker.preview_ready.connect(self.preview_available)
        self.preview_worker.preview_failed.connect(lambda: self.preview_note.setText('Playing original recording'))
        self.preview_worker.finished.connect(self.preview_finished)
        self.preview_worker.start()
        self.refresh_lists()
        self.position_changed(0)
        if data['source']['audio'] and not data.get('scanned'):
            self.find_suggestions()

    def button(self, layout, label, callback):
        button = QPushButton(label, self)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def spin(self, layout, label, value):
        layout.addWidget(QLabel(label))
        spin = QDoubleSpinBox(self)
        spin.setAccessibleName(label)
        spin.setDecimals(3)
        spin.setRange(0., self.duration + .0005)
        spin.setValue(value)
        spin.setSuffix(' s')
        layout.addWidget(spin)
        return spin

    def waveform_unavailable(self):
        self.waveform.message = 'Audio waveform unavailable'
        self.waveform.update()

    def preview_available(self, path):
        if self.closing:
            return
        self.fast_preview = path
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            return
        self.use_fast_preview()

    def use_fast_preview(self):
        if not self.fast_preview or self.preview_active or self.closing:
            return
        self.restore_position = self.position()
        self.preview_active = True
        self.player.setSource(QUrl.fromLocalFile(self.fast_preview))
        self.preview_note.setText('Fast preview ready')

    def playback_state_changed(self, state):
        if state != QMediaPlayer.PlaybackState.PlayingState and self.restore_position is None:
            self.use_fast_preview()

    def preview_status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) and self.restore_position is not None:
            target = self.restore_position
            self.restore_position = None
            self.seek(target)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia and self.preview_active:
            self.restore_position = self.position()
            self.preview_active = False
            self.fast_preview = None
            self.player.setSource(QUrl.fromLocalFile(self.data['source']['original']))
            self.preview_note.setText('Playing original recording')

    def preview_finished(self):
        if self.closing:
            self.close()

    def position(self):
        for pending in (self.requested_position, self.restore_position):
            if pending is not None:
                return pending
        return frame_time(self.player.position() / 1000., self.data)

    def scrub(self, seconds):
        self.requested_position = frame_time(seconds, self.data)
        self.show_position(self.requested_position)
        if not self.seek_timer.isActive():
            self.seek_timer.start(25)

    def apply_scrub(self):
        if self.requested_position is not None:
            self.seek(self.requested_position)

    def seek(self, seconds):
        self.seek_timer.stop()
        self.requested_position = None
        target = frame_time(seconds, self.data)
        if self.restore_position is not None:
            self.restore_position = target
        else:
            self.player.setPosition(round(target * 1000))

    def step(self, direction):
        self.player.pause()
        self.seek(self.position() + direction / self.data['source']['fps'])

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self.skip.isChecked():
                try:
                    kept = kept_ranges(self.data, self.cuts())
                except ValueError as error:
                    self.status.setText(str(error))
                    return
                if self.position() >= kept[-1][1]:
                    self.seek(kept[0][0])
            self.player.play()

    def show_position(self, position):
        self.waveform.position = position
        self.waveform.update()
        peaks = self.waveform.peaks
        index = int(position * WAVEFORM_RATE)
        self.level.setValue(round(100 * peaks[index]) if peaks and 0 <= index < len(peaks) else 0)
        self.timeline.position = position
        self.timeline.update()
        self.position_label.setText(f'{position:.2f} / {self.duration:.2f} s')
    def position_changed(self, ms):
        if self.requested_position is not None or self.restore_position is not None:
            return
        position = ms / 1000.
        self.show_position(position)
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        if self.skip.isChecked():
            try:
                kept = kept_ranges(self.data, self.cuts())
            except ValueError:
                return
            for a, b in kept:
                if a - .0005 <= position < b - .0005:
                    return
                if position < a - .0005:
                    self.seek(a)
                    return
            self.player.pause()

    def set_trim(self, index):
        self.drag_trim(index, self.position())

    def drag_trim(self, index, value):
        start, end = self.edges()
        step = 1 / self.data['source']['fps']
        value = frame_time(value, self.data)
        if index == 0:
            self.start.setValue(max(0., min(end - step, value)))
        else:
            self.end.setValue(min(self.duration, max(start + step, value)))

    def edges(self):
        return frame_time(self.start.value(), self.data), frame_time(self.end.value(), self.data)

    def cuts(self):
        start, end = self.edges()
        if end <= start:
            raise ValueError('Keep Until must be after Keep From.')
        cuts = list(self.sections)
        if start > 0:
            cuts.append(dict(start=0., end=start))
        if end < self.duration:
            cuts.append(dict(start=end, end=self.duration))
        return cuts

    def selection_changed(self):
        try:
            self.timeline.selection = self.selected_range()
        except ValueError:
            self.timeline.selection = None
        self.timeline.update()

    def selected_range(self):
        a = frame_time(self.cut_start.value(), self.data)
        b = frame_time(self.cut_end.value(), self.data)
        if b <= a:
            raise ValueError('Mark the beginning and end of the section first.')
        return a, b

    def preview_join(self):
        try:
            a, b = self.selected_range()
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.player.pause()
        from processing_ui import CutPreview
        source = {**self.data['source'], 'scene': self.data['source']['original'], 'foreground': None}
        preview = CutPreview(self, {**self.data, 'source': source}, dict(start=a, end=b))
        preview.setWindowModality(Qt.WindowModality.WindowModal)
        preview.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        preview.show()

    def remove_section(self):
        try:
            a, b = self.selected_range()
            section = dict(start=a, end=b)
            kept_ranges(self.data, self.cuts() + [section])
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.sections.append(section)
        candidate = self.candidates.currentRow()
        if candidate >= 0 and self.suggestions[candidate]['start'] < b and self.suggestions[candidate]['end'] > a:
            self.suggestions.pop(candidate)
        self.refresh_lists()
        if candidate >= 0 and self.suggestions:
            self.candidates.setCurrentRow(min(candidate, len(self.suggestions) - 1))
        self.status.setText('Section removed. Select it in Removed sections to restore it.')

    def restore_section(self):
        row = self.removed.currentRow()
        if row >= 0:
            self.sections.pop(row)
            self.refresh_lists()

    def select_candidate(self, row):
        if 0 <= row < len(self.suggestions):
            cut = self.suggestions[row]
            self.cut_start.setValue(cut['start'])
            self.cut_end.setValue(cut['end'])
            self.player.pause()
            self.seek(cut['start'])

    def next_suggestion(self):
        if self.suggestions:
            self.candidates.setCurrentRow((self.candidates.currentRow() + 1) % len(self.suggestions))

    def ignore_suggestion(self):
        row = self.candidates.currentRow()
        if row >= 0:
            self.suggestions.pop(row)
            self.refresh_lists()
            if self.suggestions:
                self.candidates.setCurrentRow(min(row, len(self.suggestions) - 1))

    def refresh_lists(self):
        self.candidates.clear()
        for cut in self.suggestions:
            self.candidates.addItem(f"{cut['start']:.2f}–{cut['end']:.2f} s · {cut['reason']}")
        self.removed.clear()
        for cut in self.sections:
            self.removed.addItem(f"{cut['start']:.2f}–{cut['end']:.2f} s")
        self.refresh()

    def refresh(self):
        try:
            cuts = self.cuts()
            kept_ranges(self.data, cuts)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.timeline.cuts = cuts
        self.timeline.suggestions = self.suggestions
        self.timeline.edges = self.edges()
        self.timeline.update()
        self.data.update(sections=self.sections, suggestions=self.suggestions,
                         keep_start=self.edges()[0], keep_end=self.edges()[1])
        try:
            atomic_json(self.path, self.data)
        except OSError as error:
            self.status.setText(f'Could not save cut decisions: {error}')

    def find_suggestions(self):
        self.start_worker(None, 'Looking for possible coughs and repeated speech')

    def prepare_export(self):
        try:
            cuts = self.cuts()
            kept_ranges(self.data, cuts)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.player.pause()
        self.start_worker(cuts, 'Preparing the edited take for caption review')

    def start_worker(self, cuts, message):
        if self.worker is not None:
            return
        self.work = tempfile.TemporaryDirectory(prefix='suite_review_')
        self.worker = EditWorker(dict(self.data), cuts, self.work.name, self)
        self.worker.ready.connect(self.result_ready)
        self.worker.failed.connect(self.status.setText)
        self.worker.progress.connect(self.status.setText)
        self.worker.finished.connect(self.work_finished)
        self.scan.setEnabled(False)
        self.export.setEnabled(False)
        self.cancel.show()
        self.status.setText(message)
        self.worker.start()

    def result_ready(self, result):
        self.result = result

    def cancel_work(self):
        if self.worker:
            self.worker.cancel.set()
            self.status.setText('Cancelling analysis')

    def work_finished(self):
        worker = self.worker
        self.worker = None
        self.cancel.hide()
        self.scan.setEnabled(self.data['source']['audio'])
        self.export.setEnabled(True)
        result = getattr(self, 'result', None)
        self.result = None
        try:
            if self.closing:
                self.close()
            elif worker.cancel.is_set():
                self.status.setText('Analysis cancelled')
            elif result is not None and worker.cuts is None:
                self.suggestions = result
                self.data['scanned'] = True
                self.refresh_lists()
                self.status.setText(f'{len(result)} suggested sections. Review a suggestion before removing it.')
            elif result is not None:
                from processing_ui import AlignmentReview
                review_path = Path(self.work.name) / 'caption-review.json'
                review = AlignmentReview(self, result, review_path)
                review.exec()
                self.data['caption_review'] = dict(
                    kept=[list(span) for span in kept_ranges(worker.data, worker.cuts)],
                    words=result['words'])
                self.refresh()
                # Release the export dialog's references before removing its input media.
                from shiboken6 import delete
                delete(review)
                if result.get('exports'):
                    self.status.setText('Exported ' + result['exports']['edited'])
                else:
                    self.status.setText('Caption review saved.')
        finally:
            worker.deleteLater()
            self.work.cleanup()
            self.work = None

    def reject(self):
        self.close()

    def closeEvent(self, event):
        self.closing = True
        self.preview_worker.cancel.set()
        if self.worker is not None:
            self.cancel_work()
        if self.worker is not None or self.preview_worker.isRunning():
            event.ignore()
            return
        self.seek_timer.stop()
        self.playback_clock.stop()
        self.player.stop()
        self.player.setSource(QUrl())
        QTimer.singleShot(500, self.preview_work.cleanup)
        event.accept()
