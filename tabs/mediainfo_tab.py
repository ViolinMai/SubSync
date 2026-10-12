import os
import sys
import json
import re
import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QTextEdit, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QTabWidget, QApplication, QMessageBox, QFrame,
    QCheckBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QColor, QFont
from i18n import t

# استيراد آمن ومباشر لدوال قراءة الترجمة
from tabs.SubSync.utils import load_subtitles_safe, ms_to_timestamp, sanitize_font_name

PRIORITY_KEYS = {
    "General": [
        "file_name", "file_size", "duration", "overall_bit_rate",
        "format", "format_version", "encoded_date", "writing_application", "writing_library"
    ],
    "Video": [
        "format", "format_profile", "codec_id", "commercial_name",
        "width", "height", "display_aspect_ratio", "pixel_aspect_ratio",
        "bit_rate", "maximum_bit_rate",
        "frame_rate", "frame_rate_mode",
        "bit_depth", "hdr_format", "color_space", "chroma_subsampling", "color_range",
        "color_primaries", "transfer_characteristics", "matrix_coefficients",
        "writing_library", "encoding_settings"
    ],
    "Audio": [
        "format", "commercial_name", "codec_id",
        "bit_rate", "bit_rate_mode",
        "channel_s", "channel_layout",
        "sampling_rate", "bit_depth", "compression_mode",
        "language", "title", "default", "forced",
        "writing_library", "encoding_settings"
    ],
    "Text": [
        "format", "codec_id", "language", "title", "element_count",
        "first_timestamp", "last_timestamp", "play_res", "file_size"
    ],
    "Styles": [
        "styles_list"
    ],
    "Menu": [
        "chapters"
    ]
}

QUICK_INFO_KEYS = {
    "General": ["file_name", "file_size", "duration", "overall_bit_rate", "format", "writing_application"],
    "Video": ["format", "format_profile", "width", "height", "display_aspect_ratio", "bit_rate", "frame_rate", "bit_depth", "hdr_format", "color_range", "color_primaries", "writing_library"],
    "Audio": ["format", "bit_rate", "channel_s", "channel_layout", "sampling_rate", "bit_depth", "language", "title", "default"],
    "Text": ["format", "codec_id", "element_count", "first_timestamp", "last_timestamp", "play_res", "file_size"],
    "Styles": ["styles_list"],
    "Menu": ["chapters"]
}

IGNORED_KEYS = {
    "count", "count_of_stream_of_this_kind", "kind_of_stream", "stream_identifier",
    "unique_id", "streamorder", "id", "video_format_list", "video_format_withhint_list",
    "codecs_video", "audio_format_list", "audio_format_withhint_list", "audio_codecs",
    "audio_language_list", "audio_channels_total", "text_format_list", "text_format_withhint_list",
    "text_codecs", "text_language_list", "format_url", "format_extensions_usually_used",
    "codec_id_url", "codec_id_info", "format_info", "delay_origin", "isstreamable",
    "proportion_of_this_stream", "service_kind", "chapters_pos_begin", "chapters_pos_end",
    "colour_description_present", "colour_description_present_source", "colour_range_source"
}

def detect_hdr_status(track_data: dict) -> str:
    trans = str(track_data.get("transfer_characteristics", "")).lower()
    prim = str(track_data.get("color_primaries", "")).lower()
    hdr_f = str(track_data.get("hdr_format", "")).upper()

    if hdr_f:
        return hdr_f
    if "smpte st 2084" in trans or "pq" in trans:
        return "HDR10"
    if "arib std-b67" in trans or "hlg" in trans:
        return "HLG"
    if "bt.2020" in prim and "10" in str(track_data.get("bit_depth", "")):
        return "HDR"
    return "SDR"

def clean_key_name(k: str) -> str:
    mapping = {
        "file_name": "File Name",
        "file_size": "File Size",
        "duration": "Duration",
        "overall_bit_rate": "Overall Bitrate",
        "format": "Format / Standard",
        "format_profile": "Profile",
        "codec_id": "Codec ID",
        "commercial_name": "Format Name",
        "width": "Width",
        "height": "Height",
        "display_aspect_ratio": "Aspect Ratio",
        "pixel_aspect_ratio": "Pixel Ratio (PAR)",
        "bit_rate": "Bitrate",
        "maximum_bit_rate": "Max Bitrate",
        "frame_rate": "Frame Rate",
        "frame_rate_mode": "Frame Rate Mode",
        "bit_depth": "Bit Depth",
        "hdr_format": "Dynamic Range",
        "color_space": "Color Space",
        "chroma_subsampling": "Chroma Subsampling",
        "color_range": "Color Range",
        "color_primaries": "Color Primaries",
        "transfer_characteristics": "Transfer Characteristics",
        "matrix_coefficients": "Matrix Coefficients",
        "channel_s": "Channels",
        "channel_layout": "Channel Layout",
        "sampling_rate": "Sampling Rate",
        "compression_mode": "Compression",
        "writing_application": "Writing App (Muxer)",
        "writing_library": "Encoder / Library",
        "encoding_settings": "Encoding Settings",
        "encoded_date": "Encoded Date",
        "language": "Language",
        "title": "Title / Track Name",
        "default": "Default Track",
        "forced": "Forced Track",
        "element_count": "Total Dialogues",
        "first_timestamp": "First Dialogue Time",
        "last_timestamp": "Last Dialogue Time",
        "play_res": "Canvas Resolution (PlayRes)",
        "styles_list": "Defined Styles & Fonts"
    }
    return mapping.get(k, k.replace("_", " ").title())

def format_human_value(key: str, val) -> str:
    if val is None:
        return ""
    v_str = str(val).strip()
    k_low = key.lower()

    if k_low == "codec_id":
        c_up = v_str.upper()
        if "S_TEXT/ASS" in c_up or "SSA" in c_up:
            return "ASS (Advanced SubStation Alpha)"
        elif "S_TEXT/UTF8" in c_up or "SUBRIP" in c_up:
            return "SRT (SubRip Text)"
        elif "S_HDMV/PGS" in c_up or "PGS" in c_up:
            return "PGS (HDMV Graphic Subtitle)"
        elif "S_TEXT/WEBVTT" in c_up or "VTT" in c_up:
            return "WebVTT"
        return v_str

    if k_low in ("file_size", "stream_size") and v_str.isdigit():
        sz = int(v_str)
        if sz >= 1024**3:
            return f"{sz / (1024**3):.2f} GiB ({sz:,} bytes)"
        elif sz >= 1024**2:
            return f"{sz / (1024**2):.2f} MiB ({sz:,} bytes)"
        else:
            return f"{sz / 1024:.2f} KiB"

    elif k_low in ("bit_rate", "overall_bit_rate", "maximum_bit_rate") and v_str.isdigit():
        br = int(v_str)
        if br >= 1_000_000:
            return f"{br / 1_000_000:.2f} Mb/s ({br:,} bps)"
        else:
            return f"{br / 1000:.1f} kb/s"

    elif k_low == "duration" and v_str.replace(".", "").isdigit():
        try:
            ms = float(v_str)
            if ms > 5000:
                secs = int(ms // 1000)
                m, s = divmod(secs, 60)
                h, m = divmod(m, 60)
                ms_rem = int(ms % 1000)
                if h > 0:
                    return f"{h}h {m:02d}m {s:02d}s ({ms:,.0f} ms)"
                return f"{m}m {s:02d}s {ms_rem}ms"
        except Exception:
            pass

    elif k_low == "bit_depth" and v_str.isdigit():
        return f"{v_str}-bit"

    elif k_low == "sampling_rate" and v_str.isdigit():
        return f"{int(v_str) / 1000:.1f} kHz"

    return v_str

class MediaInfoTab(QWidget):
    request_studio_video = Signal()

    def __init__(self, main_window=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.current_file_path = None
        self.last_parsed_data = {}
        self.setAcceptDrops(True)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        top_bar = QHBoxLayout()
        self.lbl_title = QLabel("📊 MediaInfo Inspector")
        self.lbl_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #00a4dc;")
        top_bar.addWidget(self.lbl_title)
        top_bar.addStretch()

        self.chk_quick_info = QCheckBox("⚡ Quick Info (Clean View)")
        self.chk_quick_info.setChecked(True)
        self.chk_quick_info.setStyleSheet("color: #2ecc71; font-weight: bold; font-size: 13px;")
        self.chk_quick_info.toggled.connect(self._refresh_current_view)
        top_bar.addWidget(self.chk_quick_info)
        top_bar.addSpacing(15)

        self.btn_sync_studio = QPushButton("🎙️ Sync from Studio")
        self.btn_sync_studio.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 6px 14px;")
        self.btn_sync_studio.clicked.connect(self.sync_from_studio)
        top_bar.addWidget(self.btn_sync_studio)

        self.btn_browse = QPushButton("📁 Open File...")
        self.btn_browse.setStyleSheet("background-color: rgba(255, 255, 255, 0.1); color: white; font-weight: bold; padding: 6px 14px;")
        self.btn_browse.clicked.connect(self._browse_file)
        top_bar.addWidget(self.btn_browse)

        self.btn_copy = QPushButton("📋 Copy Formatted Text")
        self.btn_copy.setStyleSheet("background-color: #00a4dc; color: white; font-weight: bold; padding: 6px 14px;")
        self.btn_copy.clicked.connect(self._copy_report)
        top_bar.addWidget(self.btn_copy)

        layout.addLayout(top_bar)

        self.lbl_path = QLabel("Drag & drop any media or subtitle file here, or click 'Open File'...")
        self.lbl_path.setStyleSheet("color: #888; font-size: 12px; background: rgba(255, 255, 255, 0.03); padding: 7px; border-radius: 6px; border: 1px dashed rgba(255, 255, 255, 0.15);")
        self.lbl_path.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_path)

        self.views_tabs = QTabWidget()
        
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabels(["Property", "Value"])
        self.tree_widget.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree_widget.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree_widget.setStyleSheet("""
            QTreeWidget {
                background-color: rgba(20, 20, 25, 0.7);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
            }
            QTreeWidget::item { padding: 4px; }
            QTreeWidget::item:selected { background-color: #00a4dc; }
        """)
        self.views_tabs.addTab(self.tree_widget, "🌲 Prioritized Tree View")

        self.txt_report = QTextEdit()
        self.txt_report.setReadOnly(True)
        self.txt_report.setStyleSheet("""
            QTextEdit {
                background-color: rgba(15, 15, 20, 0.85);
                color: #e0e0e0;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 10px;
            }
        """)
        self.views_tabs.addTab(self.txt_report, "📄 Clean Text Report")

        layout.addWidget(self.views_tabs, 1)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            file_p = urls[0].toLocalFile()
            if os.path.isfile(file_p):
                self.inspect_file(file_p)

    def _browse_file(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select Media or Subtitle File", "",
            "Media Files (*.mp4 *.mkv *.avi *.mov *.webm *.m4v *.ts *.mp3 *.flac *.aac *.wav *.ass *.srt *.ssa *.vtt);;All Files (*.*)"
        )
        if f:
            self.inspect_file(f)

    def sync_from_studio(self):
        if self.main_window and hasattr(self.main_window, 'subsync_tab'):
            vid = getattr(self.main_window.subsync_tab, 'video_path', None)
            sub = getattr(self.main_window.subsync_tab, 'sub_path', None)
            target = vid or sub
            if target and os.path.exists(target):
                self.inspect_file(target)
                return
        QMessageBox.information(self, "Notice", "No active video or subtitle loaded in Studio currently.")

    def inspect_file(self, file_path: str):
        self.current_file_path = file_path
        self.lbl_path.setText(f"File: <b>{file_path}</b>")
        self.last_parsed_data = self._extract_metadata(file_path)
        self._refresh_current_view()

    def _refresh_current_view(self):
        if not self.last_parsed_data:
            return
        is_quick = self.chk_quick_info.isChecked()
        self._populate_tree(self.last_parsed_data, is_quick)
        self._generate_clean_text_report(self.last_parsed_data, is_quick)

    def _parse_standalone_subtitle(self, file_path: str) -> dict:
        """تحليل ملفات الترجمة المستقلة بدون General."""
        p = Path(file_path)
        ext = p.suffix.lower()
        stat = p.stat()

        subs = load_subtitles_safe(file_path)
        ev_count = len(subs.events)

        first_ts = ms_to_timestamp(subs.events[0].start) if ev_count > 0 else "00:00:00.000"
        last_ts = ms_to_timestamp(subs.events[-1].end) if ev_count > 0 else "00:00:00.000"

        fmt_name = "ASS" if ext in ('.ass', '.ssa') else ("SRT" if ext == '.srt' else ext.upper().replace('.', ''))
        codec_id = "S_TEXT/ASS" if ext in ('.ass', '.ssa') else "S_TEXT/UTF8"

        text_data = {
            "format": fmt_name,
            "codec_id": codec_id,
            "file_size": stat.st_size,
            "element_count": ev_count,
            "first_timestamp": first_ts,
            "last_timestamp": last_ts
        }

        structured = {"Text": [text_data]}

        if ext in ('.ass', '.ssa'):
            rx = subs.info.get("PlayResX")
            ry = subs.info.get("PlayResY")
            if rx and ry:
                text_data["play_res"] = f"{rx} x {ry}"

            if hasattr(subs, "styles") and subs.styles:
                styles_dict = {}
                for s_name, s_obj in subs.styles.items():
                    raw_fn = getattr(s_obj, "fontname", "Arial")
                    fn = sanitize_font_name(raw_fn) or "Default Font"
                    fs = getattr(s_obj, "fontsize", 72)
                    styles_dict[s_name] = f"Font: [{fn}] | Size: {fs}pt"
                structured["Styles"] = [{"styles_list": styles_dict}]

        return structured

    def _extract_metadata(self, file_path: str) -> dict:
        ext = Path(file_path).suffix.lower()
        # إذا كان ملف ترجمة مستقل، نرسله مباشرة للمحلل الداخلي دون استدعاء MediaInfo.dll
        if ext in ('.ass', '.srt', '.ssa', '.vtt'):
            try:
                res = self._parse_standalone_subtitle(file_path)
                if res and "Text" in res:
                    return res
            except Exception as e:
                print(f"[MediaInfoTab] Subtitle parsing error: {e}")

        try:
            from pymediainfo import MediaInfo
            libs_dir = Path(__file__).resolve().parent.parent / "libs"
            mi_dll_win = libs_dir / "MediaInfo.dll"
            custom_lib = str(mi_dll_win) if mi_dll_win.exists() else None

            media_info = MediaInfo.parse(file_path, library_file=custom_lib) if custom_lib else MediaInfo.parse(file_path)
            structured = {}
            for track in media_info.tracks:
                t_type = track.track_type
                if t_type not in structured:
                    structured[t_type] = []
                data_dict = track.to_data()

                if t_type == "Video":
                    data_dict["hdr_format"] = detect_hdr_status(data_dict)

                structured[t_type].append(data_dict)
            return structured
        except Exception:
            pass

        return self._extract_metadata_ffprobe(file_path)

    def _extract_metadata_ffprobe(self, file_path: str) -> dict:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        cmd = [
            "ffprobe", "-v", "error",
            "-show_format", "-show_streams", "-show_chapters",
            "-of", "json",
            str(file_path)
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags)
            if res.returncode == 0 and res.stdout:
                data = json.loads(res.stdout)
                structured = {"General": [data.get("format", {})]}
                for s in data.get("streams", []):
                    c_type = s.get("codec_type", "Other").capitalize()
                    if c_type not in structured:
                        structured[c_type] = []
                    if c_type == "Video":
                        s["hdr_format"] = detect_hdr_status(s)
                    structured[c_type].append(s)
                if data.get("chapters"):
                    ch_dict = {}
                    for ch in data["chapters"]:
                        start_time = float(ch.get("start_time", 0.0))
                        title = ch.get("tags", {}).get("title", "Chapter")
                        ch_dict[f"{start_time:.3f}"] = title
                    structured["Menu"] = [{"chapters": ch_dict}]
                return structured
        except Exception:
            pass
        return {}

    def _order_and_filter_keys(self, cat_name: str, track_data: dict, is_quick: bool) -> list:
        priority_list = PRIORITY_KEYS.get(cat_name, [])
        quick_set = set(QUICK_INFO_KEYS.get(cat_name, []))

        ordered = []
        seen = set()

        for k in priority_list:
            if k in track_data:
                if is_quick and k not in quick_set:
                    continue
                val = track_data[k]
                if val is not None and str(val).strip() != "":
                    ordered.append((k, val))
                    seen.add(k)

        if not is_quick:
            for k, val in track_data.items():
                k_low = k.lower()
                if k in seen or k_low in IGNORED_KEYS:
                    continue
                if any(ignored in k_low for ignored in ("_count", "stream_size_", "duration_")):
                    continue
                if val is not None and str(val).strip() != "":
                    ordered.append((k, val))

        return ordered

    def _populate_tree(self, data: dict, is_quick: bool):
        self.tree_widget.clear()
        category_icons = {
            "General": "📦",
            "Video": "🎬",
            "Audio": "🔊",
            "Text": "💬",
            "Styles": "🎨",
            "Menu": "📑"
        }

        for cat_name, tracks in data.items():
            icon = category_icons.get(cat_name, "📁")
            for idx, track_data in enumerate(tracks):
                title = f"{icon} {cat_name}" if len(tracks) == 1 else f"{icon} {cat_name} #{idx + 1}"
                
                if cat_name == "Video":
                    w = track_data.get("width")
                    h = track_data.get("height")
                    codec = track_data.get("format")
                    hdr = track_data.get("hdr_format") or detect_hdr_status(track_data)
                    if w and h and codec:
                        title += f"  [{w}x{h} | {codec} | {hdr}]"
                elif cat_name == "Audio":
                    lang = (track_data.get("language") or track_data.get("title") or "").upper()
                    codec = track_data.get("format", "")
                    title += f"  [{lang} | {codec}]"
                elif cat_name == "Text":
                    lang = (track_data.get("language") or track_data.get("title") or "").upper()
                    codec = track_data.get("format") or track_data.get("codec_id") or ""
                    codec_short = "ASS" if "ASS" in str(codec).upper() else ("SRT" if "UTF8" in str(codec).upper() or "SUBRIP" in str(codec).upper() else str(codec))
                    title = f"💬 Subtitle Track  [{lang} | {codec_short}]" if lang else f"💬 Subtitle Track  [{codec_short}]"
                elif cat_name == "Styles":
                    title = f"🎨 Defined Styles & Fonts ({len(track_data.get('styles_list', {}))})"

                cat_item = QTreeWidgetItem([title, ""])
                cat_item.setExpanded(True)

                ordered_items = self._order_and_filter_keys(cat_name, track_data, is_quick)
                for raw_key, raw_val in ordered_items:
                    display_key = clean_key_name(raw_key)
                    display_val = format_human_value(raw_key, raw_val)

                    if isinstance(raw_val, dict):
                        sub_node = QTreeWidgetItem([display_key, ""])
                        for sub_k, sub_v in raw_val.items():
                            sub_node.addChild(QTreeWidgetItem([str(sub_k), str(sub_v)]))
                        sub_node.setExpanded(True)
                        cat_item.addChild(sub_node)
                    else:
                        item = QTreeWidgetItem([display_key, display_val])
                        if raw_key in ("bit_depth", "format", "codec_id", "color_range", "color_primaries", "bit_rate", "overall_bit_rate", "hdr_format"):
                            item.setForeground(1, QColor("#00d2ff"))
                        cat_item.addChild(item)

                if cat_item.childCount() > 0:
                    self.tree_widget.addTopLevelItem(cat_item)

    def _generate_clean_text_report(self, data: dict, is_quick: bool):
        lines = []
        lines.append("=" * 65)
        mode_tag = "[ QUICK INFO ]" if is_quick else "[ COMPLETE REPORT ]"
        lines.append(f"🔍 MediaInfo Summary {mode_tag} : {Path(self.current_file_path or '').name}")
        lines.append("=" * 65)

        for cat_name, tracks in data.items():
            for idx, track_data in enumerate(tracks):
                header = f"\n[{cat_name.upper()}" + (f" #{idx+1}]" if len(tracks) > 1 else "]")
                lines.append(header)
                lines.append("-" * len(header.strip()))

                ordered_items = self._order_and_filter_keys(cat_name, track_data, is_quick)
                for raw_key, raw_val in ordered_items:
                    if isinstance(raw_val, dict):
                        lines.append(f"  {clean_key_name(raw_key)}:")
                        for sk, sv in raw_val.items():
                            lines.append(f"    • {sk:<18} : {sv}")
                    else:
                        d_k = clean_key_name(raw_key)
                        d_v = format_human_value(raw_key, raw_val)
                        lines.append(f"  {d_k:<26} : {d_v}")

        self.txt_report.setPlainText("\n".join(lines))

    def _copy_report(self):
        txt = self.txt_report.toPlainText().strip()
        if txt:
            QApplication.clipboard().setText(txt)
            QMessageBox.information(self, "Success", "Clean report copied to clipboard!")
        else:
            QMessageBox.information(self, "Notice", "No report to copy.")
