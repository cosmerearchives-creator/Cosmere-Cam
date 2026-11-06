"""Recording choices and review of complete-script word timings."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QComboBox, QPushButton, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QMessageBox)
from update_core import atomic_json


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
        self.align = QCheckBox('Align the full script to recorded speech and review timings')
        self.align.setEnabled(audio and model)
        layout.addWidget(self.align)
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
                    align=self.align.isChecked())


class AlignmentReview(QDialog):
    def __init__(self, parent, data, path):
        super().__init__(parent)
        self.data, self.path = data, Path(path)
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
        self.table.itemChanged.connect(self.edited)

    def edited(self, item):
        if item.column() in (1, 2):
            word = self.data['words'][item.row()]
            word['needs_approval'] = True
            word['status'] = 'Adjusted timing—needs approval'
            self.table.item(item.row(), 3).setText(word['status'])

    def collect(self):
        previous = 0.
        for row, word in enumerate(self.data['words']):
            start, end = (float(self.table.item(row, col).text()) for col in (1, 2))
            if not (previous <= start <= end <= self.data['duration'] + .002):
                raise ValueError(f'Check start/end times on word {row+1}; timings must stay in order.')
            word.update(start=start, end=end)
            previous = end

    def save(self):
        try:
            self.collect()
            atomic_json(self.path, self.data)
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

    def closeEvent(self, event):
        if self.save():
            event.accept()
        else:
            event.ignore()
