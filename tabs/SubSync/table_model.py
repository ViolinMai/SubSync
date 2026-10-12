import re
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer, QPoint
from PySide6.QtWidgets import QTableView, QHeaderView, QAbstractItemView, QApplication
from PySide6.QtGui import QMouseEvent, QPainter, QColor, QCursor, QPen, QBrush
from .utils import ms_to_timestamp, timestamp_to_ms, clean_sub_text, get_event_font_info

ARABIC_CHAR_PATTERN = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]')

class FastSubtitleTableModel(QAbstractTableModel):
    def __init__(self, subs_events=None, subs_data=None, parent=None):
        super().__init__(parent)
        self.events = subs_events or []
        self.subs_data = subs_data
        self.headers = ["#", "Start", "End", "Duration", "Text"]

    def rowCount(self, parent=QModelIndex()):
        return len(self.events)

    def columnCount(self, parent=QModelIndex()):
        return 5

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.events)):
            return None
        ev = self.events[index.row()]
        col = index.column()

        if role == Qt.DisplayRole or role == Qt.EditRole:
            if col == 0:
                return str(index.row() + 1)
            elif col == 1:
                return ms_to_timestamp(ev.start)
            elif col == 2:
                return ms_to_timestamp(ev.end)
            elif col == 3:
                return f"{max(0.0, (ev.end - ev.start) / 1000.0):.2f}s"
            elif col == 4:
                return clean_sub_text(ev.text) if role == Qt.DisplayRole else ev.text

        elif role == Qt.TextAlignmentRole:
            if col in (0, 1, 2, 3):
                # الحفاظ على اتجاه الأرقام والوقت من اليسار دائماً لمنع تشويه الفواصل
                return int(Qt.AlignCenter)
            elif col == 4:
                txt = clean_sub_text(ev.text)
                if ARABIC_CHAR_PATTERN.search(txt):
                    return int(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    return int(Qt.AlignLeft | Qt.AlignVCenter)

        elif role == Qt.ToolTipRole:
            font_name, is_inst = get_event_font_info(ev, self.subs_data)
            status_text = "Installed ✅" if is_inst else "Missing ❌"
            return f"Font: {font_name} ({status_text})\nStyle: {getattr(ev, 'style', 'Default')}"

        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        if index.column() in (1, 2, 4):
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or role != Qt.EditRole:
            return False
        row, col = index.row(), index.column()
        ev = self.events[row]

        if col == 1:
            ev.start = timestamp_to_ms(str(value))
        elif col == 2:
            ev.end = timestamp_to_ms(str(value))
        elif col == 4:
            ev.text = str(value)
        else:
            return False

        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        return True

    def set_events(self, events, subs_data=None):
        self.beginResetModel()
        self.events = events
        if subs_data is not None:
            self.subs_data = subs_data
        self.endResetModel()

class AutoScrollTableView(QTableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.auto_scroll_active = False
        self.auto_scroll_global_anchor = QPoint()
        self.mouse_drag_mode = False
        self.sub_pixel_accumulator = 0.0

        self.scroll_timer = QTimer(self)
        self.scroll_timer.setInterval(16)
        self.scroll_timer.timeout.connect(self._handle_auto_scroll)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MiddleButton:
            if self.auto_scroll_active:
                self._stop_auto_scroll()
            else:
                self._start_auto_scroll(event.globalPosition().toPoint(), is_drag=True)
            event.accept()
            return
        elif self.auto_scroll_active:
            self._stop_auto_scroll()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MiddleButton and self.auto_scroll_active:
            global_cur = event.globalPosition().toPoint()
            if (global_cur - self.auto_scroll_global_anchor).manhattanLength() > 8:
                self._stop_auto_scroll()
            else:
                self.mouse_drag_mode = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.auto_scroll_active:
            self._update_directional_cursor()
            self.viewport().update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _start_auto_scroll(self, global_pos: QPoint, is_drag: bool = False):
        self.auto_scroll_active = True
        self.mouse_drag_mode = is_drag
        self.auto_scroll_global_anchor = global_pos
        self.sub_pixel_accumulator = 0.0
        self.setCursor(Qt.SizeVerCursor)
        self.scroll_timer.start()
        self.viewport().update()

    def _stop_auto_scroll(self):
        self.auto_scroll_active = False
        self.mouse_drag_mode = False
        self.scroll_timer.stop()
        self.sub_pixel_accumulator = 0.0
        self.unsetCursor()
        self.viewport().update()

    def _update_directional_cursor(self):
        global_cur = QCursor.pos()
        delta_y = global_cur.y() - self.auto_scroll_global_anchor.y()
        if abs(delta_y) <= 12:
            self.setCursor(Qt.SizeVerCursor)
        elif delta_y < 0:
            self.setCursor(Qt.UpArrowCursor)
        else:
            self.setCursor(Qt.DownArrowCursor)

    def _handle_auto_scroll(self):
        if not self.auto_scroll_active: return
        global_cur = QCursor.pos()
        delta_y = global_cur.y() - self.auto_scroll_global_anchor.y()
        deadzone = 12

        if abs(delta_y) > deadzone:
            sign = 1.0 if delta_y > 0 else -1.0
            distance = abs(delta_y) - deadzone
            speed = sign * (distance * 0.12 + (distance ** 1.3) * 0.006)
            self.sub_pixel_accumulator += speed
            step = int(self.sub_pixel_accumulator)
            if step != 0:
                self.sub_pixel_accumulator -= step
                sb = self.verticalScrollBar()
                sb.setValue(sb.value() + step)
        else:
            self.sub_pixel_accumulator = 0.0

        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.auto_scroll_active:
            local_anchor = self.viewport().mapFromGlobal(self.auto_scroll_global_anchor)
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor(0, 164, 220, 220), 1.5))
            painter.setBrush(QBrush(QColor(15, 15, 20, 200)))
            painter.drawEllipse(local_anchor, 13, 13)
            painter.setBrush(QBrush(QColor(0, 164, 220, 240)))
            painter.drawEllipse(local_anchor, 3, 3)

            painter.setPen(QPen(QColor(0, 164, 220, 180), 1.5))
            ax, ay = local_anchor.x(), local_anchor.y()
            painter.drawLine(ax - 4, ay - 7, ax, ay - 11)
            painter.drawLine(ax + 4, ay - 7, ax, ay - 11)
            painter.drawLine(ax - 4, ay + 7, ax, ay + 11)
            painter.drawLine(ax + 4, ay + 7, ax, ay + 11)
            painter.end()
