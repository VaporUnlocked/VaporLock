"""Visual styling for the native desktop interface."""

STYLESHEET = """
QWidget { font-family: 'Segoe UI'; font-size: 13px; color: #202723; }
QMainWindow, QWidget#desktopRoot, QWidget#settingsBody { background: #f3f2eb; }
QWidget#masthead { background: #fffef8; border-bottom: 1px solid #d8d9cf; }
QLabel#brand { font-size: 20px; font-weight: 750; color: #232d25; }
QLabel#brandMark { background: #24332b; color: #f8f6e9; font-size: 18px;
    font-weight: 800; padding: 4px 9px; }
QLabel#pageTitle { font-size: 37px; font-weight: 750; color: #253027; }
QLabel#sectionTitle { font-size: 17px; font-weight: 700; }
QLabel#muted, QLabel#metricName { color: #535c53; }
QLabel#metricValue { font-size: 25px; font-weight: 700; }
QLabel#stateChip { background: #e9ebe1; color: #3d5143; padding: 6px 10px; font-weight: 700; }
QLabel#preview { background: #202a25; color: #e5e9db; font-size: 15px; }
QFrame#panel { background: #fffef8; border: 1px solid #d8d9cf; }
QFrame#metric { background: #e8ebe0; border: 1px solid #d3dacb; }
QLabel#fieldLabel { font-weight: 600; }
QPushButton, QToolButton { background: #fffef8; border: 1px solid #bdc4b8;
    padding: 7px 12px; min-height: 20px; }
QPushButton:hover, QToolButton:hover { background: #e8ebe0; border-color: #859586; }
QPushButton:disabled { background: #eeeee7; color: #767d73; border-color: #d9dcd2; }
QPushButton#primary { background: #d95428; color: #fff; border-color: #bf4720; font-weight: 700; }
QPushButton#primary:hover { background: #bd431e; }
QPushButton#primary:disabled { background: #ead2c8; border-color: #dac0b4; color: #774d3c; }
QPushButton#nav { background: transparent; border: 1px solid transparent; padding: 7px 12px; }
QPushButton#nav:hover { background: #efefe7; }
QPushButton#nav:checked { background: #e7ecdf; color: #253b2b; font-weight: 700; }
QPushButton:focus, QToolButton:focus, QCheckBox:focus, QLineEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus { border: 2px solid #516e55; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTableWidget {
    background: #fffef8; border: 1px solid #bdc4b8; padding: 6px 8px; selection-background-color: #dbe8d7;
    selection-color: #202723; }
QComboBox QAbstractItemView { background: #fffef8; selection-background-color: #dbe8d7;
    selection-color: #202723; }
QCheckBox { spacing: 7px; padding: 4px; border: 1px solid transparent; }
QScrollArea { background: transparent; border: 0; }
QScrollBar:vertical { background: #e9ebe2; width: 10px; }
QScrollBar::handle:vertical { background: #a4b19f; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 1px solid #d8d9cf; }
QTabBar::tab { background: #e7ecdf; padding: 8px 15px; }
QTabBar::tab:selected { background: #fffef8; border-bottom: 2px solid #d95428; }
QHeaderView::section { background: #e7ecdf; border: 0; padding: 6px; font-weight: 650; }
QStatusBar { background: #fffef8; border-top: 1px solid #d8d9cf; }
"""
