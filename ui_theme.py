APP_STYLESHEET = """
QWidget {
    background-color: #0f0f14;
    color: #ffffff;
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}
QGroupBox {
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 14px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: #00a4dc;
}
QLineEdit, QSpinBox {
    background-color: rgba(25, 25, 30, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 6px;
    padding: 4px 8px;
    color: #ffffff;
}
QLineEdit:focus, QSpinBox:focus {
    border: 1px solid #00a4dc;
}
QComboBox {
    background-color: rgba(25, 25, 30, 0.85);
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 6px;
    padding: 3px 6px;
    color: #ffffff;
    font-weight: normal;
}
QComboBox:hover {
    border-color: #00a4dc;
}
QComboBox QAbstractItemView {
    background-color: #141418;
    color: #ffffff;
    selection-background-color: #00a4dc;
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 6px;
    padding: 4px;
}
QPushButton {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 6px;
    padding: 4px 8px;
    font-weight: bold;
    color: #ffffff;
}
QPushButton:hover {
    background-color: rgba(255, 255, 255, 0.18);
    border-color: rgba(255, 255, 255, 0.35);
}
QPushButton:pressed {
    background-color: rgba(255, 255, 255, 0.05);
}
QTabWidget::pane {
    border: 1px solid rgba(255, 255, 255, 0.08);
    background: #0f0f14;
    border-radius: 8px;
    top: -1px;
}
QTabBar::tab {
    background: #141418;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-bottom: none;
    padding: 8px 16px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    color: #8e9297;
    font-weight: 700;
}
QTabBar::tab:selected {
    background: #0f0f14;
    border-top: 2px solid #00a4dc;
    color: #ffffff;
}
QTabBar::tab:hover:!selected {
    background: #1a1a22;
    color: #ddd;
}
QCheckBox, QRadioButton {
    spacing: 6px;
    font-weight: normal;
    color: #ffffff;
}
QProgressBar {
    background-color: rgba(25, 25, 30, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 6px;
    text-align: center;
}
QProgressBar::chunk {
    background-color: #00a4dc;
    border-radius: 5px;
}
QTableView, QTableWidget {
    background-color: rgba(25, 25, 30, 0.6);
    alternate-background-color: rgba(18, 18, 24, 0.6);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 6px;
    gridline-color: rgba(255, 255, 255, 0.05);
}
QTableView::item:selected, QTableWidget::item:selected {
    background-color: #00a4dc;
    color: white;
}
QHeaderView::section {
    background-color: rgba(30, 32, 40, 0.9);
    padding: 6px;
    border: 1px solid rgba(255, 255, 255, 0.06);
    font-weight: bold;
    color: #00a4dc;
}
QScrollArea {
    border: none;
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 8px;
}
QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 0.2);
    border-radius: 4px;
    min-height: 25px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(255, 255, 255, 0.4);
}
QScrollBar:horizontal {
    background: transparent;
    height: 8px;
}
QScrollBar::handle:horizontal {
    background: rgba(255, 255, 255, 0.2);
    border-radius: 4px;
    min-width: 25px;
}
QScrollBar::handle:horizontal:hover {
    background: rgba(255, 255, 255, 0.4);
}
"""
