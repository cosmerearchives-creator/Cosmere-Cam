"""Recording choices and review of complete-script word timings."""
from pathlib import Path
from PySide6.QtCore import Qt,QThread,Signal,QUrl,QTimer
from PySide6.QtGui import QColor,QDesktopServices
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QComboBox, QDoubleSpinBox, QPushButton, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QMessageBox,QProgressBar)
from update_core import atomic_json


class RecordingProgress(QDialog):
    def __init__(self,parent,recorder):
        super().__init__(parent);self.recorder=recorder;self.setWindowTitle('Recording processing');self.resize(600,220)
        box=QVBoxLayout(self);self.stage=QLabel('Finalizing capture…');self.stage.setWordWrap(True);box.addWidget(self.stage)
        self.bar=QProgressBar();self.bar.setFormat('%p% of this stage');self.bar.setRange(0,0);box.addWidget(self.bar)
        self.details=QLabel();self.details.setWordWrap(True);box.addWidget(self.details)
        open_folder=QPushButton('Open Diagnostics Folder');open_folder.clicked.connect(self.open_folder);box.addWidget(open_folder)
        close=QPushButton('Hide progress');close.clicked.connect(self.hide);box.addWidget(close)
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(200)
    def open_folder(self):
        folder=self.recorder.path.with_suffix('.processing')
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder if folder.is_dir() else folder.parent)))
    def refresh(self):
        state=dict(self.recorder.processing_progress);name=state.get('stage','Finalizing capture')
        percent=state.get('percent')
        if percent is None:self.bar.setRange(0,0)
        else:self.bar.setRange(0,100);self.bar.setValue(round(percent))
        hint='No recent progress detected; checking for a stall.' if state.get('stalled') else ('Working slowly' if str(state.get('speed','')).startswith('0.') else 'Working')
        self.stage.setText(name)
        self.details.setText(f"Elapsed: {state.get('elapsed_seconds',0):.0f}s • Stage: {state.get('stage_seconds',0):.0f}s\n{hint}  {state.get('speed','')}")
        if self.recorder.finished:
            self.timer.stop();self.bar.setRange(0,100)
            if self.recorder.error or self.recorder.processing_error:self.stage.setText('Processing stopped. Original or recovery take retained.');self.bar.setValue(0)
            elif self.recorder.awaiting_review:self.stage.setText('Waiting for your word-timing review');self.bar.setValue(100)
            else:self.stage.setText('Recording saved');self.bar.setValue(100)


class ProcessingChoices(QDialog):
    def __init__(self, parent, audio, model):
        super().__init__(parent)
        self.setWindowTitle('Process this recording')
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Capture has stopped. Choose what to run before saving.'))
        self.trim = QCheckBox('Automatic trimming')
        self.trim.setEnabled(audio)
        layout.addWidget(self.trim)
        self.trim_mode = QComboBox()
        self.trim_mode.addItem('Trim silence at beginning and end only', 'edges')
        self.trim_mode.addItem('Also shorten long pauses within the recording', 'pauses')
        self.trim_mode.setEnabled(False)
        self.trim.toggled.connect(self.trim_mode.setEnabled)
        layout.addWidget(self.trim_mode)
        self.minimum_pause=QDoubleSpinBox();self.minimum_pause.setRange(.6,30);self.minimum_pause.setValue(1.2)
        self.minimum_pause.setSuffix(' seconds — minimum internal pause to shorten')
        layout.addWidget(self.minimum_pause)
        self.align = QCheckBox('Align the full script to recorded speech and review timings')
        self.align.setEnabled(audio and model)
        layout.addWidget(self.align)
        self.advanced=QComboBox()
        self.advanced.addItem('Advanced editing: off','off')
        self.advanced.addItem('Assisted: review proposed retake cuts','assisted')
        self.advanced.addItem('Automatic: apply confident retake cuts; keep uncertain sections','automatic')
        editable=getattr(parent,'keep_editable',None)
        eligible=bool(audio and model and (editable is None or editable.isChecked()))
        self.align.setEnabled(eligible)
        self.advanced.setEnabled(eligible)
        if editable is not None and not editable.isChecked():
            layout.addWidget(QLabel('Corrected text export requires editable source preparation before recording.'))
        self.advanced.currentIndexChanged.connect(lambda index:self.align.setChecked(True) if index else None)
        layout.addWidget(self.advanced)
        note=QLabel('Advanced editing currently detects script-based repeated/abandoned attempts. Cough and filler detection are planned separately.')
        note.setWordWrap(True);layout.addWidget(note)
        if not audio or not model:
            label = QLabel('Trimming needs recorded microphone audio. Word alignment also needs the offline English speech model installed.')
            label.setWordWrap(True)
            layout.addWidget(label)
        buttons = QHBoxLayout()
        plain = QPushButton('Save without processing')
        plain.clicked.connect(self.reject)
        buttons.addWidget(plain)
        selected = QPushButton('Run selected processes')
        selected.clicked.connect(self.accept)
        buttons.addWidget(selected)
        layout.addLayout(buttons)

    def options(self):
        if self.exec() != QDialog.DialogCode.Accepted:
            return {}
        return dict(trim=self.trim_mode.currentData() if self.trim.isChecked() else 'none',
                    align=self.align.isChecked() or self.advanced.currentData()!='off',
                    advanced=self.advanced.currentData(),minimum_pause=self.minimum_pause.value())


class AlignmentReview(QDialog):
    def __init__(self, parent, data, path):
        super().__init__(parent)
        self.data, self.path = data, Path(path)
        self.export_dialog=None
        self.preview_worker=None
        self.preview_dialog=None
        self.setWindowTitle('Full script — review word timings')
        self.resize(850, 650)
        layout = QVBoxLayout(self)
        note = QLabel('Every script word is retained. Highlighted timings are estimates, not proof the words were spoken. Select a section to approve it, or edit its times.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(len(data['words']), 4)
        self.table.setHorizontalHeaderLabels(['Script word', 'Start (s)', 'End (s)', 'Review status'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, word in enumerate(data['words']):
            for col, key in enumerate(('word', 'start', 'end', 'status')):
                value = f"{word[key]:.3f}" if key in ('start', 'end') else word[key]
                item = QTableWidgetItem(value)
                if key not in ('start', 'end'):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if word['needs_approval']:
                    item.setBackground(QColor('#EDE3FA'))
                self.table.setItem(row, col, item)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.resizeRowsToContents()
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        for text, callback in (('Next section needing approval', self.next_section),
                               ('Approve selected section', self.approve_selected),
                               ('Approve all estimates', self.approve_all),
                               ('Save review', self.save)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status=QLabel('Review timings, save progress, or approve and export.');self.status.setWordWrap(True);layout.addWidget(self.status)
        if data.get('advanced_mode','off')!='off':
            layout.addWidget(QLabel('Advanced cuts — checked rows will be removed. Unchecked rows will be kept.'))
            cuts=data.get('cuts',[])
            self.cut_table=QTableWidget(len(cuts),4)
            self.cut_table.setHorizontalHeaderLabels(['Remove','Start–end (s)','Reason','Confidence'])
            self.cut_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            self.cut_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
            self.cut_table.setMaximumHeight(170)
            for row,cut in enumerate(cuts):
                check=QTableWidgetItem('Remove');check.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable|Qt.ItemFlag.ItemIsUserCheckable)
                check.setCheckState(Qt.CheckState.Checked if cut.get('approved') else Qt.CheckState.Unchecked)
                self.cut_table.setItem(row,0,check)
                for column,value in [(1,f'{cut["start"]:.2f}–{cut["end"]:.2f}'),(2,cut['reason']),(3,'Confident' if cut['confidence']>=.95 else 'Review suggested')]:
                    item=QTableWidgetItem(value);item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable);self.cut_table.setItem(row,column,item)
            layout.addWidget(self.cut_table)
            preview=QPushButton('Preview selected cut / join');preview.setEnabled(bool(data.get('source')));preview.clicked.connect(self.preview_cut);layout.addWidget(preview)
            if not cuts:layout.addWidget(QLabel('No script-based retake cuts were detected. Both exports will have the same duration.'))
        self.export_button=QPushButton('Approve && Export')
        self.export_button.clicked.connect(self.start_export);layout.addWidget(self.export_button)
        if not data.get('source'):
            self.export_button.setEnabled(False)
            self.status.setText('This older recording has baked-in text and no editable source. Review can be saved, but corrected text export requires a new recording with editable source enabled.')
        if data.get('kind')=='edited_alignment_report':
            self.status.setText('Timing report for the edited video. Open the original take’s review to change cuts and re-export.')
        if data.get('source'):
            info=QLabel('Corrected exports use approved script captions. Live entrance/exit animations are not replayed.')
            info.setWordWrap(True);layout.addWidget(info)
        self.table.itemChanged.connect(self.edited)

    def edited(self, item):
        if item.column() in (1, 2):
            word = self.data['words'][item.row()]
            word['needs_approval'] = True
            word['status'] = 'Adjusted timing—needs approval'
            self.table.item(item.row(), 3).setText(word['status'])

    def collect(self):
        if hasattr(self,'cut_table'):
            for row,cut in enumerate(self.data.get('cuts',[])):
                cut['approved']=self.cut_table.item(row,0).checkState()==Qt.CheckState.Checked
        previous = 0.
        for row, word in enumerate(self.data['words']):
            start, end = (float(self.table.item(row, col).text()) for col in (1, 2))
            if not (previous <= start <= end <= self.data['duration'] + .002):
                raise ValueError(f'Check start/end times on word {row+1}; timings must stay in order.')
            word.update(start=start, end=end)
            previous = end
        from edit_pipeline import validate_alignment
        validate_alignment(self.data)

    def save(self):
        try:
            self.collect()
            atomic_json(self.path, self.data)
            self.status.setText('Review saved: '+str(self.path))
            return True
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Review could not be saved', str(exc))
            return False

    def approve(self, rows):
        try:
            self.collect()
        except ValueError as exc:
            QMessageBox.warning(self, 'Check timing', str(exc))
            return
        self.table.blockSignals(True)
        for row in rows:
            word = self.data['words'][row]
            if word['needs_approval']:
                word.update(needs_approval=False, status='Approved timing estimate')
                self.table.item(row, 3).setText(word['status'])
                for col in range(4):
                    self.table.item(row, col).setBackground(QColor('white'))
        self.table.blockSignals(False)
        self.save()

    def approve_selected(self):
        self.approve({index.row() for index in self.table.selectedIndexes()})

    def approve_all(self):
        self.approve(range(len(self.data['words'])))

    def next_section(self):
        current = self.table.currentRow()
        order = list(range(current+1, len(self.data['words']))) + list(range(0, current+1))
        for row in order:
            if self.data['words'][row]['needs_approval']:
                while row > 0 and self.data['words'][row-1]['needs_approval']:
                    row -= 1
                self.table.clearSelection()
                first = self.table.model().index(row, 0)
                last_row = row
                while last_row+1 < len(self.data['words']) and self.data['words'][last_row+1]['needs_approval']:
                    last_row += 1
                from PySide6.QtCore import QItemSelection, QItemSelectionModel
                self.table.selectionModel().select(QItemSelection(first, self.table.model().index(last_row, 3)),
                    QItemSelectionModel.SelectionFlag.Select)
                self.table.scrollToItem(self.table.item(row, 0))
                return

    def start_export(self):
        if self.export_dialog is not None and self.export_dialog.isVisible():
            self.export_dialog.raise_();return
        if not self.save():return
        from edit_pipeline import validate_alignment
        try:validate_alignment(self.data,approved=True)
        except ValueError as error:
            QMessageBox.information(self,'Approval needed',str(error));self.next_section();return
        stem=self.data.get('output_stem',str(self.path.with_suffix('')))
        targets=[Path(stem+'_baseline.mp4')]
        if self.data.get('advanced_mode','off')!='off':targets.append(Path(stem+'_edited.mp4'))
        if any(path.exists() for path in targets):
            if QMessageBox.question(self,'Replace previous exports?','Replace the previous exports for this take? The source take will be retained.',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        from export_ui import ExportDialog
        self.export_dialog=ExportDialog(self,self.data,self.path);self.export_dialog.show()

    def preview_cut(self):
        row=self.cut_table.currentRow()
        if row<0:
            QMessageBox.information(self,'Choose a cut','Select a proposed cut first.');return
        if self.preview_worker is not None and self.preview_worker.isRunning():return
        cut=self.data['cuts'][row]
        self.preview_dialog=CutPreview(self,self.data,cut)
        self.preview_dialog.show()

    def reject(self):
        if self.export_dialog is not None and self.export_dialog.worker is not None and self.export_dialog.worker.isRunning():
            self.export_dialog.raise_();return
        if self.save():
            super().reject()

    def closeEvent(self, event):
        if self.export_dialog is not None and self.export_dialog.worker is not None and self.export_dialog.worker.isRunning():
            self.export_dialog.raise_();event.ignore();return
        if self.save():
            event.accept()
        else:
            event.ignore()


class PreviewWorker(QThread):
    done=Signal(str)
    failed=Signal(str)
    def __init__(self,data,cut,directory,parent):
        super().__init__(parent);self.data=data;self.cut=cut;self.directory=directory
        import threading
        self.cancel=threading.Event()
    def run(self):
        try:
            from edit_pipeline import preview_join
            from recorder import resolve_ffmpeg
            target=preview_join(self.data,self.cut,resolve_ffmpeg(),self.directory,self.cancel)
            self.done.emit(str(target))
        except Exception as error:self.failed.emit(str(error))

class CutPreview(QDialog):
    def __init__(self,parent,data,cut):
        super().__init__(parent)
        import tempfile
        from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
        from PySide6.QtMultimediaWidgets import QVideoWidget
        self.setWindowTitle('Preview proposed join');self.resize(700,500)
        layout=QVBoxLayout(self)
        self.status=QLabel('Preparing join preview. Corrected text appears in the final export.');self.status.setWordWrap(True);layout.addWidget(self.status)
        video=QVideoWidget();layout.addWidget(video)
        self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.player.setAudioOutput(self.audio);self.player.setVideoOutput(video)
        play=QPushButton('Replay join');play.clicked.connect(lambda:(self.player.setPosition(0),self.player.play()));layout.addWidget(play)
        self.temp=tempfile.TemporaryDirectory(prefix='suite_join_')
        self.worker=PreviewWorker(data,cut,self.temp.name,self)
        self.worker.done.connect(self.play);self.worker.failed.connect(self.status.setText);self.worker.start()
    def play(self,path):
        self.status.setText('Join preview — the selected section is removed. Corrected text appears in the final export.')
        self.player.setSource(QUrl.fromLocalFile(path));self.player.play()
    def reject(self):
        self.player.stop()
        if self.worker.isRunning():self.worker.cancel.set();self.worker.finished.connect(self.reject);return
        self.player.setSource(QUrl());self.temp.cleanup();super().reject()
    def closeEvent(self,event):
        if self.worker.isRunning():self.worker.cancel.set();event.ignore();self.worker.finished.connect(self.close)
        else:self.player.stop();self.player.setSource(QUrl());self.temp.cleanup();event.accept()
