"""GUI-thread caption rasterization followed by cancellable background FFmpeg exports."""
import tempfile
import threading
from pathlib import Path
from PySide6.QtCore import QThread,Signal,QTimer,QUrl,Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QProgressBar,QPushButton,QMessageBox
from recorder import resolve_ffmpeg
from edit_pipeline import caption_events,write_concat,export_videos

class ExportWorker(QThread):
    progress=Signal(str)
    completed=Signal(dict)
    failed=Signal(str)
    def __init__(self,data,concat,stem,work,ffmpeg,parent=None,session=None):
        super().__init__(parent);self.data=data;self.concat=concat;self.stem=stem;self.work=work;self.ffmpeg=ffmpeg
        self.cancel=threading.Event();self.session=session
    def run(self):
        from processing_session import active
        def stage(name):
            self.session.begin(name);self.session.duration=self.data['duration'];self.progress.emit(name)
        try:
            with active(self.session):result=export_videos(self.data,self.concat,self.stem,self.ffmpeg,self.work,stage,self.cancel)
            self.session.finish('complete');self.completed.emit(result)
        except Exception as error:
            self.session.finish('cancelled' if self.cancel.is_set() else 'failed');self.failed.emit(str(error))

class ExportDialog(QDialog):
    def __init__(self,parent,data,review_path):
        super().__init__(parent)
        self.data=data;self.review_path=Path(review_path);self.worker=None;self.canvas=None;self._session_finished=False;self._render_failed=False
        from processing_session import ProcessingSession
        self.session=ProcessingSession(data.get('processing_directory',self.review_path.with_suffix('.processing')))
        self.session.begin_export()
        self.setWindowTitle('Approve & Export');self.resize(640,280)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        layout=QVBoxLayout(self)
        self.status=QLabel('Preparing approved text…');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.bar=QProgressBar();layout.addWidget(self.bar)
        self.elapsed=QLabel();layout.addWidget(self.elapsed)
        diagnostics=QPushButton('Open Diagnostics Folder');diagnostics.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.session.directory))));layout.addWidget(diagnostics)
        self.progress_timer=QTimer(self);self.progress_timer.timeout.connect(self.show_progress);self.progress_timer.start(200)
        self.cancel_button=QPushButton('Cancel export');self.cancel_button.clicked.connect(self.cancel_export);layout.addWidget(self.cancel_button)
        self.open_button=QPushButton('Open export folder');self.open_button.hide();layout.addWidget(self.open_button)
        self.temp=tempfile.TemporaryDirectory(prefix='suite_export_');self.work=Path(self.temp.name)
        self.project=data.get('render_project',{})
        self.groups=self.project.get('groups') or [data['script']]
        self.events=caption_events(data,self.groups);self.images=[];self.position=0
        self.bar.setRange(0,len(self.events))
        self.timer=QTimer(self);self.timer.timeout.connect(self.render_next);self.timer.start(0)
    def render_next(self):
        try:
            if self.canvas is None:
                from app import OutputWindow
                self.canvas=OutputWindow()
            if self.position>=len(self.events):
                self.timer.stop();self.canvas.close();self.canvas=None
                concat=self.work/'captions.ffconcat';write_concat(self.events,self.images,self.data['duration'],concat)
                ffmpeg=resolve_ffmpeg()
                if not ffmpeg:raise RuntimeError('FFmpeg is unavailable.')
                stem=self.data.get('output_stem') or str(self.review_path.with_suffix(''))
                self.worker=ExportWorker(self.data,concat,stem,self.work,ffmpeg,self,session=self.session)
                self.worker.progress.connect(self.status.setText);self.worker.completed.connect(self.finished_export)
                self.worker.failed.connect(self.failed_export);self.worker.finished.connect(self.worker_stopped)
                self.bar.setRange(0,0);self.worker.start();return
            time,text,number=self.events[self.position]
            settings=dict(self.project.get('settings',{}))
            layer=next((dict(layer) for layer in settings.get('media_layers',[]) if layer.get('type')=='text'),dict(type='text',visible=True))
            behavior=settings.get('behavior','Replace')
            settings.update(media_layers=[layer],transparent=True,background_image='',mode='Script',instant=True,behavior='Replace',motion={})
            settings['canvas_resolution']=f'{self.data["source"]["width"]}x{self.data["source"]["height"]}'
            visual={} if number+1 in self.project.get('global_effect_exclusions',[]) else dict(self.project.get('visual_global',{}))
            visual.update(self.project.get('slide_visuals',{}).get(str(number+1),{}));settings['visual']=visual
            template=self.groups[number] if number<len(self.groups) else text
            if behavior in ('Stack','Scroll'):
                previous=self.groups[max(0,number-(3 if behavior=='Stack' else 5)):number]
                text='\n'.join(previous+[text])
                template='\n'.join(previous+[template])
            self.canvas.set_output([text,'',''],settings,layout_templates=[template,'',''])
            path=self.work/f'{self.position:06d}.png'
            if not self.canvas.render_frame(force=True).save(str(path)):raise OSError('Could not write caption frame.')
            self.images.append(path);self.position+=1;self.bar.setValue(self.position)
            self.status.setText(f'Preparing text: {self.position} / {len(self.events)}')
        except Exception as error:
            self.timer.stop();self.failed_export(str(error));self.worker_stopped()
    def show_progress(self):
        import time
        self.elapsed.setText(f'Elapsed: {time.monotonic()-self.session.started:.0f}s')
        if self.worker is None or not self.worker.isRunning():return
        state=dict(self.session.state);percent=state.get('percent')
        if percent is None:self.bar.setRange(0,0)
        else:self.bar.setRange(0,100);self.bar.setValue(round(percent))
        self.status.setText(state['stage']+(' — no recent progress detected' if state.get('stalled') else '')+(' • '+str(state['speed']) if state.get('speed') else ''))

    def finished_export(self,result):
        from update_core import atomic_json
        self.data['exports']=result
        try:atomic_json(self.review_path,self.data)
        except OSError as error:
            result['review_warning']='Video exported; review metadata could not be updated: '+str(error)
        self.status.setText('Export complete.\n\n'+'\n'.join(f'{key}: {value}' for key,value in result.items()))
        self.open_button.show();folder=Path(result['baseline']).parent
        self.open_button.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))
        self.bar.setRange(0,1);self.bar.setValue(1)
    def failed_export(self,message):
        self._render_failed=True
        self.status.setText('Export stopped. Source take retained.\n'+message)
        self.bar.setRange(0,1);self.bar.setValue(0)
    def worker_stopped(self):
        if self.worker is None and not self._session_finished:self.session.finish('failed' if self._render_failed else 'cancelled')
        self._session_finished=True
        if self.canvas is not None:self.canvas.close();self.canvas=None
        self.progress_timer.stop()
        self.temp.cleanup();self.cancel_button.setText('Close');self.cancel_button.setEnabled(True)
    def cancel_export(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel.set();self.cancel_button.setEnabled(False);self.status.setText('Cancelling export…');return
        self.timer.stop();self.worker_stopped();self.accept()
    def reject(self):
        if self.worker is not None and self.worker.isRunning():self.cancel_export();return
        self.timer.stop();self.worker_stopped();super().reject()
    def closeEvent(self,event):
        if self.worker is not None and self.worker.isRunning():self.cancel_export();event.ignore()
        else:self.timer.stop();self.worker_stopped();event.accept()
