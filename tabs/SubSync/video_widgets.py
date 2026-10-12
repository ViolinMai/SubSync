import ctypes
from PySide6.QtCore import Qt, Signal, QObject, QEvent, QTimer, QByteArray
from PySide6.QtWidgets import QWidget, QApplication, QLineEdit, QTextEdit
from PySide6.QtGui import QMouseEvent, QResizeEvent, QPainter, QColor, QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget

# توقيع C الدقيق لـ libmpv get_proc_address
GET_PROC_ADDR_TYPE = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)

class VideoSurfaceWidget(QWidget):
    clicked = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setStyleSheet("background-color: #000000;")
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, False)
        self.setAutoFillBackground(True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

class VideoSurfaceGLWidget(QOpenGLWidget):
    """
    سطح فيديو لبيئة Wayland الأصلية عبر libmpv Render API (opengl).
    لا يؤثر على بيئات Windows أو X11 إطلاقاً.
    """
    clicked = Signal()
    frame_ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.PartialUpdate)
        self.mpv_player = None
        self._render_ctx = None
        self._c_get_proc_address = None
        self.frame_ready.connect(self.update)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def attach_player(self, mpv_player):
        self.mpv_player = mpv_player

    @staticmethod
    def _py_get_proc_address(_ctx, name):
        glctx = QOpenGLContext.currentContext()
        if glctx is None or not name:
            return None
        addr = glctx.getProcAddress(QByteArray(name))
        return int(addr) if addr else None

    def initializeGL(self):
        if self.mpv_player is None or self._render_ctx is not None:
            return
        import mpv as mpv_module

        # تغليف الدالة بـ CFUNCTYPE والاحتفاظ بمرجع لها لمنع الـ Garbage Collection
        self._c_get_proc_address = GET_PROC_ADDR_TYPE(self._py_get_proc_address)

        self._render_ctx = mpv_module.MpvRenderContext(
            self.mpv_player,
            "opengl",
            opengl_init_params={"get_proc_address": self._c_get_proc_address},
        )
        self._render_ctx.update_cb = lambda: self.frame_ready.emit()

    def paintGL(self):
        if self._render_ctx is None:
            return
        factor = self.devicePixelRatioF()
        w = max(1, round(self.width() * factor))
        h = max(1, round(self.height() * factor))
        self._render_ctx.render(
            flip_y=True,
            opengl_fbo={"w": w, "h": h, "fbo": int(self.defaultFramebufferObject())},
        )

    def release_render_context(self):
        if self._render_ctx is None:
            return
        try:
            self._render_ctx.free()
        except Exception:
            pass
        self._render_ctx = None
        self._c_get_proc_address = None

class AspectRatioContainer(QWidget):
    def __init__(self, child_widget, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.child_widget = child_widget
        self.child_widget.setParent(self)
        self.setStyleSheet("background-color: #000000;")
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(True)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(16)
        self._resize_timer.timeout.connect(self._apply_geometry)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        painter.end()

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self._resize_timer.start()

    def _apply_geometry(self):
        target_ratio = 16.0 / 9.0
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        if w / max(1, h) > target_ratio:
            new_h = h
            new_w = int(h * target_ratio)
        else:
            new_w = w
            new_h = int(w / target_ratio)
        x = (w - new_w) // 2
        y = (h - new_h) // 2
        self.child_widget.setGeometry(x, y, new_w, new_h)

class GlobalKeyFilter(QObject):
    def __init__(self, toggle_callback, seek_relative_callback):
        super().__init__()
        self.toggle_callback = toggle_callback
        self.seek_relative_callback = seek_relative_callback

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            focus_widget = QApplication.focusWidget()
            if isinstance(focus_widget, (QLineEdit, QTextEdit)):
                return False
            if event.key() == Qt.Key_Space:
                self.toggle_callback()
                return True
            elif event.key() == Qt.Key_Right:
                self.seek_relative_callback(1.0)
                return True
            elif event.key() == Qt.Key_Left:
                self.seek_relative_callback(-1.0)
                return True
        return super().eventFilter(obj, event)
