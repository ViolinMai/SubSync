from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout

class EmptyToolTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(15)

        lbl_title = QLabel("🚀 الأداة القادمة / Custom Tool Workspace")
        lbl_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #00a4dc;")
        layout.addWidget(lbl_title)

        lbl_desc = QLabel("هذا الملف مفصول وجاهز لاستقبال كود أي برنامج ترغب بإضافته مستقبلاً.")
        lbl_desc.setStyleSheet("color: #888;")
        layout.addWidget(lbl_desc)

        btn_test = QPushButton("زر اختبار للأداة")
        btn_test.setMinimumWidth(140)
        layout.addWidget(btn_test)

        layout.addStretch()
