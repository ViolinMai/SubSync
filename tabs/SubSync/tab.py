import os
import sys
from pathlib import Path
import json
import subprocess
import bisect

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ["PATH"] = PROJECT_ROOT + os.pathsep + os.environ.get("PATH", "")
if sys.platform == 'win32' and hasattr(os, "add_dll_directory"):
    try:
        os.add_dll_directory(PROJECT_ROOT)
    except Exception:
        pass
    for candidate in [Path(PROJECT_ROOT) / "installer_output", Path(PROJECT_ROOT) / "libs"]:
        if candidate.exists():
            try:
                os.add_dll_directory(str(candidate))
                os.environ["PATH"] = str(candidate) + os.pathsep + os.environ["PATH"]
            except Exception:
                pass

import tempfile
import locale
import ctypes
import logging
import pysubs2
import mpv
import numpy as np

from PySide6.QtCore import Qt, Signal, QTimer, QEvent
from PySide6.QtGui import QKeySequence, QShortcut, QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QFileDialog, QMessageBox,
    QScrollArea, QSplitter, QCheckBox, QHeaderView, QAbstractItemView,
    QMenu
)

from config_manager import load_config, save_config
from i18n import t

from .utils import (
    clean_sub_text, get_event_font_info, get_embedded_subtitle_tracks,
    extract_embedded_subtitle, ms_to_timestamp
)
from .workers import SubtitleLoaderWorker, FastAudioWorker, MediaProbeWorker
from .video_widgets import VideoSurfaceWidget, VideoSurfaceGLWidget, AspectRatioContainer, GlobalKeyFilter
from .table_model import FastSubtitleTableModel, AutoScrollTableView
from .waveform_widget import WaveformWidget
from .dialogs import SubtitleInfoDialog, BatchTimingDialog

logger = logging.getLogger("SubSyncTab")
IS_WINDOWS = sys.platform == 'win32'
IS_LINUX = sys.platform.startswith('linux')

class TimeHUDLabel(QLabel):
    """مؤشر زمني مستقل عائم يثبت في أعلى يمين نافذة التمرير ولا يعاد رسمه مع الموجة."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFont(QFont("Consolas", 11, QFont.Bold))
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("""
            QLabel {
                background-color: rgba(15, 18, 24, 0.88);
                color: #00d2ff;
                border: 1px solid rgba(0, 164, 220, 0.45);
                border-radius: 6px;
                padding: 4px 12px;
            }
        """)
        self.setFixedHeight(30)
        self.setText("⏱ 00:00:00.000 / 00:00:00.000")
        self.adjustSize()

class SubSyncTab(QWidget):
    switch_to_merger_requested = Signal(str, str)
    global_progress_updated = Signal(int, int)

    @staticmethod
    def _is_native_wayland_session() -> bool:
        if os.environ.get("QT_QPA_PLATFORM", "").startswith("xcb"):
            return False
        return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" \
            or bool(os.environ.get("WAYLAND_DISPLAY"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

        self.video_path = None
        self.sub_path = None
        self.subs_data = None
        self.worker = None
        self.probe_worker = None
        self.sub_loader_worker = None
        self.temp_sub_path = None

        self.audio_cache = {}
        self.current_audio_stream_idx = 0
        self.video_duration = 0.0
        self._last_highlighted_row = -1

        self.undo_stack = []
        self.redo_stack = []
        self.last_applied_shift_ms = 0
        self.is_dirty = False
        self._batch_dialog = None
        self._calibrating_batch_dlg = None
        self._calibration_accumulated_ms = 0
        self._is_video_fullscreen = False
        self._manual_list_override = None

        self.target_seek_time = None
        self.seek_timer = QTimer(self)
        self.seek_timer.setSingleShot(True)
        self.seek_timer.setInterval(20)
        self.seek_timer.timeout.connect(self._execute_queued_seek)

        self.live_sub_sync_timer = QTimer(self)
        self.live_sub_sync_timer.setSingleShot(True)
        self.live_sub_sync_timer.setInterval(60)
        self.live_sub_sync_timer.timeout.connect(self._do_sync_subtitles_to_player)

        self._init_ui()
        self._init_mpv()
        self._init_shortcuts()

        self.key_filter = GlobalKeyFilter(
            self._toggle_playback,
            self._relative_seek,
            self.toggle_video_fullscreen,
            self._exit_video_fullscreen_if_active
        )
        QApplication.instance().installEventFilter(self.key_filter)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        self.calibration_banner = QWidget(self)
        self.calibration_banner.setStyleSheet("""
            QWidget {
                background-color: rgba(39, 174, 96, 0.18);
                border: 1px solid #2ecc71;
                border-radius: 6px;
            }
        """)
        cal_layout = QHBoxLayout(self.calibration_banner)
        cal_layout.setContentsMargins(10, 6, 10, 6)
        self.lbl_cal_info = QLabel("🎯 Calibrating for Batch — Drag subtitles on waveform to set offset (0 ms)")
        self.lbl_cal_info.setStyleSheet("color: #2ecc71; font-weight: bold; border: none; background: transparent;")
        btn_finish_cal = QPushButton("✅ Apply Offset & Return to Batch")
        btn_finish_cal.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 4px 12px; border: none;")
        btn_finish_cal.clicked.connect(self._finish_batch_calibration)
        cal_layout.addWidget(self.lbl_cal_info)
        cal_layout.addStretch()
        cal_layout.addWidget(btn_finish_cal)
        self.calibration_banner.hide()
        main_layout.addWidget(self.calibration_banner)

        self.main_v_splitter = QSplitter(Qt.Vertical)
        self.main_v_splitter.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        main_layout.addWidget(self.main_v_splitter, 1)

        self.top_h_splitter = QSplitter(Qt.Horizontal)
        self.top_h_splitter.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

        self.table_container = QWidget()
        self.table_container.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        table_layout = QVBoxLayout(self.table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(4)

        tbl_header_layout = QHBoxLayout()
        tbl_title = QLabel(t("sub_list"))
        tbl_title.setStyleSheet("font-weight: bold; color: #00a4dc; font-size: 15px;")
        tbl_header_layout.addWidget(tbl_title)
        tbl_header_layout.addStretch()

        cfg = load_config()
        self.chk_auto_save = QCheckBox(f"⚡ {t('auto_save')}")
        self.chk_auto_save.setChecked(cfg.get("auto_save_enabled", False))
        self.chk_auto_save.setToolTip(t("auto_save_desc"))
        self.chk_auto_save.setStyleSheet("color: #2ecc71; font-weight: bold;")
        self.chk_auto_save.toggled.connect(self._on_auto_save_toggled)
        tbl_header_layout.addWidget(self.chk_auto_save)
        tbl_header_layout.addSpacing(10)

        btn_add_line = QPushButton(t("add_line"))
        btn_add_line.setFocusPolicy(Qt.NoFocus)
        btn_add_line.clicked.connect(self.insert_subtitle_at_playhead)
        btn_del_line = QPushButton(t("delete_line"))
        btn_del_line.setFocusPolicy(Qt.NoFocus)
        btn_del_line.clicked.connect(self.delete_selected_subtitle)
        tbl_header_layout.addWidget(btn_add_line)
        tbl_header_layout.addWidget(btn_del_line)

        self.btn_more_opts = QPushButton("⋮ Options")
        self.btn_more_opts.setFocusPolicy(Qt.NoFocus)
        self.btn_more_opts.setStyleSheet("background-color: rgba(255, 255, 255, 0.1); font-weight: bold;")
        self.btn_more_opts.clicked.connect(self._show_more_video_options)
        tbl_header_layout.addWidget(self.btn_more_opts)

        table_layout.addLayout(tbl_header_layout)

        self.sub_model = FastSubtitleTableModel()
        self.sub_table = AutoScrollTableView()
        self.sub_table.setModel(self.sub_model)
        self.sub_table.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.sub_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.sub_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.sub_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.sub_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.sub_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.sub_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sub_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sub_table.clicked.connect(self._on_table_row_selected)
        self.sub_model.dataChanged.connect(self._on_model_data_changed)
        table_layout.addWidget(self.sub_table)
        self.top_h_splitter.addWidget(self.table_container)

        self._use_native_wayland_gl = IS_LINUX and self._is_native_wayland_session()
        self.video_surface = VideoSurfaceGLWidget() if self._use_native_wayland_gl else VideoSurfaceWidget()
        self.video_surface.clicked.connect(self._toggle_playback)
        self.aspect_container = AspectRatioContainer(self.video_surface)
        self.top_h_splitter.addWidget(self.aspect_container)

        self.top_h_splitter.setStretchFactor(0, 2)
        self.top_h_splitter.setStretchFactor(1, 4)
        self.top_h_splitter.setSizes([460, 780])
        self.main_v_splitter.addWidget(self.top_h_splitter)

        self.timeline_box = QWidget()
        self.timeline_box.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        timeline_layout = QVBoxLayout(self.timeline_box)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(4)

        self.scroll_area = QScrollArea()
        self.scroll_area.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        h_bar = self.scroll_area.horizontalScrollBar()
        h_bar.sliderReleased.connect(self._on_scrollbar_released)

        self.waveform = WaveformWidget()
        self.waveform.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.waveform.subtitle_changed.connect(self._on_sub_state_changed)
        self.waveform.subtitle_selected.connect(self._on_waveform_sub_selected)
        self.waveform.seek_requested.connect(self._on_seek_requested)
        self.waveform.wheel_seek_requested.connect(self._relative_seek)
        self.scroll_area.setWidget(self.waveform)

        self.time_hud = TimeHUDLabel(self.scroll_area.viewport())
        self.scroll_area.viewport().installEventFilter(self)

        timeline_layout.addWidget(self.scroll_area)
        self.main_v_splitter.addWidget(self.timeline_box)
        self.main_v_splitter.setStretchFactor(0, 3)
        self.main_v_splitter.setStretchFactor(1, 2)
        self.main_v_splitter.setSizes([500, 240])

        self.status_label = QLabel(t("status_shortcuts") + " | F: Fullscreen")
        self.status_label.setStyleSheet("color: #aaa; font-weight: bold; padding: 2px;")
        main_layout.addWidget(self.status_label)

        self.controls_container = QWidget()
        controls = QHBoxLayout(self.controls_container)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.setDirection(QHBoxLayout.LeftToRight)

        self.btn_toggle_list = QPushButton("📋 List")
        self.btn_toggle_list.setCheckable(True)
        self.btn_toggle_list.setChecked(True)
        self.btn_toggle_list.setFocusPolicy(Qt.NoFocus)
        self.btn_toggle_list.setToolTip("Toggle Subtitle List Panel")
        self.btn_toggle_list.clicked.connect(self._on_manual_toggle_list)
        controls.addWidget(self.btn_toggle_list)

        btn_open_vid = QPushButton(t("open_video"))
        btn_open_vid.setFocusPolicy(Qt.NoFocus)
        btn_open_vid.clicked.connect(self._browse_video)
        controls.addWidget(btn_open_vid)

        btn_open_sub = QPushButton(t("open_sub"))
        btn_open_sub.setFocusPolicy(Qt.NoFocus)
        btn_open_sub.clicked.connect(self._browse_sub)
        controls.addWidget(btn_open_sub)

        self.lbl_audio = QLabel("🎧")
        controls.addWidget(self.lbl_audio)
        self.audio_combo = QComboBox()
        self.audio_combo.setFocusPolicy(Qt.NoFocus)
        self.audio_combo.setMinimumWidth(90)
        self.audio_combo.currentIndexChanged.connect(self._on_audio_track_changed)
        controls.addWidget(self.audio_combo)

        self.lbl_link = QLabel(t("link_mode"))
        controls.addWidget(self.lbl_link)
        self.link_combo = QComboBox()
        self.link_combo.setFocusPolicy(Qt.NoFocus)
        self.link_combo.addItems([t("link_none"), t("link_all"), t("link_following")])
        self.link_combo.setCurrentIndex(1)
        self.waveform.link_mode = "all"
        self.link_combo.currentIndexChanged.connect(self._on_link_mode_changed)
        controls.addWidget(self.link_combo)

        self.lbl_speed = QLabel(t("speed"))
        controls.addWidget(self.lbl_speed)
        self.speed_combo = QComboBox()
        self.speed_combo.setFocusPolicy(Qt.NoFocus)
        self.speed_combo.addItems(["0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x"])
        self.speed_combo.setCurrentText("1.0x")
        self.speed_combo.currentTextChanged.connect(self._change_speed)
        controls.addWidget(self.speed_combo)

        self.btn_batch = QPushButton(t("batch_tools"))
        self.btn_batch.setFocusPolicy(Qt.NoFocus)
        self.btn_batch.setStyleSheet("background-color: rgba(255, 255, 255, 0.12); color: white; font-weight: bold;")
        self.btn_batch.clicked.connect(self._open_batch_dialog)
        controls.addWidget(self.btn_batch)

        self.btn_info = QPushButton(t("sub_info"))
        self.btn_info.setFocusPolicy(Qt.NoFocus)
        self.btn_info.clicked.connect(self.show_subtitle_info_dialog)
        controls.addWidget(self.btn_info)

        self.chk_keep_styles = QCheckBox(t("keep_styles"))
        self.chk_keep_styles.setChecked(True)
        self.chk_keep_styles.setFocusPolicy(Qt.NoFocus)
        controls.addWidget(self.chk_keep_styles)

        self.lbl_enc = QLabel(t("encoding"))
        controls.addWidget(self.lbl_enc)
        self.encoding_combo = QComboBox()
        self.encoding_combo.setFocusPolicy(Qt.NoFocus)
        self.encoding_combo.addItems(["utf-8-sig", "utf-8", "cp1256", "latin-1"])
        controls.addWidget(self.encoding_combo)

        self.lbl_fmt = QLabel(t("format"))
        controls.addWidget(self.lbl_fmt)
        self.format_combo = QComboBox()
        self.format_combo.setFocusPolicy(Qt.NoFocus)
        self.format_combo.addItems([t("keep_original"), ".ass", ".srt", ".vtt"])
        controls.addWidget(self.format_combo)

        self.save_btn = QPushButton(t("save"))
        self.save_btn.setStyleSheet("font-weight: bold; background-color: #00a4dc; color: white;")
        self.save_btn.setFocusPolicy(Qt.NoFocus)
        self.save_btn.clicked.connect(self.save_subtitles_direct)
        controls.addWidget(self.save_btn)

        self.save_as_btn = QPushButton(t("save_as"))
        self.save_as_btn.setFocusPolicy(Qt.NoFocus)
        self.save_as_btn.clicked.connect(self.save_subtitles_as)
        controls.addWidget(self.save_as_btn)

        controls.addStretch()
        main_layout.addWidget(self.controls_container)

        self._secondary_control_widgets = [
            self.lbl_speed, self.speed_combo,
            self.btn_info, self.chk_keep_styles,
            self.lbl_enc, self.encoding_combo,
            self.lbl_fmt, self.format_combo,
            self.save_as_btn
        ]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_dynamic_responsive_layout()

    def _on_manual_toggle_list(self, checked: bool):
        self._manual_list_override = checked
        self.table_container.setVisible(checked)

    def _apply_dynamic_responsive_layout(self):
        if self._is_video_fullscreen:
            return
        w = self.width()
        h = max(1, self.height())
        aspect = w / float(h)

        # في حال التايلنق (Hyprland نصف شاشة أو مربع): نخفي القائمة اليسرى تلقائياً للتركيز على الفيديو والويف فورم
        is_compact_or_square = (w < 960) or (aspect < 1.35)
        if self._manual_list_override is None:
            self.table_container.setVisible(not is_compact_or_square)
            self.btn_toggle_list.setChecked(not is_compact_or_square)
        else:
            if w >= 1200 and aspect >= 1.5 and self._manual_list_override:
                self.table_container.setVisible(True)

        # إخفاء عناصر التحكم الثانوية عند ضيق العرض لمنع قص الأزرار الأساسية
        hide_secondary = w < 1100
        for widget in getattr(self, "_secondary_control_widgets", []):
            widget.setVisible(not hide_secondary)

        self.lbl_link.setVisible(w >= 780)

    def toggle_video_fullscreen(self):
        main_win = self.window()
        if hasattr(main_win, "tabs") and main_win.tabs.currentWidget() != self:
            return

        self._is_video_fullscreen = not self._is_video_fullscreen
        if self._is_video_fullscreen:
            self.table_container.hide()
            self.timeline_box.hide()
            self.status_label.hide()
            self.controls_container.hide()
            self.calibration_banner.hide()
            if hasattr(main_win, "tabs"):
                main_win.tabs.tabBar().hide()
            main_win.showFullScreen()
        else:
            main_win.showNormal()
            if hasattr(main_win, "tabs"):
                main_win.tabs.tabBar().show()
            self.timeline_box.show()
            self.status_label.show()
            self.controls_container.show()
            if self._calibrating_batch_dlg is not None:
                self.calibration_banner.show()
            self._apply_dynamic_responsive_layout()

    def _exit_video_fullscreen_if_active(self) -> bool:
        if self._is_video_fullscreen:
            self.toggle_video_fullscreen()
            return True
        return False

    def eventFilter(self, obj, event):
        if hasattr(self, 'scroll_area') and obj == self.scroll_area.viewport():
            if event.type() in (QEvent.Resize, QEvent.LayoutRequest, QEvent.Show):
                self._reposition_time_hud()
        return super().eventFilter(obj, event)

    def _reposition_time_hud(self):
        if not hasattr(self, 'time_hud') or not hasattr(self, 'scroll_area'):
            return
        vp = self.scroll_area.viewport()
        vp_w = vp.width()
        hud_w = self.time_hud.width()
        margin = 12
        self.time_hud.move(max(10, vp_w - hud_w - margin), margin)

    def _update_time_hud(self, cur_sec: float):
        cur_str = ms_to_timestamp(int(cur_sec * 1000.0))
        tot_str = ms_to_timestamp(int(self.video_duration * 1000.0))
        self.time_hud.setText(f"⏱ {cur_str} / {tot_str}")
        self.time_hud.adjustSize()
        self._reposition_time_hud()

    def _show_more_video_options(self):
        if not self.video_path:
            QMessageBox.information(self, "Notice", "Please open a video first to view available options.")
            return

        menu = QMenu(self)
        embedded_tracks = get_embedded_subtitle_tracks(self.video_path, IS_WINDOWS)

        if not embedded_tracks:
            action_none = menu.addAction("No Embedded Subtitle Streams Found")
            action_none.setEnabled(False)
        else:
            sub_menu = menu.addMenu("📥 Extract Embedded Subtitles")
            for t_info in embedded_tracks:
                track_action = sub_menu.addAction(t_info["label"])
                track_action.triggered.connect(lambda checked=False, t=t_info: self._handle_embedded_sub_action(t))

        menu.addSeparator()
        action_merger = menu.addAction("🎬 Send Current Video to Merger")
        action_merger.triggered.connect(lambda: self.switch_to_merger_requested.emit(self.video_path, self.sub_path or ""))

        menu.exec(self.btn_more_opts.mapToGlobal(self.btn_more_opts.rect().bottomLeft()))

    def _handle_embedded_sub_action(self, track_info: dict):
        action_menu = QMenu(self)
        a_export = action_menu.addAction("💾 Export to File")
        a_studio = action_menu.addAction("🎙️ Open Directly in Studio")
        a_merger = action_menu.addAction("🎬 Send Directly to Merger")

        chosen_action = action_menu.exec(self.btn_more_opts.mapToGlobal(self.btn_more_opts.rect().bottomLeft()))
        if not chosen_action:
            return

        v_path = Path(self.video_path)
        default_out = v_path.with_name(f"{v_path.stem}.{track_info['lang']}{track_info['ext']}")

        if chosen_action == a_export:
            save_path, _ = QFileDialog.getSaveFileName(
                self, "Save Extracted Subtitle", str(default_out), f"Subtitle (*{track_info['ext']})"
            )
            if save_path:
                if extract_embedded_subtitle(self.video_path, track_info["stream_idx"], save_path, IS_WINDOWS):
                    QMessageBox.information(self, "Success", f"Subtitle exported successfully to:\n{save_path}")
                else:
                    QMessageBox.critical(self, "Error", "Failed to extract subtitle from video container.")

        elif chosen_action == a_studio:
            temp_out = Path(tempfile.gettempdir()) / f"extracted_{v_path.stem}_{track_info['stream_idx']}{track_info['ext']}"
            if extract_embedded_subtitle(self.video_path, track_info["stream_idx"], str(temp_out), IS_WINDOWS):
                self.load_subtitles(str(temp_out))
            else:
                QMessageBox.critical(self, "Error", "Failed to extract subtitle.")

        elif chosen_action == a_merger:
            temp_out = Path(tempfile.gettempdir()) / f"extracted_{v_path.stem}_{track_info['stream_idx']}{track_info['ext']}"
            if extract_embedded_subtitle(self.video_path, track_info["stream_idx"], str(temp_out), IS_WINDOWS):
                self.switch_to_merger_requested.emit(self.video_path, str(temp_out))
            else:
                QMessageBox.critical(self, "Error", "Failed to extract subtitle.")

    def _on_scrollbar_released(self):
        if not self.video_path or self.waveform.pixels_per_sec <= 0:
            return
        h_bar = self.scroll_area.horizontalScrollBar()
        viewport_w = self.scroll_area.viewport().width()
        center_x = h_bar.value() + (viewport_w // 2)
        target_sec = center_x / self.waveform.pixels_per_sec
        max_sec = self.waveform.duration_sec if self.waveform.duration_sec > 0 else (self.waveform.width() / self.waveform.pixels_per_sec)
        target_sec = max(0.0, min(max_sec, target_sec))
        
        self.waveform.set_current_time(target_sec)
        self._on_seek_requested(target_sec)

    def _init_mpv(self):
        try:
            locale.setlocale(locale.LC_NUMERIC, 'C')
            if IS_LINUX:
                try:
                    libc = ctypes.CDLL(None)
                    libc.setlocale(1, b"C")
                except Exception:
                    pass
        except Exception:
            pass

        if self._use_native_wayland_gl:
            mpv_opts = {
                "vo": "libmpv",
                "hwdec": "auto-safe",
                "keep_open": "yes",
                "video_sync": "audio",
                "interpolation": "no",
                "scale": "bilinear",
                "cscale": "bilinear",
                "dscale": "bilinear",
                "sub_font": "sans-serif",
                "sub_font_size": 58,
                "sub_color": "#FFFFFF",
                "sub_border_color": "#000000",
                "sub_border_size": 3.5,
                "sub_bold": "yes"
            }
            self.player = mpv.MPV(**mpv_opts)
            self.video_surface.attach_player(self.player)
        else:
            mpv_opts = {
                "wid": int(self.video_surface.winId()),
                "vo": "gpu-next,gpu",
                "gpu_api": "vulkan",
                "hwdec": "auto-safe",
                "keep_open": "yes",
                "video_sync": "audio",
                "interpolation": "no",
                "scale": "bilinear",
                "cscale": "bilinear",
                "dscale": "bilinear",
                "sub_font": "sans-serif",
                "sub_font_size": 58,
                "sub_color": "#FFFFFF",
                "sub_border_color": "#000000",
                "sub_border_size": 3.5,
                "sub_bold": "yes"
            }
            try:
                self.player = mpv.MPV(**mpv_opts)
            except Exception as e:
                logger.warning(f"Vulkan gpu-api initialization failed ({e}), falling back to auto api.")
                mpv_opts.pop("gpu_api", None)
                self.player = mpv.MPV(**mpv_opts)

        @self.player.property_observer("time-pos")
        def time_observer(_name, value):
            if value is not None and not self.waveform.is_scrubbing_playhead and not self.waveform.is_dragging_sub:
                self.waveform.set_current_time(value)
                target_x = int(value * self.waveform.pixels_per_sec) - (self.scroll_area.viewport().width() // 2)
                self.scroll_area.horizontalScrollBar().setValue(max(0, target_x))

                self._auto_scroll_table_to_time(value)
                self._update_time_hud(value)

    def _auto_scroll_table_to_time(self, current_sec: float):
        if not self.subs_data or not self.subs_data.events or not self.waveform.sub_starts:
            return

        cur_ms = int(current_sec * 1000.0)
        idx = bisect.bisect_right(self.waveform.sub_starts, cur_ms) - 1

        if 0 <= idx < len(self.subs_data.events):
            sub = self.subs_data.events[idx]
            if sub.start <= cur_ms <= sub.end:
                if self._last_highlighted_row != idx:
                    self._last_highlighted_row = idx
                    model_idx = self.sub_model.index(idx, 0)
                    self.sub_table.blockSignals(True)
                    self.sub_table.selectRow(idx)
                    self.sub_table.scrollTo(model_idx, QAbstractItemView.PositionAtCenter)
                    self.sub_table.blockSignals(False)

    def _on_audio_track_changed(self, idx: int):
        if idx < 0: return
        track_data = self.audio_combo.itemData(idx)
        if not track_data: return

        stream_idx = track_data.get("stream_idx", 0)
        mpv_aid = track_data.get("mpv_aid", stream_idx + 1)
        self.current_audio_stream_idx = stream_idx

        if hasattr(self, "player"):
            try:
                self.player.aid = mpv_aid
                self.status_label.setText(f"Audio Track: {self.audio_combo.currentText()}")
            except Exception as e:
                logger.error(f"Failed to switch audio in player: {e}")

        if stream_idx in self.audio_cache:
            peaks, dur = self.audio_cache[stream_idx]
            self.waveform.set_duration_preload(dur)
            self.waveform.append_audio_peaks(peaks, 0, len(peaks))
            self.waveform.finalize_audio_peaks(dur)
            self.global_progress_updated.emit(100, 100)
            self._update_time_hud(0.0)
        else:
            self._start_audio_waveform_extraction(stream_idx)

    def _start_audio_waveform_extraction(self, stream_idx: int):
        if not self.video_path: return

        if self.worker and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()

        if self.video_duration > 0:
            self.waveform.set_duration_preload(self.video_duration)
            self._update_time_hud(0.0)

        self.global_progress_updated.emit(10, 100)
        self.worker = FastAudioWorker(self.video_path, audio_stream_idx=stream_idx, pre_duration=self.video_duration, is_windows=IS_WINDOWS)
        self.worker.chunk_ready.connect(self._on_worker_chunk)
        self.worker.finished.connect(self._on_audio_extracted)
        self.worker.error.connect(lambda err: QMessageBox.critical(self, t("error"), f"Audio extraction failed:\n{err}"))
        self.worker.start()

    def _on_worker_chunk(self, chunk, start_idx, total_est):
        self.waveform.append_audio_peaks(chunk, start_idx, total_est)
        if total_est > 0:
            self.global_progress_updated.emit(start_idx + len(chunk), total_est)

    def _on_audio_extracted(self, stream_idx: int, full_peaks: np.ndarray, actual_dur: float):
        self.audio_cache[stream_idx] = (full_peaks, actual_dur)
        self.waveform.finalize_audio_peaks(actual_dur)
        self.video_duration = actual_dur
        self.global_progress_updated.emit(100, 100)
        self.status_label.setText(f"{t('ready')}: {Path(self.video_path).name} ({actual_dur:.1f}s)")
        self._update_time_hud(0.0)

    def _sync_subtitles_to_player(self):
        self.live_sub_sync_timer.start()

    def _do_sync_subtitles_to_player(self):
        if not self.subs_data or not self.video_path: return
        try:
            if not self.temp_sub_path:
                self.temp_sub_path = str(Path(tempfile.gettempdir()) / f"_live_sync_{os.getpid()}.ass")
            
            self.subs_data.save(self.temp_sub_path, encoding="utf-8")
            
            has_sub = False
            for tr in self.player.track_list:
                if tr.get("type") == "sub" and tr.get("external-filename") == self.temp_sub_path:
                    has_sub = True
                    break
            
            if not has_sub:
                self.player.command("sub-add", self.temp_sub_path, "select")
            else:
                self.player.command("sub-reload")
        except Exception as e:
            logger.debug(f"Live sub sync error: {e}")

    def _record_history_state(self):
        if not self.subs_data: return
        self.is_dirty = True
        if len(self.undo_stack) > 30:
            self.undo_stack.pop(0)
        self.undo_stack.append([(s.start, s.end, s.text) for s in self.subs_data.events])
        self.redo_stack.clear()

    def prompt_unsaved_changes(self) -> bool:
        if not self.is_dirty or not self.subs_data:
            return True
        if getattr(self, "chk_auto_save", None) and self.chk_auto_save.isChecked():
            return True

        sub_name = Path(self.sub_path).name if self.sub_path else "Untitled"
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(t("warning"))
        msg_box.setText(f"You have unsaved changes in:\n[{sub_name}]\n\nDo you want to save changes before proceeding?")
        msg_box.setIcon(QMessageBox.Warning)
        
        btn_save = msg_box.addButton("Save Changes", QMessageBox.AcceptRole)
        btn_discard = msg_box.addButton("Discard Changes", QMessageBox.DestructiveRole)
        btn_cancel = msg_box.addButton("Cancel", QMessageBox.RejectRole)
        
        msg_box.setDefaultButton(btn_save)
        msg_box.exec()

        if msg_box.clickedButton() == btn_save:
            self.save_subtitles_direct()
            return not self.is_dirty
        elif msg_box.clickedButton() == btn_discard:
            self.is_dirty = False
            return True
        else:
            return False

    def load_video(self, path: str):
        self.video_path = path
        self.status_label.setText(f"{t('ready')}: {Path(path).name} (Inspecting...)")
        self.audio_cache.clear()
        self._last_highlighted_row = -1
        self.global_progress_updated.emit(15, 100)

        try:
            self.player.command("loadfile", str(Path(path).resolve()), "replace")
            self.player.pause = True
        except Exception as e:
            QMessageBox.critical(self, t("error"), f"Failed to load video:\n{str(e)}")
            return

        if self.probe_worker and self.probe_worker.isRunning():
            self.probe_worker.terminate()
            self.probe_worker.wait()

        self.probe_worker = MediaProbeWorker(path, IS_WINDOWS)
        self.probe_worker.finished.connect(self._on_probe_finished)
        self.probe_worker.start()

        if self.subs_data:
            self._sync_subtitles_to_player()

    def _on_probe_finished(self, duration: float, tracks: list):
        self.video_duration = duration
        if duration > 0:
            self.waveform.set_duration_preload(duration)
            self._update_time_hud(0.0)

        self.audio_combo.blockSignals(True)
        self.audio_combo.clear()
        default_choice_idx = 0
        if tracks:
            for idx, t_info in enumerate(tracks):
                self.audio_combo.addItem(t_info["label"], t_info)
                if t_info.get("is_default", False):
                    default_choice_idx = idx
            self.audio_combo.setCurrentIndex(default_choice_idx)
        else:
            self.audio_combo.addItem("Default Audio", {"stream_idx": 0, "mpv_aid": 1})
        self.audio_combo.blockSignals(False)

        track_data = self.audio_combo.itemData(default_choice_idx)
        initial_stream_idx = track_data.get("stream_idx", 0) if track_data else 0
        if hasattr(self, "player") and track_data:
            try:
                self.player.aid = track_data.get("mpv_aid", 1)
            except Exception:
                pass

        self.global_progress_updated.emit(40, 100)
        self._start_audio_waveform_extraction(initial_stream_idx)

    def load_subtitles(self, path: str):
        if not self.prompt_unsaved_changes():
            return

        self.sub_path = path
        self.status_label.setText(f"{Path(path).name} (Loading...)")
        self._last_highlighted_row = -1
        self.global_progress_updated.emit(20, 100)

        if self.sub_loader_worker and self.sub_loader_worker.isRunning():
            self.sub_loader_worker.terminate()
            self.sub_loader_worker.wait()

        self.sub_loader_worker = SubtitleLoaderWorker(path)
        self.sub_loader_worker.finished.connect(self._on_subtitles_loaded)
        self.sub_loader_worker.error.connect(lambda err: QMessageBox.critical(self, t("error"), f"Failed to load subtitles:\n{err}"))
        self.sub_loader_worker.start()

    def _on_subtitles_loaded(self, subs_data, lanes, starts, ends):
        self.subs_data = subs_data
        self.waveform.set_subtitles_data(self.subs_data.events, lanes, starts, ends)
        self.sub_model.set_events(self.subs_data.events, self.subs_data)

        self.undo_stack.clear()
        self.redo_stack.clear()
        self.is_dirty = False
        self._last_highlighted_row = -1
        self.global_progress_updated.emit(100, 100)

        if self.video_path:
            self._sync_subtitles_to_player()
        self.status_label.setText(f"{t('ready')}: {Path(self.sub_path).name} ({len(self.subs_data)} lines)")

    def _display_line_font_status(self, row: int):
        if not self.subs_data or not (0 <= row < len(self.subs_data.events)):
            return
        sub = self.subs_data.events[row]
        font_name, is_inst = get_event_font_info(sub, self.subs_data)
        status_sym = "✅ Installed" if is_inst else "❌ Missing"
        style_name = getattr(sub, "style", "Default")
        self.status_label.setText(
            f"Line #{row + 1} | Font: [{font_name}] ({status_sym}) | Style: [{style_name}]"
        )

    def _on_model_data_changed(self, top_left, bottom_right):
        if not self.subs_data: return
        self._record_history_state()
        self.subs_data.events.sort(key=lambda s: s.start)
        self.waveform.sub_starts = [s.start for s in self.subs_data.events]
        self.waveform.sub_ends = [s.end for s in self.subs_data.events]
        self.waveform._sub_cleaned_texts = [clean_sub_text(s.text) for s in self.subs_data.events]
        self.waveform._recompute_all_geometries()
        self.waveform.update()
        self._sync_subtitles_to_player()
        self._maybe_auto_save()

    def _on_sub_state_changed(self, delta_ms: int):
        if delta_ms == 0 or not self.subs_data: return
        self._record_history_state()
        self.last_applied_shift_ms = delta_ms

        max_limit_ms = int(self.waveform.duration_sec * 1000.0) if self.waveform.duration_sec > 0 else 86400000
        sel_idx = self.waveform.selected_sub_idx

        if sel_idx != -1 and 0 <= sel_idx < len(self.subs_data.events):
            if self.waveform.is_resizing_left:
                sub = self.subs_data.events[sel_idx]
                sub.start = max(0, min(sub.end - 100, sub.start + delta_ms))
            elif self.waveform.is_resizing_right:
                sub = self.subs_data.events[sel_idx]
                sub.end = min(max_limit_ms, max(sub.start + 100, sub.end + delta_ms))
            else:
                if self.waveform.link_mode == "all":
                    for s in self.subs_data.events:
                        dur = s.end - s.start
                        new_start = max(0, min(max_limit_ms - dur, s.start + delta_ms))
                        s.start = new_start
                        s.end = new_start + dur
                elif self.waveform.link_mode == "following":
                    sel_time = self.subs_data.events[sel_idx].start
                    for s in self.subs_data.events:
                        if s.start >= sel_time:
                            dur = s.end - s.start
                            new_start = max(0, min(max_limit_ms - dur, s.start + delta_ms))
                            s.start = new_start
                            s.end = new_start + dur
                else:
                    sub = self.subs_data.events[sel_idx]
                    dur = sub.end - sub.start
                    new_start = max(0, min(max_limit_ms - dur, sub.start + delta_ms))
                    sub.start = new_start
                    sub.end = new_start + dur

        self.subs_data.events.sort(key=lambda s: s.start)
        self.waveform.sub_starts = [s.start for s in self.subs_data.events]
        self.waveform.sub_ends = [s.end for s in self.subs_data.events]
        self.waveform._sub_cleaned_texts = [clean_sub_text(s.text) for s in self.subs_data.events]
        self.waveform._recompute_all_geometries()
        self.sub_model.set_events(self.subs_data.events, self.subs_data)
        self.waveform.update()
        self._sync_subtitles_to_player()
        self._maybe_auto_save()

        if self._calibrating_batch_dlg is not None:
            self._calibration_accumulated_ms += delta_ms
            self.lbl_cal_info.setText(
                f"🎯 Calibrating for Batch — Accumulated Shift: {self._calibration_accumulated_ms:+d} ms"
            )

    def _on_table_row_selected(self, index):
        row = index.row()
        if self.subs_data and 0 <= row < len(self.subs_data.events):
            sub = self.subs_data.events[row]
            self.waveform.selected_sub_idx = row
            self.waveform.update()
            target_sec = sub.start / 1000.0
            if self.video_path:
                self.player.pause = True
                self.waveform.set_current_time(target_sec)
                self._relative_seek_direct(target_sec)
                self._update_time_hud(target_sec)
            self._display_line_font_status(row)

    def _on_waveform_sub_selected(self, idx: int):
        if 0 <= idx < self.sub_model.rowCount():
            model_index = self.sub_model.index(idx, 0)
            self.sub_table.selectRow(idx)
            self.sub_table.scrollTo(model_index, QAbstractItemView.PositionAtCenter)
            self._display_line_font_status(idx)

    def insert_subtitle_at_playhead(self):
        if not self.subs_data: self.subs_data = pysubs2.SSAFile()
        self._record_history_state()
        cur_ms = int(self.waveform.current_time_sec * 1000.0)
        max_limit_ms = int(self.waveform.duration_sec * 1000.0) if self.waveform.duration_sec > 0 else (cur_ms + 2000)
        end_ms = min(max_limit_ms, cur_ms + 2000)
        self.subs_data.events.append(pysubs2.SSAEvent(start=cur_ms, end=end_ms, text="New Subtitle"))
        self.subs_data.events.sort(key=lambda s: s.start)
        self.waveform.sub_starts = [s.start for s in self.subs_data.events]
        self.waveform.sub_ends = [s.end for s in self.subs_data.events]
        self.waveform._sub_cleaned_texts = [clean_sub_text(s.text) for s in self.subs_data.events]
        self.waveform._recompute_all_geometries()
        self.sub_model.set_events(self.subs_data.events, self.subs_data)
        self.waveform.update()
        self._sync_subtitles_to_player()
        self._maybe_auto_save()

    def delete_selected_subtitle(self):
        sel_idx = self.waveform.selected_sub_idx
        if sel_idx == -1 and self.sub_table.currentIndex().isValid():
            sel_idx = self.sub_table.currentIndex().row()
        if self.subs_data and 0 <= sel_idx < len(self.subs_data.events):
            self._record_history_state()
            del self.subs_data.events[sel_idx]
            self.waveform.sub_starts = [s.start for s in self.subs_data.events]
            self.waveform.sub_ends = [s.end for s in self.subs_data.events]
            self.waveform._sub_cleaned_texts = [clean_sub_text(s.text) for s in self.subs_data.events]
            self.waveform._recompute_all_geometries()
            self.sub_model.set_events(self.subs_data.events, self.subs_data)
            self.waveform.update()
            self._sync_subtitles_to_player()
            self._maybe_auto_save()

    def split_subtitle_at_playhead(self):
        sel_idx = self.waveform.selected_sub_idx
        if sel_idx == -1 and self.sub_table.currentIndex().isValid():
            sel_idx = self.sub_table.currentIndex().row()
        if self.subs_data and 0 <= sel_idx < len(self.subs_data.events):
            sub = self.subs_data.events[sel_idx]
            cur_ms = int(self.waveform.current_time_sec * 1000.0)
            if sub.start < cur_ms < sub.end:
                self._record_history_state()
                orig_end = sub.end
                sub.end = cur_ms
                self.subs_data.events.insert(sel_idx + 1, pysubs2.SSAEvent(start=cur_ms, end=orig_end, text=sub.text))
                self.waveform.sub_starts = [s.start for s in self.subs_data.events]
                self.waveform.sub_ends = [s.end for s in self.subs_data.events]
                self.waveform._sub_cleaned_texts = [clean_sub_text(s.text) for s in self.subs_data.events]
                self.waveform._recompute_all_geometries()
                self.sub_model.set_events(self.subs_data.events, self.subs_data)
                self.waveform.update()
                self._sync_subtitles_to_player()
                self._maybe_auto_save()

    def _relative_seek_direct(self, target_sec: float):
        if not self.video_path: return
        try: self.player.command("seek", float(target_sec), "absolute+exact")
        except Exception: pass

    def _on_link_mode_changed(self, idx: int):
        if idx == 1: self.waveform.link_mode = "all"
        elif idx == 2: self.waveform.link_mode = "following"
        else: self.waveform.link_mode = "none"

    def _change_speed(self, text: str):
        try: self.player.speed = float(text.replace("x", ""))
        except Exception: pass

    def _toggle_playback(self):
        if self.video_path and hasattr(self, "player") and not self.player.playback_abort:
            self.player.pause = not self.player.pause

    def _relative_seek(self, seconds: float):
        if self.video_path:
            try: self.player.command("seek", seconds, "relative+exact")
            except Exception: pass

    def _on_seek_requested(self, target_sec: float):
        self.target_seek_time = target_sec
        if self.video_path:
            try: self.player.pause = True
            except Exception: pass
        if not self.seek_timer.isActive():
            self.seek_timer.start()

    def _execute_queued_seek(self):
        if self.target_seek_time is not None and self.video_path:
            try: self.player.command("seek", float(self.target_seek_time), "absolute+exact")
            except Exception: pass
            self._update_time_hud(float(self.target_seek_time))
            self.target_seek_time = None

    def undo(self):
        if not self.undo_stack or not self.subs_data: return
        self.is_dirty = True
        self.redo_stack.append([(s.start, s.end, s.text) for s in self.subs_data.events])
        prev_state = self.undo_stack.pop()
        self.subs_data.events = [pysubs2.SSAEvent(start=st, end=en, text=txt) for st, en, txt in prev_state]
        self.waveform.sub_starts = [s.start for s in self.subs_data.events]
        self.waveform.sub_ends = [s.end for s in self.subs_data.events]
        self.waveform._sub_cleaned_texts = [clean_sub_text(s.text) for s in self.subs_data.events]
        self.waveform._recompute_all_geometries()
        self.sub_model.set_events(self.subs_data.events, self.subs_data)
        self.waveform.update()
        self._sync_subtitles_to_player()
        self._maybe_auto_save()

    def redo(self):
        if not self.redo_stack or not self.subs_data: return
        self.is_dirty = True
        self.undo_stack.append([(s.start, s.end, s.text) for s in self.subs_data.events])
        next_state = self.redo_stack.pop()
        self.subs_data.events = [pysubs2.SSAEvent(start=st, end=en, text=txt) for st, en, txt in next_state]
        self.waveform.sub_starts = [s.start for s in self.subs_data.events]
        self.waveform.sub_ends = [s.end for s in self.subs_data.events]
        self.waveform._sub_cleaned_texts = [clean_sub_text(s.text) for s in self.subs_data.events]
        self.waveform._recompute_all_geometries()
        self.sub_model.set_events(self.subs_data.events, self.subs_data)
        self.waveform.update()
        self._sync_subtitles_to_player()
        self._maybe_auto_save()

    def _on_auto_save_toggled(self, checked):
        cfg = load_config()
        cfg["auto_save_enabled"] = checked
        save_config(cfg)
        if checked and self.is_dirty:
            self.save_subtitles_direct()

    def _maybe_auto_save(self):
        if getattr(self, "chk_auto_save", None) and self.chk_auto_save.isChecked() and self.subs_data and self.sub_path:
            try:
                enc = self.encoding_combo.currentText()
                self._apply_style_preference()
                self.subs_data.save(str(self.sub_path), encoding=enc)
                self.is_dirty = False
                self.status_label.setText(f"{t('auto_saved_toast')} {Path(self.sub_path).name}")
            except Exception as e:
                logger.error(f"Auto-save failed: {e}")

    def start_batch_calibration(self, sub_path: str, video_path: str, batch_dlg):
        self._calibrating_batch_dlg = batch_dlg
        self._calibration_accumulated_ms = 0
        self.lbl_cal_info.setText("🎯 Calibrating for Batch — Drag subtitles on waveform to set offset (0 ms)")
        self.calibration_banner.show()
        self.load_subtitles(sub_path)
        if video_path:
            self.load_video(video_path)

    def _finish_batch_calibration(self):
        dlg = self._calibrating_batch_dlg
        self.calibration_banner.hide()
        self._calibrating_batch_dlg = None
        if dlg is not None:
            if self._calibration_accumulated_ms != 0:
                dlg.spin_ms.setValue(self._calibration_accumulated_ms)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()

    def _open_batch_dialog(self):
        default_folder = ""
        if self.video_path and os.path.exists(self.video_path):
            default_folder = str(Path(self.video_path).parent)
        elif self.sub_path and os.path.exists(self.sub_path):
            default_folder = str(Path(self.sub_path).parent)

        if self._batch_dialog is None:
            self._batch_dialog = BatchTimingDialog(
                initial_shift_ms=self.last_applied_shift_ms,
                keep_styles_default=self.chk_keep_styles.isChecked(),
                default_video_folder=default_folder,
                parent=self
            )
        else:
            if default_folder:
                self._batch_dialog.default_video_folder = default_folder
            if self.last_applied_shift_ms != 0 and self._batch_dialog.spin_ms.value() == 0:
                self._batch_dialog.spin_ms.setValue(self.last_applied_shift_ms)

        self._batch_dialog.show()
        self._batch_dialog.raise_()
        self._batch_dialog.activateWindow()

    def _browse_video(self):
        f, _ = QFileDialog.getOpenFileName(self, t("open_video"), "", "Videos (*.mp4 *.mkv *.avi *.mov *.webm)")
        if f: self.load_video(f)

    def _browse_sub(self):
        if not self.prompt_unsaved_changes():
            return
        f, _ = QFileDialog.getOpenFileName(self, t("open_sub"), "", "Subtitles (*.srt *.ass *.ssa *.vtt)")
        if f: self.load_subtitles(f)

    def show_subtitle_info_dialog(self):
        if not self.subs_data or not getattr(self.subs_data, 'events', None):
            QMessageBox.information(self, t("notice"), "No subtitles are loaded yet.")
            return
        dlg = SubtitleInfoDialog(self.subs_data, self.sub_path, self)
        dlg.exec()

    def _init_shortcuts(self):
        QShortcut(QKeySequence.Undo, self, self.undo)
        QShortcut(QKeySequence.Redo, self, self.redo)
        QShortcut(QKeySequence.Save, self, self.save_subtitles_direct)
        QShortcut(QKeySequence(Qt.Key_Insert), self, self.insert_subtitle_at_playhead)
        QShortcut(QKeySequence(Qt.Key_Delete), self, self.delete_selected_subtitle)
        QShortcut(QKeySequence("Ctrl+K"), self, self.split_subtitle_at_playhead)

    def _apply_style_preference(self):
        if not self.chk_keep_styles.isChecked() and self.subs_data:
            self.subs_data.styles.clear()
            for ev in self.subs_data.events:
                ev.text = clean_sub_text(ev.text)

    def save_subtitles_direct(self):
        if not self.subs_data:
            QMessageBox.warning(self, t("warning"), "No subtitles loaded.")
            return
        if not self.sub_path:
            self.save_subtitles_as()
            return
        enc = self.encoding_combo.currentText()
        fmt = self.format_combo.currentText()
        target_path = Path(self.sub_path) if fmt == t("keep_original") else Path(self.sub_path).with_suffix(fmt)
        try:
            self._apply_style_preference()
            self.subs_data.save(str(target_path), encoding=enc)
            self.sub_path = str(target_path)
            self.is_dirty = False
            self.status_label.setText(f"{t('completed')}: {target_path.name} ({enc})")
            self._prompt_single_merge()
        except Exception as e:
            QMessageBox.critical(self, t("error"), f"Failed to save:\n{str(e)}")

    def save_subtitles_as(self):
        if not self.subs_data:
            QMessageBox.warning(self, t("warning"), "No subtitles loaded.")
            return
        fmt = self.format_combo.currentText()
        enc = self.encoding_combo.currentText()
        out_ext = Path(self.sub_path).suffix.lower() if (fmt == t("keep_original") and self.sub_path) else (fmt if fmt != t("keep_original") else ".ass")

        save_path, _ = QFileDialog.getSaveFileName(self, t("save_as"), self.sub_path or f"output{out_ext}", f"Subtitle File (*{out_ext})")
        if not save_path: return
        try:
            self._apply_style_preference()
            self.subs_data.save(save_path, encoding=enc)
            self.sub_path = save_path
            self.is_dirty = False
            self.status_label.setText(f"{t('completed')}: {Path(save_path).name} ({enc})")
            self._prompt_single_merge()
        except Exception as e:
            QMessageBox.critical(self, t("error"), f"Failed to save:\n{str(e)}")

    def _prompt_single_merge(self):
        if not self.sub_path: return
        reply = QMessageBox.question(
            self, t("tab_merger"),
            f"Subtitle saved successfully.\nDo you want to switch to Subtitle Merger tab with this file?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            vid = self.video_path if (self.video_path and os.path.exists(self.video_path)) else ""
            self.switch_to_merger_requested.emit(vid, self.sub_path)

    def cleanup(self):
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()
        if self.probe_worker and self.probe_worker.isRunning():
            self.probe_worker.terminate()
            self.probe_worker.wait()
        if self.sub_loader_worker and self.sub_loader_worker.isRunning():
            self.sub_loader_worker.terminate()
            self.sub_loader_worker.wait()

        if getattr(self, "_use_native_wayland_gl", False) and hasattr(self, "video_surface"):
            self.video_surface.release_render_context()

        if hasattr(self, "player"):
            self.player.terminate()
