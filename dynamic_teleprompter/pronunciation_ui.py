"""Speech-based fantasy name setup; all microphone work stays on VoiceWorker."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QGroupBox,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QListWidget,QMessageBox
from pronunciations import validate,save,tokens

class PronunciationPanel(QGroupBox):
    def __init__(self,window):
        super().__init__('Fantasy Name Pronunciation');self.window=window;self.worker=None
        box=QVBoxLayout(self)
        note=QLabel('Type the correct name, then say only that name a few times, pausing between attempts. Confirm what the recognizer hears; no phonetic spelling is needed.');note.setWordWrap(True);box.addWidget(note)
        self.name=QLineEdit();self.name.setPlaceholderText('Correct name, for example Dalinar');box.addWidget(self.name)
        self.heard=QLineEdit();self.heard.setPlaceholderText('Recognized alternatives appear here; separate alternatives with semicolons');box.addWidget(self.heard)
        self.name.textEdited.connect(lambda _:self.heard.clear())
        row=QHBoxLayout();self.listen=QPushButton('Listen');self.listen.clicked.connect(self.toggle);row.addWidget(self.listen)
        commit=QPushButton('Confirm and Save');commit.clicked.connect(self.commit);row.addWidget(commit);box.addLayout(row)
        self.status=QLabel('Ready. Saved examples help voice following and recorded-word alignment.');self.status.setWordWrap(True);box.addWidget(self.status)
        self.names=QListWidget();self.names.setMaximumHeight(140);self.names.itemClicked.connect(self.select);box.addWidget(self.names)
        remove=QPushButton('Remove selected name');remove.clicked.connect(self.remove);box.addWidget(remove);self.refresh()
    def refresh(self):
        self.window.script.proofreader.pronunciation_words={word for entry in self.window.pronunciation_entries for word in tokens(entry['name'])}
        self.window.script.proofreader.rehighlight()
        self.names.clear()
        for item in self.window.pronunciation_entries:self.names.addItem(item['name']+' — '+'; '.join(item['alternatives']))
    def select(self,item):
        row=self.names.row(item);entry=self.window.pronunciation_entries[row];self.name.setText(entry['name']);self.heard.setText('; '.join(entry['alternatives']))
    def toggle(self):
        if self.worker is not None and self.worker.isRunning():self.stop();return
        if not self.name.text().strip():self.status.setText('Enter the correctly spelled name first.');return
        if self.window.recorder is not None or any(worker is not None and worker.isRunning() for worker in (self.window.voice_worker,self.window.test_voice_worker)):
            self.status.setText('Stop recording and other speech tests before listening to a name.');return
        if not self.window._voice_model_available():self.status.setText('Install or choose an offline English speech model first.');return
        from app import VoiceWorker
        self.worker=VoiceWorker(self.window.microphones.currentData(),'',self.window.local_settings.value('voice_model_path','',type=str) or None)
        worker=self.worker;self.name.setEnabled(False);self.names.setEnabled(False);self.listen.setText('Stop listening');self.status.setText('Listening. Say the name alone, pause, and repeat.')
        worker.recognized.connect(self.receive);worker.failed.connect(self.status.setText);worker.finished.connect(lambda:(self.listen.setText('Listen'),self.name.setEnabled(True),self.names.setEnabled(True)))
        worker.start();QTimer.singleShot(30000,lambda:worker.stop() if worker.isRunning() else None)
    def receive(self,text,final):
        if not final or not text.strip():return
        examples=[word.strip() for word in self.heard.text().split(';') if word.strip()]
        if text.strip() not in examples:examples.append(text.strip())
        self.heard.setText('; '.join(examples));self.status.setText('Heard: '+text+'. Confirm and save when these examples represent your pronunciation.')
    def stop(self):
        if self.worker is not None:self.worker.stop()
    def commit(self):
        if self.worker is not None and self.worker.isRunning():self.status.setText('Stop listening before confirming these examples.');return
        try:
            name=self.name.text().strip();entries=[item for item in self.window.pronunciation_entries if item['name'].casefold()!=name.casefold()]
            entries.append(dict(name=name,alternatives=[value.strip() for value in self.heard.text().split(';') if value.strip()]))
            validated=validate(entries)
            if not any(item['name']==name for item in validated):raise ValueError('Listen to the name first, or enter a recognized alternative.')
            self.window.pronunciation_entries=save(validated);self.refresh();self.status.setText('Saved. The correct spelling stays in your script.')
        except (OSError,ValueError) as error:QMessageBox.warning(self,'Check pronunciation',str(error))
    def remove(self):
        row=self.names.currentRow()
        if row<0:return
        entries=list(self.window.pronunciation_entries);entries.pop(row)
        self.window.pronunciation_entries=save(entries);self.refresh()
