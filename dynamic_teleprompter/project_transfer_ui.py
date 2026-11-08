"""Cancellable background project transfer with a concrete size/time warning."""
from PySide6.QtCore import QThread,Signal
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QProgressBar,QPushButton,QMessageBox
import threading
from pathlib import Path
from project_package import plan_project,export_project,load_exported_project

class TransferWorker(QThread):
    done=Signal(object);failed=Signal(str);progress=Signal(int)
    def __init__(self,operation,parent=None):super().__init__(parent);self.operation=operation;self.cancel=threading.Event()
    def run(self):
        try:self.done.emit(self.operation(self.progress.emit,self.cancel))
        except Exception as error:self.failed.emit(str(error))

class ProjectTransfer(QDialog):
    imported=Signal(object,object)
    def __init__(self,parent,project=None,destination=None,folder=None):
        super().__init__(parent);self.project=project;self.destination=destination;self.folder=folder;self.cancelled=False;self.worker=None
        self.setWindowTitle('Export Project' if project is not None else 'Open Exported Project');self.resize(600,230)
        layout=QVBoxLayout(self);self.status=QLabel('Inspecting project assets…' if project is not None else 'Checking extracted project…');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.bar=QProgressBar();self.bar.setRange(0,0);layout.addWidget(self.bar)
        self.button=QPushButton('Cancel');self.button.clicked.connect(self.reject);layout.addWidget(self.button)
        if project is not None:self.start(lambda progress,cancel:plan_project(project),self.confirm)
        else:self.start(lambda progress,cancel:load_exported_project(folder,progress,cancel),self.loaded)
    def start(self,operation,done):
        self.worker=TransferWorker(operation,self);self.worker.progress.connect(self.percent)
        self.worker.done.connect(done);self.worker.failed.connect(self.failure);self.worker.start()
    def percent(self,value):self.bar.setRange(0,100);self.bar.setValue(value)
    def confirm(self,plan):
        if self.cancelled:return
        size=plan['total_bytes']/1024**2
        text=f"This export includes {len(plan['files'])} media/model files (about {size:,.1f} MB before ZIP overhead).\n\nLarge videos and models can make the export large and take several minutes or longer. Keep the app open until it finishes. Plugins, fonts and the speech/Live2D runtimes are separate installations.\n\nCreate the project ZIP?"
        answer=QMessageBox.question(self,'Export Project',text,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:self.reject();return
        self.status.setText('Exporting project and referenced assets…');self.percent(0)
        self.start(lambda progress,cancel:export_project(plan,self.destination,progress,cancel),self.saved)
    def saved(self,path):self.status.setText('Project exported: '+path+'\nExtract the ZIP, then use Open Exported Project and select its folder.');self.percent(100);self.button.setText('Close')
    def loaded(self,result):
        if self.cancelled:return
        data,warnings=result
        if warnings and QMessageBox.question(self,'Changed media','Some files were edited after export. Use the current files?\n\n'+'\n'.join(warnings[:8]),QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:self.reject();return
        self.imported.emit(data,Path(self.folder));self.accept()
    def failure(self,message):self.status.setText(message);self.bar.setRange(0,1);self.bar.setValue(0);self.button.setText('Close')
    def reject(self):
        self.cancelled=True
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel.set();self.status.setText('Cancelling project transfer…');self.worker.finished.connect(self.reject);return
        super().reject()
    def closeEvent(self,event):
        if self.worker is not None and self.worker.isRunning():self.reject();event.ignore()
        else:event.accept()
