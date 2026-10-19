import re
import bisect
import numpy as np
from PySide6.QtCore import Qt, QRectF, Signal, QTimer, QPoint, QLineF
from PySide6.QtWidgets import QWidget, QScrollArea
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetrics, QPixmap,
    QWheelEvent, QMouseEvent, QCursor
)
from .utils import clean_sub_text

TILE_WIDTH = 2000
ARABIC_CHAR_PATTERN = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]')

class WaveformWidget(QWidget):
    subtitle_changed = Signal(int)
    subtitle_selected = Signal(int)
    seek_requested = Signal(float)
    wheel_seek_requested = Signal(float)
    zoom_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        # إبقاء الحاوية الرسومية LTR دائماً لضمان تناسق التايملاين مع تقدم الثواني
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setMinimumHeight(220)
        self.pixels_per_sec = 80.0
        self.audio_peaks = np.empty(0, dtype=np.float32)
        self.duration_sec = 0.0

        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        self.subtitles = []
        self.sub_lanes = []
        self.sub_starts = []
        self.sub_ends = []
        self._sub_cleaned_texts = []
        self._cached_geometries = []

        self.selected_sub_idx = -1
        self.drag_start_x = 0
        self.current_drag_delta_ms = 0
        self.current_time_sec = 0.0

        self.is_dragging_sub = False
        self.is_resizing_left = False
        self.is_resizing_right = False
        self.is_scrubbing_playhead = False
        self.link_mode = "all"
        self.setMouseTracking(True)

        self._pixmap_tiles = {}

        self.base_font = QFont("Segoe UI", 10, QFont.Bold)
        self._fm_cache = {}
        self._visible_elided_cache = {}

        self.auto_scroll_active = False
        self.auto_scroll_viewport_anchor = QPoint()
        self.auto_scroll_viewport_current = QPoint()
        self.mouse_drag_mode = False
        self.sub_pixel_accumulator = 0.0

        self.auto_scroll_timer = QTimer(self)
        self.auto_scroll_timer.setInterval(16)
        self.auto_scroll_timer.timeout.connect(self._handle_auto_scroll)

    def _get_font_metrics(self, pt_size: int):
        if pt_size not in self._fm_cache:
            f = QFont(self.base_font)
            f.setPointSize(max(8, pt_size))
            self._fm_cache[pt_size] = (f, QFontMetrics(f))
        return self._fm_cache[pt_size]

    def _clear_pixmap_cache(self):
        self._pixmap_tiles.clear()

    def _recompute_all_geometries(self):
        h = float(self.height() if self.height() > 50 else 220)
        mid_y = h / 2.0
        n = len(self.subtitles)
        self._cached_geometries = [None] * n

        for idx in range(n):
            st = self.sub_starts[idx]
            en = self.sub_ends[idx]
            overlaps = 0
            for i in range(max(0, idx - 8), min(n, idx + 9)):
                if not (self.sub_ends[i] <= st or self.sub_starts[i] >= en):
                    overlaps += 1

            concurrent_count = max(1, overlaps)
            lane = self.sub_lanes[idx] if idx < len(self.sub_lanes) else 0

            if concurrent_count == 1:
                box_h = (2.0 / 3.0) * h
                y_pos = mid_y - (box_h / 2.0)
                font_pt = 13
            elif concurrent_count == 2:
                box_h = (1.0 / 3.0) * h
                font_pt = 11
                spacing = (h - (box_h * 2.0)) / 3.0
                y_pos = spacing if (lane % 2 == 0) else (h - spacing - box_h)
            else:
                active_slots = min(concurrent_count, 4)
                spacing = 3.0
                box_h = max(28.0, (h - ((active_slots + 1) * spacing)) / float(active_slots))
                font_pt = 9 if box_h < 34.0 else 10
                slot = (lane // 2) % active_slots
                y_pos = (4.0 + slot * (box_h + spacing)) if (lane % 2 == 0) else (h - 4.0 - box_h - (slot * (box_h + spacing)))

            self._cached_geometries[idx] = (y_pos, box_h, font_pt)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._clear_pixmap_cache()
        self._visible_elided_cache.clear()
        self._recompute_all_geometries()
        self.update()

    def set_duration_preload(self, duration: float):
        self.duration_sec = max(0.0, float(duration))
        total_peaks_estimate = int(self.duration_sec * 100)
        self.audio_peaks = np.zeros(total_peaks_estimate, dtype=np.float32)
        self._clear_pixmap_cache()
        self._recalc_width()
        self.update()

    def append_audio_peaks(self, chunk: np.ndarray, start_idx: int, total_estimate: int):
        if len(self.audio_peaks) == 0:
            sz = max(total_estimate, start_idx + len(chunk))
            self.audio_peaks = np.zeros(sz, dtype=np.float32)

        end_idx = start_idx + len(chunk)
        if end_idx > len(self.audio_peaks):
            pad = np.zeros(end_idx - len(self.audio_peaks), dtype=np.float32)
            self.audio_peaks = np.concatenate([self.audio_peaks, pad])

        self.audio_peaks[start_idx:end_idx] = chunk

        if self.pixels_per_sec > 0:
            start_px = int((start_idx / 100.0) * self.pixels_per_sec)
            end_px = int((end_idx / 100.0) * self.pixels_per_sec)
            first_tile = start_px // TILE_WIDTH
            last_tile = end_px // TILE_WIDTH
            for t_idx in range(first_tile, last_tile + 1):
                self._pixmap_tiles.pop(t_idx, None)

        self.update()

    def finalize_audio_peaks(self, actual_duration: float):
        self.duration_sec = max(0.0, float(actual_duration))
        self._clear_pixmap_cache()
        self._recalc_width()
        self.update()

    def _recalc_width(self):
        total_width = int(self.duration_sec * self.pixels_per_sec) + 400
        self.setFixedWidth(max(total_width, 800))

    def set_zoom(self, px_per_sec: float):
        new_zoom = max(30.0, min(250.0, px_per_sec))
        if abs(new_zoom - self.pixels_per_sec) > 0.01:
            self.pixels_per_sec = new_zoom
            self._clear_pixmap_cache()
            self._visible_elided_cache.clear()
            self._recalc_width()
            self.update()

    def set_subtitles_data(self, subs, lanes, starts, ends):
        self.subtitles = subs
        self.sub_lanes = lanes
        self.sub_starts = starts
        self.sub_ends = ends
        self._sub_cleaned_texts = [clean_sub_text(s.text) for s in subs]
        self._visible_elided_cache.clear()
        self.selected_sub_idx = -1
        self._recompute_all_geometries()
        self.update()

    def set_current_time(self, current_sec: float):
        self.current_time_sec = current_sec
        self.update()

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            self.set_zoom(self.pixels_per_sec + (10.0 if delta > 0 else -10.0))
            self.zoom_changed.emit(self.pixels_per_sec)
            event.accept()
        else:
            delta_y = event.angleDelta().y()
            if delta_y != 0:
                self.wheel_seek_requested.emit(0.5 if delta_y > 0 else -0.5)
                event.accept()
            else:
                super().wheelEvent(event)

    def _get_scroll_area(self):
        w = self.parentWidget()
        while w and not isinstance(w, QScrollArea):
            w = w.parentWidget()
        return w

    def _map_to_viewport(self, local_pt: QPoint) -> QPoint:
        sa = self._get_scroll_area()
        if sa:
            return self.mapTo(sa.viewport(), local_pt)
        return local_pt

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MiddleButton:
            if self.auto_scroll_active:
                self._stop_auto_scroll()
            else:
                vp_pt = self._map_to_viewport(event.position().toPoint())
                self._start_auto_scroll(vp_pt, is_drag=True)
            event.accept()
            return
        elif self.auto_scroll_active:
            self._stop_auto_scroll()
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            click_x, click_y = event.position().x(), event.position().y()
            self.selected_sub_idx = -1
            self.is_resizing_left = self.is_resizing_right = self.is_dragging_sub = False
            self.current_drag_delta_ms = 0
            edge_threshold = 8

            if self.sub_starts:
                cur_click_ms = int((click_x / self.pixels_per_sec) * 1000.0)
                search_start = max(0, bisect.bisect_left(self.sub_starts, cur_click_ms - 30000))
                search_end = min(len(self.subtitles), bisect.bisect_right(self.sub_starts, cur_click_ms + 30000))

                for idx in range(search_start, search_end):
                    sub = self.subtitles[idx]
                    start_x = (sub.start / 1000.0) * self.pixels_per_sec
                    end_x = (sub.end / 1000.0) * self.pixels_per_sec
                    w = max(24.0, end_x - start_x)
                    end_x = start_x + w

                    if idx < len(self._cached_geometries) and self._cached_geometries[idx]:
                        y_pos, box_h, _ = self._cached_geometries[idx]
                    else:
                        y_pos, box_h = 8.0, 32.0

                    if (start_x - edge_threshold) <= click_x <= (end_x + edge_threshold) and y_pos <= click_y <= (y_pos + box_h):
                        self.selected_sub_idx = idx
                        self.drag_start_x = click_x
                        if abs(click_x - start_x) <= edge_threshold:
                            self.is_resizing_left = True
                        elif abs(click_x - end_x) <= edge_threshold:
                            self.is_resizing_right = True
                        else:
                            self.is_dragging_sub = True
                        self.subtitle_selected.emit(idx)
                        break

            if self.selected_sub_idx == -1:
                self.is_scrubbing_playhead = True
                max_sec = self.duration_sec if self.duration_sec > 0 else (self.width() / self.pixels_per_sec)
                target_sec = max(0.0, min(max_sec, click_x / self.pixels_per_sec))
                self.set_current_time(target_sec)
                self.seek_requested.emit(target_sec)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MiddleButton and self.auto_scroll_active:
            vp_cur = self._map_to_viewport(event.position().toPoint())
            if (vp_cur - self.auto_scroll_viewport_anchor).manhattanLength() > 8:
                self._stop_auto_scroll()
            else:
                self.mouse_drag_mode = False
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            delta_ms = self.current_drag_delta_ms
            if (self.is_dragging_sub or self.is_resizing_left or self.is_resizing_right) and delta_ms != 0:
                self.subtitle_changed.emit(delta_ms)
            self.current_drag_delta_ms = 0
            self.is_dragging_sub = self.is_resizing_left = self.is_resizing_right = self.is_scrubbing_playhead = False
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.auto_scroll_active:
            self.auto_scroll_viewport_current = self._map_to_viewport(event.position().toPoint())
            self._update_directional_cursor()
            self.update()
            event.accept()
            return

        cur_x = event.position().x()
        if (self.is_dragging_sub or self.is_resizing_left or self.is_resizing_right) and self.selected_sub_idx != -1:
            self.current_drag_delta_ms = int(((cur_x - self.drag_start_x) / self.pixels_per_sec) * 1000.0)
            self.update()
        elif self.is_scrubbing_playhead and (event.buttons() & Qt.LeftButton):
            max_sec = self.duration_sec if self.duration_sec > 0 else (self.width() / self.pixels_per_sec)
            target_sec = max(0.0, min(max_sec, cur_x / self.pixels_per_sec))
            self.set_current_time(target_sec)
            self.seek_requested.emit(target_sec)

    def _start_auto_scroll(self, vp_pos: QPoint, is_drag: bool = False):
        self.auto_scroll_active = True
        self.mouse_drag_mode = is_drag
        self.auto_scroll_viewport_anchor = vp_pos
        self.auto_scroll_viewport_current = vp_pos
        self.sub_pixel_accumulator = 0.0
        self.setCursor(Qt.SizeHorCursor)
        self.auto_scroll_timer.start()
        self.update()

    def _stop_auto_scroll(self):
        self.auto_scroll_active = False
        self.mouse_drag_mode = False
        self.auto_scroll_timer.stop()
        self.sub_pixel_accumulator = 0.0
        self.unsetCursor()
        self.update()

    def _update_directional_cursor(self):
        self.setCursor(Qt.SizeHorCursor)

    def _handle_auto_scroll(self):
        if not self.auto_scroll_active: return
        scroll_area = self._get_scroll_area()
        if not scroll_area: return

        delta_x = self.auto_scroll_viewport_current.x() - self.auto_scroll_viewport_anchor.x()
        deadzone = 12

        if abs(delta_x) > deadzone:
            sign = 1.0 if delta_x > 0 else -1.0
            distance = abs(delta_x) - deadzone
            speed = sign * (distance * 0.15 + (distance ** 1.3) * 0.008)
            self.sub_pixel_accumulator += speed
            step = int(self.sub_pixel_accumulator)
            if step != 0:
                self.sub_pixel_accumulator -= step
                h_bar = scroll_area.horizontalScrollBar()
                h_bar.setValue(h_bar.value() + step)
        else:
            self.sub_pixel_accumulator = 0.0

        self.update()

    def _render_waveform_tile(self, tile_idx: int, tile_h: int) -> QPixmap:
        pixmap = QPixmap(TILE_WIDTH, tile_h)
        pixmap.fill(QColor(15, 15, 20))

        if len(self.audio_peaks) == 0 or self.duration_sec <= 0:
            return pixmap

        p = QPainter(pixmap)
        mid_y = tile_h / 2.0
        p.setPen(QPen(QColor(255, 255, 255, 20), 1))
        p.drawLine(0, int(mid_y), TILE_WIDTH, int(mid_y))

        total_samples = len(self.audio_peaks)
        sec_to_sample = total_samples / self.duration_sec
        inv_px = 1.0 / self.pixels_per_sec
        max_wave_height = mid_y - 10

        tile_start_x = tile_idx * TILE_WIDTH
        step = 1 if self.pixels_per_sec >= 80 else 2

        lines = []
        for lx in range(0, TILE_WIDTH, step):
            gx = tile_start_x + lx
            sec = gx * inv_px
            sample_idx = int(sec * sec_to_sample)
            if sample_idx < total_samples:
                val = self.audio_peaks[sample_idx]
                if val > 0.025:
                    bar_h = val * max_wave_height
                    lines.append(QLineF(lx, mid_y - bar_h, lx, mid_y + bar_h))

        if lines:
            p.setPen(QPen(QColor(0, 180, 230, 210), 1))
            p.drawLines(lines)

        p.end()
        return pixmap

    def paintEvent(self, event):
        rect = event.rect()
        visible_left = rect.left()
        visible_right = rect.right()
        w_viewport = rect.width()
        h_viewport = rect.height()
        h = float(self.height())

        if w_viewport <= 0 or h_viewport <= 0:
            return

        buffer = QPixmap(w_viewport, h_viewport)
        buffer.fill(QColor(15, 15, 20))

        p = QPainter(buffer)
        p.translate(-visible_left, -rect.top())

        first_tile = max(0, visible_left // TILE_WIDTH)
        last_tile = max(0, visible_right // TILE_WIDTH)

        # 1. رسم شرائح الـ Waveform
        for t_idx in range(first_tile, last_tile + 1):
            tile_x = t_idx * TILE_WIDTH
            if t_idx not in self._pixmap_tiles or self._pixmap_tiles[t_idx].height() != int(h):
                self._pixmap_tiles[t_idx] = self._render_waveform_tile(t_idx, int(h))
            p.drawPixmap(tile_x, 0, self._pixmap_tiles[t_idx])

        # 2. رسم صناديق ونصوص الترجمة مع دعم ذكي لاتجاه النص (RTL/LTR Aware)
        if self.sub_starts and len(self.subtitles) > 0:
            left_ms = int((visible_left / self.pixels_per_sec) * 1000.0) - 20000
            right_ms = int((visible_right / self.pixels_per_sec) * 1000.0) + 20000

            start_idx = max(0, bisect.bisect_left(self.sub_starts, left_ms) - 10)
            end_idx = min(len(self.subtitles), bisect.bisect_right(self.sub_starts, right_ms) + 10)

            delta_ms = self.current_drag_delta_ms if (self.is_dragging_sub or self.is_resizing_left or self.is_resizing_right) else 0
            sel_orig_start = self.sub_starts[self.selected_sub_idx] if self.selected_sub_idx != -1 else 0

            brush_normal = QBrush(QColor(18, 90, 85, 145))
            pen_normal = QPen(QColor(233, 196, 106, 220), 1)
            brush_sel = QBrush(QColor(230, 57, 70, 195))
            pen_sel = QPen(QColor(255, 255, 255, 255), 1.5)
            text_color = QColor(255, 255, 255, 250)

            cur_font_size = -1

            for idx in range(start_idx, end_idx):
                sub = self.subtitles[idx]
                st = sub.start
                en = sub.end

                if delta_ms != 0:
                    if self.is_dragging_sub:
                        if self.link_mode == "all" or (self.link_mode == "following" and sub.start >= sel_orig_start) or idx == self.selected_sub_idx:
                            st += delta_ms
                            en += delta_ms
                    elif idx == self.selected_sub_idx:
                        if self.is_resizing_left:
                            st = max(0, min(en - 100, st + delta_ms))
                        elif self.is_resizing_right:
                            en = max(st + 100, en + delta_ms)

                start_x = max(0.0, (st / 1000.0) * self.pixels_per_sec)
                end_x = max(0.0, (en / 1000.0) * self.pixels_per_sec)
                box_w = max(24.0, end_x - start_x)

                if (start_x + box_w) < visible_left or start_x > visible_right:
                    continue

                if idx < len(self._cached_geometries) and self._cached_geometries[idx]:
                    y_pos, box_h, font_pt = self._cached_geometries[idx]
                else:
                    y_pos, box_h, font_pt = 8.0, 32.0, 10

                rect_box = QRectF(start_x, y_pos, box_w, box_h)

                if idx == self.selected_sub_idx:
                    p.setBrush(brush_sel)
                    p.setPen(pen_sel)
                else:
                    p.setBrush(brush_normal)
                    p.setPen(pen_normal)

                p.drawRoundedRect(rect_box, 4, 4)

                if box_w > 14:
                    if cur_font_size != font_pt:
                        font_obj, fm = self._get_font_metrics(font_pt)
                        p.setFont(font_obj)
                        cur_font_size = font_pt
                    else:
                        _, fm = self._get_font_metrics(font_pt)

                    raw_txt = self._sub_cleaned_texts[idx] if idx < len(self._sub_cleaned_texts) else clean_sub_text(sub.text)
                    if not raw_txt:
                        raw_txt = sub.text.strip()

                    text_inner_rect = rect_box.adjusted(6, 4, -6, -4)
                    line_height = fm.lineSpacing()
                    max_allowed_lines = max(1, int(text_inner_rect.height() / line_height))
                    p.setPen(text_color)

                    # فحص دقيق: إذا كان النص عربياً يحاذى لليمين، وإن كان إنجليزياً يحاذى لليسار
                    is_arabic = bool(ARABIC_CHAR_PATTERN.search(raw_txt))
                    align_flag = (Qt.AlignRight if is_arabic else Qt.AlignLeft) | Qt.AlignVCenter

                    if max_allowed_lines >= 2 and text_inner_rect.width() > 30:
                        p.save()
                        p.setClipRect(text_inner_rect)
                        p.drawText(
                            text_inner_rect,
                            align_flag | Qt.TextWordWrap,
                            raw_txt
                        )
                        p.restore()
                    else:
                        cache_key = (idx, int(box_w), font_pt, is_arabic)
                        elided_txt = self._visible_elided_cache.get(cache_key)
                        if elided_txt is None:
                            elided_txt = fm.elidedText(raw_txt, Qt.ElideRight, int(box_w - 12))
                            if len(self._visible_elided_cache) > 500:
                                self._visible_elided_cache.clear()
                            self._visible_elided_cache[cache_key] = elided_txt

                        p.drawText(
                            text_inner_rect,
                            align_flag | Qt.TextSingleLine,
                            elided_txt
                        )

        # 3. مؤشر الـ Playhead
        playhead_x = self.current_time_sec * self.pixels_per_sec
        p.setPen(QPen(QColor(255, 45, 85), 2))
        p.drawLine(int(playhead_x), 0, int(playhead_x), int(h))

        # 4. علامة السكرول بالزر الأوسط
        if self.auto_scroll_active:
            sa = self._get_scroll_area()
            local_anchor = self.mapFrom(sa.viewport(), self.auto_scroll_viewport_anchor) if sa else self.auto_scroll_viewport_anchor
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(0, 164, 220, 220), 1.5))
            p.setBrush(QBrush(QColor(15, 15, 20, 200)))
            p.drawEllipse(local_anchor, 13, 13)
            p.setBrush(QBrush(QColor(0, 164, 220, 240)))
            p.drawEllipse(local_anchor, 3, 3)

            p.setPen(QPen(QColor(0, 164, 220, 180), 1.5))
            ax, ay = local_anchor.x(), local_anchor.y()
            p.drawLine(ax - 7, ay - 4, ax - 11, ay)
            p.drawLine(ax + 7, ay - 4, ax + 11, ay)
            p.drawLine(ax - 7, ay + 4, ax - 11, ay)
            p.drawLine(ax + 7, ay + 4, ax + 11, ay)

        p.end()

        screen_painter = QPainter(self)
        screen_painter.drawPixmap(rect.topLeft(), buffer)
        screen_painter.end()
