import os
import re
import urllib.parse
from pathlib import Path
from natsort import natsorted
from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QComboBox, QCheckBox, QMessageBox, QFileDialog,
    QFrame, QGridLayout, QTableWidget, QTableWidgetItem, QHeaderView, QListWidget,
    QRadioButton, QButtonGroup, QAbstractItemView, QLineEdit
)
from PySide6.QtGui import QColor, QFontDatabase, QDesktopServices, QDragEnterEvent, QDropEvent
from .utils import (
    ms_to_timestamp, clean_sub_text, load_subtitles_safe, 
    get_episode_number, get_embedded_subtitle_tracks, extract_embedded_subtitle,
    apply_custom_cr_template, sanitize_font_name
)
from i18n import t

class BatchSubtitleTrackChooserDialog(QDialog):
    def __init__(self, sample_video_path, tracks, total_videos_count, default_dir="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose Subtitle Track & Output Location")
        self.resize(740, 520)
        self.tracks = tracks
        self.default_dir = default_dir
        self.selected_target = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        lbl_head = QLabel(f"<b>Batch Subtitle Extractor</b> — ({total_videos_count} video files detected)")
        lbl_head.setStyleSheet("font-size: 14px; color: #00a4dc;")
        layout.addWidget(lbl_head)

        lbl_sample = QLabel(f"Sample Video: <b>{Path(sample_video_path).name}</b>")
        lbl_sample.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(lbl_sample)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Track ID", "Language", "Title / Name", "Format", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)

        self.table.setRowCount(len(tracks))
        best_row = 0
        found_arabic = False

        for row, tr in enumerate(tracks):
            lang = tr.get("lang", "und").upper()
            is_ar = tr.get("lang", "").lower() in ("ara", "ar", "arabic")
            if is_ar and not found_arabic:
                best_row = row
                found_arabic = True
            elif not found_arabic and tr.get("is_default"):
                best_row = row

            it_id = QTableWidgetItem(f"#{tr['stream_idx']}")
            it_lang = QTableWidgetItem(lang)
            it_title = QTableWidgetItem(tr.get("title") or "[Untitled]")
            it_ext = QTableWidgetItem(f"{tr.get('codec')} ({tr.get('ext')})")
            it_def = QTableWidgetItem("⭐ Default" if tr.get("is_default") else "")
            
            if is_ar:
                it_lang.setForeground(QColor("#2ecc71"))
                it_lang.setText(f"{lang} (Arabic)")

            for it in (it_id, it_lang, it_title, it_ext, it_def):
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

            self.table.setItem(row, 0, it_id)
            self.table.setItem(row, 1, it_lang)
            self.table.setItem(row, 2, it_title)
            self.table.setItem(row, 3, it_ext)
            self.table.setItem(row, 4, it_def)

        if len(tracks) > 0:
            self.table.selectRow(best_row)

        layout.addWidget(self.table)

        self.chk_match_by_lang = QCheckBox("Smart Language Match across all videos (Recommended for Series)")
        self.chk_match_by_lang.setChecked(True)
        self.chk_match_by_lang.setStyleSheet("color: #2ecc71; font-weight: bold;")
        layout.addWidget(self.chk_match_by_lang)

        grp_dest = QFrame()
        grp_dest.setStyleSheet("background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; padding: 10px;")
        l_dest = QVBoxLayout(grp_dest)
        l_dest.setSpacing(8)

        self.rb_same_dir = QRadioButton("📁 Save subtitles beside each video file (Default)")
        self.rb_same_dir.setChecked(True)
        self.rb_custom_dir = QRadioButton("📂 Save all subtitles into a specific directory:")
        
        self.dest_group = QButtonGroup(self)
        self.dest_group.addButton(self.rb_same_dir)
        self.dest_group.addButton(self.rb_custom_dir)

        h_cust = QHBoxLayout()
        self.txt_custom_dir = QLineEdit()
        self.txt_custom_dir.setPlaceholderText("Select output folder...")
        self.txt_custom_dir.setEnabled(False)
        self.btn_browse_dest = QPushButton("Browse...")
        self.btn_browse_dest.setEnabled(False)
        self.btn_browse_dest.clicked.connect(self._browse_custom_dir)

        h_cust.addWidget(self.txt_custom_dir, 1)
        h_cust.addWidget(self.btn_browse_dest)

        self.rb_custom_dir.toggled.connect(lambda chk: self.txt_custom_dir.setEnabled(chk) or self.btn_browse_dest.setEnabled(chk))

        l_dest.addWidget(self.rb_same_dir)
        l_dest.addWidget(self.rb_custom_dir)
        l_dest.addLayout(h_cust)
        layout.addWidget(grp_dest)

        btn_box = QHBoxLayout()
        btn_all = QPushButton("📦 Extract ALL Tracks for Each Video")
        btn_all.setStyleSheet("background-color: #8e44ad; color: white; font-weight: bold; padding: 8px 14px;")
        btn_all.clicked.connect(self._select_all)

        btn_confirm = QPushButton("🚀 Extract Selected Track")
        btn_confirm.setStyleSheet("background-color: #00a4dc; color: white; font-weight: bold; padding: 8px 20px;")
        btn_confirm.clicked.connect(self._select_current)

        btn_cancel = QPushButton(t("cancel"))
        btn_cancel.clicked.connect(self.reject)

        btn_box.addWidget(btn_all)
        btn_box.addStretch()
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_confirm)
        layout.addLayout(btn_box)

    def _browse_custom_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.default_dir or "")
        if d:
            self.txt_custom_dir.setText(d)

    def _get_dest_folder(self):
        if self.rb_custom_dir.isChecked():
            path_str = self.txt_custom_dir.text().strip()
            if path_str and os.path.exists(path_str):
                return path_str
            else:
                QMessageBox.warning(self, t("warning"), "Please choose a valid destination folder.")
                return False
        return None

    def _select_all(self):
        dest = self._get_dest_folder()
        if dest is False:
            return
        self.selected_target = {"mode": "all", "out_dir": dest}
        self.accept()

    def _select_current(self):
        dest = self._get_dest_folder()
        if dest is False:
            return

        row = self.table.currentRow()
        if row < 0 or row >= len(self.tracks):
            QMessageBox.warning(self, t("warning"), "Please select a subtitle track from the table.")
            return

        chosen_track = self.tracks[row]
        self.selected_target = {
            "mode": "single",
            "lang": chosen_track.get("lang"),
            "track_id": chosen_track.get("stream_idx"),
            "ext": chosen_track.get("ext"),
            "match_by_lang": self.chk_match_by_lang.isChecked(),
            "out_dir": dest
        }
        self.accept()

class DropListWidget(QListWidget):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        extracted_paths = []
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_file() and p.suffix.lower() in ['.srt', '.ass', '.ssa', '.vtt', '.mkv', '.mp4']:
                extracted_paths.append(p)
            elif p.is_dir():
                items = natsorted([f for ext in ('*.srt', '*.ass', '*.ssa', '*.vtt', '*.mkv', '*.mp4') for f in p.glob(ext)])
                extracted_paths.extend(items)
        if extracted_paths:
            self.files_dropped.emit(extracted_paths)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            selected_items = self.selectedItems()
            if selected_items:
                parent_dlg = self.window()
                for item in selected_items:
                    r = self.row(item)
                    if hasattr(parent_dlg, "file_paths") and 0 <= r < len(parent_dlg.file_paths):
                        parent_dlg.file_paths.pop(r)
                    self.takeItem(r)
                event.accept()
                return
        super().keyPressEvent(event)

class SubtitleInfoDialog(QDialog):
    def __init__(self, subs_data, sub_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("sub_info"))
        self.setMinimumSize(680, 580)
        self.setStyleSheet("""
            QDialog { background-color: #141418; color: white; }
            QLabel { background: transparent; color: white; }
            QTableView { background-color: #1f2027; border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; color: white; }
            QHeaderView::section { background-color: #262730; color: #00a4dc; font-weight: bold; padding: 5px; }
            QPushButton { background: #00a4dc; color: white; border-radius: 6px; padding: 8px 16px; font-weight: bold; border: none; }
            QPushButton:hover { background: #008fbf; }
            QPushButton#BtnDownload { background: #e67e22; }
            QPushButton#BtnDownload:hover { background: #d35400; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        h_title = QHBoxLayout()
        lbl_title = QLabel("📊 " + t("sub_info"), self)
        lbl_title.setStyleSheet("font-size: 20px; font-weight: 900; color: #00a4dc;")
        btn_close = QPushButton("✖", self)
        btn_close.setFixedSize(30, 30)
        btn_close.setStyleSheet("QPushButton { background: rgba(255,255,255,0.06); color: #bbb; border-radius: 6px; font-weight: bold; } QPushButton:hover { background: #e23636; color: white; }")
        btn_close.clicked.connect(self.accept)
        h_title.addWidget(lbl_title)
        h_title.addStretch()
        h_title.addWidget(btn_close)
        layout.addLayout(h_title)

        is_srt = sub_path and Path(sub_path).suffix.lower() == '.srt'
        total_lines = len(subs_data.events) if subs_data else 0
        total_styles = len(subs_data.styles) if (subs_data and not is_srt) else 0
        first_time = ms_to_timestamp(subs_data.events[0].start) if (subs_data and subs_data.events) else "00:00:00.000"
        last_time = ms_to_timestamp(subs_data.events[-1].end) if (subs_data and subs_data.events) else "00:00:00.000"

        panel_meta = QFrame(self)
        panel_meta.setStyleSheet("background: rgba(255, 255, 255, 0.04); border-radius: 10px; border: 1px solid rgba(255,255,255,0.08); padding: 12px;")
        grid_meta = QGridLayout(panel_meta)
        grid_meta.setContentsMargins(5, 5, 5, 5)
        grid_meta.setSpacing(10)

        fmt_name = Path(sub_path).suffix.upper().replace(".", "") if sub_path else "ASS"
        grid_meta.addWidget(QLabel(f"<b>File:</b> {Path(sub_path).name if sub_path else 'In-Memory'}"), 0, 0)
        grid_meta.addWidget(QLabel(f"<b>Format:</b> {fmt_name}"), 0, 1)
        grid_meta.addWidget(QLabel(f"<b>Total Dialogues:</b> {total_lines}"), 1, 0)
        grid_meta.addWidget(QLabel(f"<b>Styles Count:</b> {'N/A (Plain SRT)' if is_srt else total_styles}"), 1, 1)
        grid_meta.addWidget(QLabel(f"<b>First Timestamp:</b> {first_time}"), 2, 0)
        grid_meta.addWidget(QLabel(f"<b>Last Timestamp:</b> {last_time}"), 2, 1)
        layout.addWidget(panel_meta)

        self.missing_fonts = []

        if is_srt:
            # ملفات SRT لا تحتوي على خطوط
            lbl_srt_note = QLabel("ℹ️ <b>Plain SRT Subtitle:</b> SRT format does not define fonts, styles, or coordinates. Fonts are rendered by video player defaults.")
            lbl_srt_note.setStyleSheet("color: #00d2ff; padding: 15px; background: rgba(0, 210, 255, 0.05); border-radius: 8px;")
            lbl_srt_note.setWordWrap(True)
            layout.addWidget(lbl_srt_note)
        else:
            font_counts = {}
            fn_pattern = re.compile(r"\\fn([^\\\}]+)")
            style_font_map = {}
            if subs_data and hasattr(subs_data, "styles"):
                for sname, sval in subs_data.styles.items():
                    if hasattr(sval, "fontname") and sval.fontname:
                        sanitized = sanitize_font_name(sval.fontname)
                        if sanitized:
                            style_font_map[sname] = sanitized

            for ev in (subs_data.events if subs_data else []):
                line_fonts = set()
                if ev.style in style_font_map:
                    line_fonts.add(style_font_map[ev.style])
                for emf in fn_pattern.findall(ev.text):
                    c_f = sanitize_font_name(emf)
                    if c_f:
                        line_fonts.add(c_f)
                for f_name in line_fonts:
                    font_counts[f_name] = font_counts.get(f_name, 0) + 1

            sys_fonts = set(f.lower() for f in QFontDatabase.families())
            # فحص الخطوط المرفقة في مجلد fonts إن وجد
            local_fonts = set()
            if sub_path:
                fonts_dir = Path(sub_path).parent / f"{Path(sub_path).stem}_fonts"
                if fonts_dir.exists():
                    local_fonts = {f.stem.lower() for f in fonts_dir.glob("*.*")}

            font_rows = []
            sorted_fonts = sorted(font_counts.items(), key=lambda x: x[1], reverse=True)
            for f_name, count in sorted_fonts:
                pct = (count / max(1, total_lines)) * 100.0
                is_installed = (f_name.lower() in sys_fonts) or (f_name.lower() in local_fonts)
                if not is_installed:
                    self.missing_fonts.append(f_name)
                font_rows.append((f_name, f"{count} ({pct:.1f}%)", is_installed))

            h_status = QHBoxLayout()
            lbl_status_title = QLabel("<b>System Fonts Status:</b>", self)
            if not font_rows:
                lbl_status_val = QLabel("Default Player Font (No custom fonts requested)", self)
                lbl_status_val.setStyleSheet("color: #aaa;")
            elif not self.missing_fonts:
                lbl_status_val = QLabel("✅ All required fonts are installed / embedded", self)
                lbl_status_val.setStyleSheet("color: #2ecc71; font-weight: 800;")
            else:
                lbl_status_val = QLabel(f"⚠️ ({len(self.missing_fonts)}) font(s) missing from system!", self)
                lbl_status_val.setStyleSheet("color: #e67e22; font-weight: 800;")
            h_status.addWidget(lbl_status_title)
            h_status.addWidget(lbl_status_val)
            h_status.addStretch()
            layout.addLayout(h_status)

            table_fonts = QTableWidget(self)
            table_fonts.setColumnCount(3)
            table_fonts.setHorizontalHeaderLabels(["Font Name", "Usage Ratio", "Status"])
            table_fonts.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            table_fonts.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
            table_fonts.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
            table_fonts.setEditTriggers(QTableWidget.NoEditTriggers)
            table_fonts.setSelectionBehavior(QTableWidget.SelectRows)
            table_fonts.setRowCount(len(font_rows))

            for row, (f_name, usage_str, is_inst) in enumerate(font_rows):
                item_f = QTableWidgetItem(f_name)
                item_u = QTableWidgetItem(usage_str)
                item_s = QTableWidgetItem("✅ Available" if is_inst else "❌ Missing")
                item_s.setForeground(QColor("#2ecc71") if is_inst else QColor("#e67e22"))
                if not is_inst:
                    item_f.setForeground(QColor("#e67e22"))
                table_fonts.setItem(row, 0, item_f)
                table_fonts.setItem(row, 1, item_u)
                table_fonts.setItem(row, 2, item_s)

            layout.addWidget(table_fonts)

        btn_actions = QHBoxLayout()
        btn_actions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if self.missing_fonts and not is_srt:
            btn_download_missing = QPushButton("🔍 Search Missing Fonts in Browser", self)
            btn_download_missing.setObjectName("BtnDownload")
            btn_download_missing.clicked.connect(self.search_missing_fonts)
            btn_actions.addWidget(btn_download_missing)

        btn_done = QPushButton("Close", self)
        btn_done.setMinimumWidth(100)
        btn_done.clicked.connect(self.accept)
        btn_actions.addWidget(btn_done)
        layout.addLayout(btn_actions)

    def search_missing_fonts(self):
        if not self.missing_fonts: return
        reply = QMessageBox.question(
            self, "Download Fonts",
            f"The browser will open to search for the following fonts:\n• " + "\n• ".join(self.missing_fonts) + "\n\nDo you want to continue?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            for f_name in self.missing_fonts:
                query = urllib.parse.quote(f"{f_name} font download")
                url = f"https://www.google.com/search?q={query}"
                QDesktopServices.openUrl(QUrl(url))

class BatchTimingDialog(QDialog):
    def __init__(self, initial_shift_ms=0, keep_styles_default=True, default_video_folder="", parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setWindowTitle("Batch Subtitle Tools & Extraction")
        self.resize(760, 600)
        self.file_paths = []
        self.default_video_folder = default_video_folder
        self.parent_tab = parent

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        lbl_head = QLabel("📁 Subtitle/Video Files (Drop items here - Supports Batch Shift & Custom ASS Conversion):")
        lbl_head.setStyleSheet("font-weight: bold; color: #00a4dc; font-size: 14px;")
        layout.addWidget(lbl_head)

        self.list_widget = DropListWidget(self)
        self.list_widget.files_dropped.connect(self._add_files_from_paths)
        self.list_widget.itemDoubleClicked.connect(lambda: self.calibrate_selected_in_studio())
        layout.addWidget(self.list_widget)

        btn_box = QHBoxLayout()
        add_btn = QPushButton("Add Subtitles/Videos...")
        add_btn.clicked.connect(self._browse_files)
        add_dir_btn = QPushButton("Add Folder...")
        add_dir_btn.clicked.connect(self._browse_folder)

        btn_calibrate = QPushButton(t("calibrate"))
        btn_calibrate.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        btn_calibrate.clicked.connect(self.calibrate_selected_in_studio)

        btn_extract_batch = QPushButton("📦 Extract Subs from Videos")
        btn_extract_batch.setStyleSheet("background-color: #8e44ad; color: white; font-weight: bold;")
        btn_extract_batch.clicked.connect(self._batch_extract_subtitles)

        btn_del_selected = QPushButton("Delete Selected (Del)")
        btn_del_selected.clicked.connect(self._delete_selected)

        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self._clear_files)

        btn_box.addWidget(add_btn)
        btn_box.addWidget(add_dir_btn)
        btn_box.addWidget(btn_calibrate)
        btn_box.addWidget(btn_extract_batch)
        btn_box.addWidget(btn_del_selected)
        btn_box.addWidget(clear_btn)
        layout.addLayout(btn_box)

        shift_box = QHBoxLayout()
        shift_box.addWidget(QLabel("Time Shift (ms):"))
        self.spin_ms = QSpinBox()
        self.spin_ms.setRange(-3600000, 3600000)
        self.spin_ms.setSingleStep(100)
        self.spin_ms.setValue(initial_shift_ms)
        shift_box.addWidget(self.spin_ms)

        shift_box.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(["Keep Original", "Custom ASS (Crunchyroll/Adobe Arabic)", ".ass", ".srt", ".vtt"])
        self.format_combo.currentTextChanged.connect(self._on_format_changed)
        shift_box.addWidget(self.format_combo)
        layout.addLayout(shift_box)

        opts_box = QHBoxLayout()
        opts_box.addWidget(QLabel("Encoding:"))
        self.enc_combo = QComboBox()
        self.enc_combo.addItems(["utf-8-sig", "utf-8", "cp1256"])
        opts_box.addWidget(self.enc_combo)

        self.chk_keep_styles = QCheckBox("Keep ASS Styles & Layouts")
        self.chk_keep_styles.setChecked(keep_styles_default)
        opts_box.addWidget(self.chk_keep_styles)
        layout.addLayout(opts_box)

        apply_btn = QPushButton("Apply Shift & Process Subtitles")
        apply_btn.setStyleSheet("background-color: #00a4dc; color: white; font-weight: bold; padding: 12px; font-size: 14px;")
        apply_btn.clicked.connect(self._apply_batch)
        layout.addWidget(apply_btn)

    def _on_format_changed(self, text):
        if "Custom ASS" in text:
            self.chk_keep_styles.setChecked(False)
            self.chk_keep_styles.setEnabled(False)
        else:
            self.chk_keep_styles.setEnabled(True)

    def _browse_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Choose Subtitles or Videos", "", "Media & Subs (*.srt *.ass *.ssa *.vtt *.mkv *.mp4)")
        self._add_files_from_paths([Path(f) for f in files])

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Folder")
        if folder:
            p = Path(folder)
            items = natsorted([f for ext in ('*.srt', '*.ass', '*.ssa', '*.vtt', '*.mkv', '*.mp4') for f in p.glob(ext)])
            self._add_files_from_paths(items)

    def _add_files_from_paths(self, paths):
        for p in paths:
            if p not in self.file_paths:
                self.file_paths.append(p)
                self.list_widget.addItem(p.name)

    def _delete_selected(self):
        for item in self.list_widget.selectedItems():
            r = self.list_widget.row(item)
            if 0 <= r < len(self.file_paths):
                self.file_paths.pop(r)
            self.list_widget.takeItem(r)

    def _clear_files(self):
        self.file_paths.clear()
        self.list_widget.clear()

    def _batch_extract_subtitles(self):
        vids = [p for p in self.file_paths if p.suffix.lower() in ('.mkv', '.mp4')]
        if not vids:
            QMessageBox.information(self, "Notice", "No MKV or MP4 videos found in the list to extract subtitles from.")
            return

        is_win = os.name == 'nt'
        sample_vid = vids[0]
        sample_tracks = get_embedded_subtitle_tracks(str(sample_vid), is_win)

        if not sample_tracks:
            QMessageBox.warning(self, "Notice", f"No embedded subtitle tracks detected in [{sample_vid.name}].")
            return

        default_base_dir = str(sample_vid.parent)
        chooser = BatchSubtitleTrackChooserDialog(str(sample_vid), sample_tracks, len(vids), default_base_dir, self)
        if chooser.exec() != QDialog.Accepted or not chooser.selected_target:
            return

        choice = chooser.selected_target
        mode = choice.get("mode")
        custom_out_dir = choice.get("out_dir")
        extracted_count = 0

        for v in vids:
            v_tracks = get_embedded_subtitle_tracks(str(v), is_win)
            if not v_tracks:
                continue

            target_tracks_to_extract = []

            if mode == "all":
                target_tracks_to_extract = v_tracks
            else:
                target_lang = choice.get("lang")
                match_lang = choice.get("match_by_lang", True)
                matched_tr = None

                if match_lang and target_lang:
                    for tr in v_tracks:
                        if tr.get("lang") == target_lang:
                            matched_tr = tr
                            break

                if not matched_tr:
                    target_id = choice.get("track_id")
                    for tr in v_tracks:
                        if tr.get("stream_idx") == target_id:
                            matched_tr = tr
                            break

                if not matched_tr and v_tracks:
                    matched_tr = v_tracks[0]

                if matched_tr:
                    target_tracks_to_extract.append(matched_tr)

            target_dir = Path(custom_out_dir) if custom_out_dir else v.parent

            for tr in target_tracks_to_extract:
                ext = tr.get("ext", ".srt")
                lang_tag = f".{tr.get('lang')}" if tr.get('lang') and tr.get('lang') != 'und' else ""
                out_name = f"{v.stem}{lang_tag}{ext}" if len(target_tracks_to_extract) > 1 or mode == "all" else f"{v.stem}{ext}"
                out_file = target_dir / out_name
                stream_id = tr.get("track_id", tr["stream_idx"])

                if extract_embedded_subtitle(str(v), stream_id, str(out_file), is_win):
                    extracted_count += 1
                    if out_file not in self.file_paths:
                        self.file_paths.append(out_file)
                        self.list_widget.addItem(f"✅ {out_file.name}")

        QMessageBox.information(self, "Extraction Done", f"Successfully extracted {extracted_count} subtitle track(s) from {len(vids)} video(s).")

    def calibrate_selected_in_studio(self):
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.file_paths):
            QMessageBox.information(self, t("notice"), "Please select a subtitle file from the list first.")
            return

        selected_sub = str(self.file_paths[row])
        matched_video = ""

        if self.default_video_folder and os.path.exists(self.default_video_folder):
            ep_num = get_episode_number(Path(selected_sub).name)
            v_dir = Path(self.default_video_folder)
            v_files = natsorted([f for ext in ('*.mp4', '*.mkv', '*.avi') for f in v_dir.glob(ext)])
            for v in v_files:
                if ep_num is not None and get_episode_number(v.name) == ep_num:
                    matched_video = str(v)
                    break
            if not matched_video and v_files:
                matched_video = str(v_files[0])

        if not matched_video or not os.path.exists(matched_video):
            chosen_vid, _ = QFileDialog.getOpenFileName(
                self, f"Select Video to calibrate with [{Path(selected_sub).name}]",
                self.default_video_folder or "",
                "Videos (*.mp4 *.mkv *.avi *.mov *.webm)"
            )
            if chosen_vid:
                matched_video = chosen_vid

        if self.parent_tab:
            self.parent_tab.load_subtitles(selected_sub)
            if matched_video:
                self.parent_tab.load_video(matched_video)

            def on_calibrated(shift_diff):
                if shift_diff != 0:
                    self.spin_ms.setValue(shift_diff)
                    QMessageBox.information(self, t("notice"), t("sync_back_success"))
                try:
                    self.parent_tab.waveform.subtitle_changed.disconnect(on_calibrated)
                except Exception:
                    pass

            self.parent_tab.waveform.subtitle_changed.connect(on_calibrated)
            self.close()

    def _apply_batch(self):
        subs_only = [p for p in self.file_paths if p.suffix.lower() in ('.srt', '.ass', '.ssa', '.vtt')]
        if not subs_only:
            QMessageBox.warning(self, t("warning"), "No subtitle files selected.")
            return

        shift_val = self.spin_ms.value()
        enc = self.enc_combo.currentText()
        target_fmt = self.format_combo.currentText()
        keep_styles = self.chk_keep_styles.isChecked()
        is_custom_ass = "Custom ASS" in target_fmt
        processed_subs = []

        for f in subs_only:
            try:
                subs = load_subtitles_safe(str(f))
                if shift_val != 0:
                    subs.shift(ms=shift_val)

                if is_custom_ass:
                    apply_custom_cr_template(subs)
                    out_path = f.with_suffix(".ass")
                else:
                    if not keep_styles:
                        subs.styles.clear()
                        for ev in subs.events:
                            ev.text = clean_sub_text(ev.text)

                    out_path = f
                    if target_fmt != "Keep Original":
                        out_path = f.with_suffix(target_fmt)

                subs.save(str(out_path), encoding=enc)
                processed_subs.append(out_path)
            except Exception as e:
                QMessageBox.critical(self, t("error"), f"Failed on {f.name}:\n{str(e)}")
                return

        reply = QMessageBox.question(
            self, "Process with Videos in Merger?",
            f"Successfully modified {len(processed_subs)} subtitles.\n\nDo you want to send them directly to Subtitle Merger tab?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes and processed_subs:
            sub_folder = str(processed_subs[0].parent)
            vid_folder = self.default_video_folder or ""
            if self.parent_tab and hasattr(self.parent_tab, "switch_to_merger_requested"):
                self.parent_tab.switch_to_merger_requested.emit(vid_folder, sub_folder)

        self.accept()
