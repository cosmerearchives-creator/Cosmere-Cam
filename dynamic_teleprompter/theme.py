"""Shared suite design system, including application-created dialogs and menus.

Presentation pixels are painted explicitly by CanvasScene and never take palette
colors from these controls. Vendor plugin windows belong to a separate process.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QFileDialog, QColorDialog, QFontDialog

BACKGROUND = '#F7F5F0'
CHARCOAL = '#29262F'
LAVENDER = '#EDE3FA'
ACCENT = '#6E5C87'

STYLESHEET = '''
QWidget { color: #29262F; }
QMainWindow, QDialog, QMessageBox { background: #F7F5F0; }
QWidget#suiteRoot, QTabWidget > QWidget, QScrollArea > QWidget > QWidget { background: #F7F5F0; }
QLabel { background: transparent; }
QLabel#suiteTitle { font-size: 21pt; font-weight: 700; padding: 4px 0px 12px; }
QLabel#sectionTitle { font-size: 15pt; font-weight: 600; padding: 6px 0px; }
QLabel#mutedText { color: #736A7C; }
QPushButton, QToolButton { background: #EDE3FA; border: 1px solid #DFD3EE;
    border-radius: 16px; padding: 9px 16px; min-height: 20px; font-weight: 600; }
QPushButton:hover, QToolButton:hover { background: #DDD0EF; border-color: #B8A5CC; }
QPushButton:pressed, QToolButton:pressed { background: #C9B5E0; }
QPushButton:focus, QToolButton:focus { border: 2px solid #6E5C87; }
QPushButton:checked, QToolButton:checked { background: #6E5C87; color: white; }
QPushButton:disabled, QToolButton:disabled { background: #E5E2DE; color: #9C969E; border-color: #DDD9D5; }
QPushButton[role="primary"] { background: #6E5C87; color: white; border-color: #6E5C87; }
QPushButton[role="primary"]:hover { background: #59456F; }
QPushButton[role="danger"] { background: #F6E2E3; color: #8B3443; border-color: #EAC8CF; }
QPushButton[role="danger"]:hover { background: #EDCDD4; }
QPushButton[role="primary"]:disabled, QPushButton[role="danger"]:disabled {
    background: #E5E2DE; color: #9C969E; border-color: #DDD9D5; }
QPushButton#iconButton { padding: 0; border-radius: 20px; min-height: 0; }
QTabWidget::pane { border: 1px solid #DDD5E3; border-radius: 16px; background: #F7F5F0; padding: 8px; }
QTabBar::tab { background: #E9E4EC; color: #6B6174; border: 0;
    border-radius: 12px; padding: 10px 16px; margin: 0px 4px 8px 0px; }
QTabBar::tab:selected { background: #6E5C87; color: white; }
QTabBar::tab:hover:!selected { background: #EDE3FA; color: #29262F; }
QGroupBox { background: #FFFFFF; border: 1px solid #DFD8E5; border-radius: 14px;
    margin-top: 18px; padding: 18px 12px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0px 6px; color: #6E5C87; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox,
QListWidget, QTreeWidget, QTableWidget { background: #FFFFFF; color: #29262F;
    border: 1px solid #D9D1E2; border-radius: 9px; padding: 7px;
    selection-background-color: #D8C6ED; selection-color: #29262F; }
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus { border-color: #8E72AD; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled { background: #EBE8E5; color: #99929D; }
QListWidget::item { padding: 7px; border-radius: 6px; }
QListWidget::item:selected { background: #EDE3FA; color: #29262F; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox QAbstractItemView { background: white; color: #29262F; selection-background-color: #EDE3FA; }
QCheckBox, QRadioButton { spacing: 8px; padding: 3px; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #AA99BB; border-radius: 5px; background: white; }
QCheckBox::indicator:checked { background: #6E5C87; border: 3px solid #BFA8D7; }
QCheckBox::indicator:disabled { background: #DAD5DF; border-color: #CFC9D6; }
QSlider::groove:horizontal { height: 5px; background: #E3DCE9; border-radius: 2px; }
QSlider::handle:horizontal { width: 17px; margin: -6px 0; border-radius: 8px; background: #6E5C87; }
QSlider::sub-page:horizontal { background: #BAA2D2; }
QProgressBar { background: #E7E1EC; border: 0; border-radius: 7px; text-align: center; min-height: 12px; }
QProgressBar::chunk { background: #9C81B8; border-radius: 7px; }
QMenuBar { background: #F7F5F0; padding: 4px; border-bottom: 2px solid #C5B7D4; }
QMenuBar::item { padding: 7px 12px; border-radius: 7px; }
QMenuBar::item:selected { background: #EDE3FA; }
QMenu { background: #FFFFFF; border: 1px solid #D9D0E2; padding: 6px; }
QMenu::item { padding: 9px 24px 9px 14px; border-radius: 6px; }
QMenu::item:selected { background: #EDE3FA; color: #29262F; }
QMenu::item:disabled { color: #A6A0AB; }
QMenu::separator { height: 1px; background: #E7E0EC; margin: 5px 8px; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { width: 12px; margin: 2px; background: #EEE9F2; border-radius: 5px; }
QScrollBar::handle:vertical { background: #C0AFD0; min-height: 28px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { background: #29262F; color: #F7F5F0; border: 1px solid #6E5C87; padding: 6px; }
QSplitter::handle { background: #DFD5E8; }
QStatusBar { background: #EDE7F3; color: #6E5C87; }
QTextEdit#readerText { background: #29262F; color: #F7F5F0; border: none; border-radius: 20px; padding: 22px; }
QTextEdit#scriptEditor { background: #29262F; color: #F7F5F0; border: none; border-radius: 18px; padding: 16px; }
QLabel#readerStatus { color: #6E5C87; font-size: 12pt; font-weight: 600; padding: 6px; }
'''


def apply_theme(application):
    application.setStyle('Fusion')
    application.setFont(QFont('Segoe UI', 10))
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, BACKGROUND),
                        (QPalette.ColorRole.Base, '#FFFFFF'),
                        (QPalette.ColorRole.WindowText, CHARCOAL),
                        (QPalette.ColorRole.Text, CHARCOAL),
                        (QPalette.ColorRole.Button, LAVENDER),
                        (QPalette.ColorRole.ButtonText, CHARCOAL),
                        (QPalette.ColorRole.Highlight, '#D8C6ED'),
                        (QPalette.ColorRole.HighlightedText, CHARCOAL)):
        palette.setColor(role, QColor(color))
    application.setPalette(palette)
    application.setStyleSheet(STYLESHEET)
    # Native OS dialogs cannot use an application's palette. Use Qt dialogs for
    # the same accessible controls, colors and spacing across the whole suite.
    application.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
