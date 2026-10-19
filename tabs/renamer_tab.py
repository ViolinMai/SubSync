import re
import os
from pathlib import Path
from natsort import natsorted

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, 
    QComboBox, QGroupBox, QRadioButton, QSpinBox, QMessageBox, 
    QFileDialog, QAbstractItemView, QCheckBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from config_manager import load_config, save_config
from i18n import t

def parse_filename_metadata(filename: str):
    stem = Path(filename).stem
    ext = Path(filename).suffix

    crc_match = re.search(r'\[([a-fA-F0-9]{8})\]', stem)
    crc = crc_match.group(1).upper() if crc_match else ""

    res_match = re.search(r'(?i)\b(2160p|1440p|1080p|720p|480p|360p|4k|8k|uhd|fhd|hd)\b', stem)
    resolution = res_match.group(1).upper() if res_match else ""

    src_match = re.search(r'(?i)\b(bluray|bdrip|bd|web-dl|webrip|web|hdtv|dvdrip|dvd)\b', stem)
    source = src_match.group(1).upper() if src_match else ""

    codec_match = re.search(r'(?i)\b(x265|x264|hevc|h\.?265|h\.?264|av1|vp9|10bit|8bit)\b', stem)
    codec = codec_match.group(1).upper() if codec_match else ""

    group_match = re.match(r'^\[([^\]]+)\]', stem)
    group = group_match.group(1).strip() if group_match else ""

    clean = stem
    if crc_match: clean = clean.replace(crc_match.group(0), "")
    clean = re.sub(r'(?i)\b(2160p|1080p|720p|480p|4k|x265|x264|hevc|av1|bluray|web-dl|webrip|10bit|dual\s?audio)\b', '', clean)

    ep_num = None
    m = re.search(r'(?i)(?:s\d{1,2})?[ex](\d{1,4})', clean)
    if m: ep_num = int(m.group(1))
    if ep_num is None:
        m = re.search(r'(?i)(?:episode|ep|حلقة)[\s_.-]*(\d{1,4})', clean)
        if m: ep_num = int(m.group(1))
    if ep_num is None:
        m = re.search(r'-\s*0*(\d{1,4})(?:v\d)?', clean)
        if m: ep_num = int(m.group(1))
    if ep_num is None:
        matches = re.findall(r'(?<!\d)0*(\d{1,4})(?!\d)', clean)
        if matches: ep_num = int(matches[-1])

    return {
        "ep": ep_num,
        "res": resolution,
        "source": source,
        "codec": codec,
        "crc": crc,
        "group": group,
        "stem": stem,
        "ext": ext
    }

def get_episode_number(filename: str):
    meta = parse_filename_metadata(filename)
    return meta["ep"]

class DropLineEdit(QLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            self.setText(str(Path(urls[0].toLocalFile())))

class BatchTableWidget(QTableWidget):
    dropped = Signal(list)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        
    def dropEvent(self, event: QDropEvent):
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        valid_dirs = [p for p in paths if p.is_dir()]
        if valid_dirs: self.dropped.emit(valid_dirs)

class RenamerTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = load_config()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        mode_box = QGroupBox(t("renamer_mode"))
        m_lay = QHBoxLayout(mode_box)
        self.radio_single = QRadioButton(t("renamer_single"))
        self.radio_batch = QRadioButton(t("renamer_batch"))
        self.radio_single.setChecked(True)
        m_lay.addWidget(self.radio_single)
        m_lay.addWidget(self.radio_batch)
        m_lay.addStretch()
        layout.addWidget(mode_box)

        # Single Folder Area
        self.widget_single = QWidget()
        s_lay = QVBoxLayout(self.widget_single)
        s_lay.setContentsMargins(0, 0, 0, 0)
        s_lay.setSpacing(6)

        h_s = QHBoxLayout()
        self.entry_folder = DropLineEdit()
        self.entry_folder.setPlaceholderText(t("renamer_drop_hint"))
        self.entry_folder.textChanged.connect(self._on_inputs_changed)
        btn_sel = QPushButton(t("renamer_browse_btn"))
        btn_sel.clicked.connect(lambda: self.select_folder(self.entry_folder))
        h_s.addWidget(self.entry_folder, 1)
        h_s.addWidget(btn_sel)
        s_lay.addLayout(h_s)

        h_info = QHBoxLayout()
        self.entry_custom_title = QLineEdit()
        self.entry_custom_title.setPlaceholderText(t("renamer_custom_title"))
        self.entry_custom_title.textChanged.connect(self._on_inputs_changed)

        h_season = QHBoxLayout()
        h_season.addWidget(QLabel(t("renamer_season")))
        self.spin_season = QSpinBox()
        self.spin_season.setRange(1, 99)
        self.spin_season.valueChanged.connect(self._on_inputs_changed)
        h_season.addWidget(self.spin_season)

        h_info.addWidget(self.entry_custom_title, 3)
        h_info.addLayout(h_season, 1)
        s_lay.addLayout(h_info)
        layout.addWidget(self.widget_single)

        # Batch Folders Area
        self.widget_batch = QWidget()
        self.widget_batch.setVisible(False)
        b_lay = QVBoxLayout(self.widget_batch)
        b_lay.setContentsMargins(0, 0, 0, 0)
        b_lay.setSpacing(6)

        self.table_batch = BatchTableWidget()
        self.table_batch.dropped.connect(self.add_batch_folders_list)
        self.table_batch.setColumnCount(3)
        self.table_batch.setHorizontalHeaderLabels([t("renamer_col_dir"), t("renamer_custom_title"), t("renamer_season")])
        self.table_batch.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table_batch.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        b_lay.addWidget(self.table_batch)

        b_btns = QHBoxLayout()
        b_add = QPushButton(t("renamer_batch_add"))
        b_add.clicked.connect(self.browse_batch_folders)
        b_del = QPushButton(t("renamer_batch_remove"))
        b_del.clicked.connect(lambda: self.table_batch.removeRow(self.table_batch.currentRow()) if self.table_batch.currentRow() >= 0 else None)
        b_clear = QPushButton(t("renamer_batch_clear"))
        b_clear.clicked.connect(lambda: self.table_batch.setRowCount(0) or self._on_inputs_changed())
        b_btns.addWidget(b_add)
        b_btns.addWidget(b_del)
        b_btns.addWidget(b_clear)
        b_btns.addStretch()
        b_lay.addLayout(b_btns)
        layout.addWidget(self.widget_batch)

        self.radio_single.toggled.connect(lambda c: self.widget_single.setVisible(c) or self.widget_batch.setVisible(not c) or self._on_inputs_changed())

        # Pattern Builder
        pat_box = QGroupBox(t("renamer_pattern_box"))
        p_lay = QVBoxLayout(pat_box)
        p_lay.setSpacing(8)

        chip_bar = QHBoxLayout()
        chips = [
            ("{title}", "Show Title"),
            ("S{season:02d}", "Season 01"),
            ("E{ep:02d}", "Episode 01"),
            ("E{ep:03d}", "Ep 001"),
            ("{resolution}", "Resolution"),
            ("{source}", "Source"),
            ("{codec}", "Codec"),
            ("{crc}", "CRC32"),
            ("{orig}", "Original Name")
        ]
        for tag, tip in chips:
            btn_chip = QPushButton(f"+ {tag}")
            btn_chip.setToolTip(tip)
            btn_chip.setStyleSheet("background: rgba(0, 164, 220, 0.12); color: #00a4dc; border: 1px solid rgba(0, 164, 220, 0.3); border-radius: 4px; padding: 3px 8px; font-weight: bold;")
            btn_chip.clicked.connect(lambda ch=False, t_tag=tag: self._insert_chip_tag(t_tag))
            chip_bar.addWidget(btn_chip)
        chip_bar.addStretch()
        p_lay.addLayout(chip_bar)

        h_pat = QHBoxLayout()
        self.combo_pattern = QComboBox()
        self.combo_pattern.setEditable(True)
        default_pats = [
            "{title} S{season:02d}E{ep:02d}{ext}",
            "{title} S{season:02d}E{ep:02d} [{resolution}]{ext}",
            "{title} - {ep:02d}{ext}",
            "{title} E{ep:03d} [{resolution} {codec}]{ext}",
            "[{season:02d}x{ep:02d}] {title}{ext}"
        ]
        self.combo_pattern.addItems(self.config.get("patterns", default_pats))
        self.combo_pattern.setCurrentText(self.config.get("last_pattern", "{title} S{season:02d}E{ep:02d}{ext}"))
        self.combo_pattern.currentTextChanged.connect(self._on_inputs_changed)
        h_pat.addWidget(QLabel(t("renamer_pattern_lbl")))
        h_pat.addWidget(self.combo_pattern, 1)

        self.entry_exts = QLineEdit(self.config.get("last_extensions", ".mp4, .mkv, .srt, .ass"))
        self.entry_exts.setPlaceholderText(".mp4, .mkv")
        self.entry_exts.textChanged.connect(self._on_inputs_changed)
        h_pat.addWidget(QLabel(t("renamer_exts_lbl")))
        h_pat.addWidget(self.entry_exts)
        p_lay.addLayout(h_pat)

        h_clean = QHBoxLayout()
        self.chk_strip_group = QCheckBox(t("renamer_strip_group"))
        self.chk_strip_group.setChecked(True)
        self.chk_strip_group.toggled.connect(self._on_inputs_changed)
        h_clean.addWidget(self.chk_strip_group)
        h_clean.addStretch()
        p_lay.addLayout(h_clean)

        layout.addWidget(pat_box)

        layout.addWidget(QLabel(t("renamer_preview_lbl")))
        self.table_preview = QTableWidget()
        self.table_preview.setColumnCount(3)
        self.table_preview.setHorizontalHeaderLabels([t("renamer_col_dir"), t("renamer_col_orig"), t("renamer_col_new")])
        self.table_preview.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_preview.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.table_preview.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table_preview.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table_preview)

        btn_bar = QHBoxLayout()
        self.lbl_stats = QLabel(t("renamer_ready_stats").format(0))
        self.lbl_stats.setStyleSheet("color: #888; font-weight: bold;")
        btn_bar.addWidget(self.lbl_stats)
        btn_bar.addStretch()

        self.btn_apply = QPushButton(t("renamer_apply_btn"))
        self.btn_apply.setFixedHeight(40)
        self.btn_apply.setStyleSheet("background-color: #00a4dc; color: white; font-weight: bold; padding: 0 30px; font-size: 14px; border: none; border-radius: 6px;")
        self.btn_apply.clicked.connect(self.execute_rename)
        btn_bar.addWidget(self.btn_apply)
        layout.addLayout(btn_bar)

    def _insert_chip_tag(self, tag_str: str):
        line = self.combo_pattern.lineEdit()
        if line:
            line.insert(tag_str)
        else:
            self.combo_pattern.setCurrentText(self.combo_pattern.currentText() + tag_str)

    def select_folder(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, t("select_folder"))
        if folder: line_edit.setText(folder)

    def browse_batch_folders(self):
        d = QFileDialog(self, t("select_folder"))
        d.setFileMode(QFileDialog.Directory)
        d.setOption(QFileDialog.DontUseNativeDialog, True)
        d.setOption(QFileDialog.ShowDirsOnly, True)
        for v in d.findChildren(QAbstractItemView): v.setSelectionMode(QAbstractItemView.ExtendedSelection)
        if d.exec() == QFileDialog.Accepted:
            self.add_batch_folders_list([Path(p) for p in d.selectedFiles()])

    def add_batch_folders_list(self, paths):
        for p in paths:
            if not p.is_dir(): continue
            r = self.table_batch.rowCount()
            self.table_batch.insertRow(r)
            self.table_batch.setItem(r, 0, QTableWidgetItem(str(p)))
            self.table_batch.setItem(r, 1, QTableWidgetItem(p.name))
            sp = QSpinBox()
            sp.setRange(1, 99)
            sp.setValue(1)
            sp.valueChanged.connect(self._on_inputs_changed)
            self.table_batch.setCellWidget(r, 2, sp)
        self._on_inputs_changed()

    def _apply_pattern(self, pattern, title, season, meta, idx):
        ep_val = meta["ep"] if meta["ep"] is not None else (idx + 1)
        ext = meta["ext"]

        mapping = {
            "{title}": str(title),
            "{season}": str(season),
            "{season:02d}": f"{season:02d}",
            "{s}": str(season),
            "{s:02d}": f"{season:02d}",
            "{ep}": str(ep_val),
            "{ep:02d}": f"{ep_val:02d}",
            "{ep:03d}": f"{ep_val:03d}",
            "{resolution}": meta["res"],
            "{source}": meta["source"],
            "{codec}": meta["codec"],
            "{crc}": meta["crc"],
            "{orig}": meta["stem"],
            "{ext}": ext
        }

        output = pattern
        for k, v in mapping.items():
            output = output.replace(k, str(v))

        output = re.sub(r'\s+', ' ', output).strip()
        output = output.replace(" [ ]", "").replace(" []", "")
        if not output.endswith(ext):
            output += ext
        return output

    def _on_inputs_changed(self):
        pat = self.combo_pattern.currentText().strip()
        raw_exts = [e.strip().lower() for e in self.entry_exts.text().split(",") if e.strip()]
        exts = ['*'] if ('*' in raw_exts or not raw_exts) else [(e if e.startswith('.') else '.' + e) for e in raw_exts]

        tasks = []
        if self.radio_single.isChecked():
            folder_text = self.entry_folder.text().strip('"{}" ')
            if folder_text:
                f_p = Path(folder_text)
                if f_p.is_dir():
                    raw_title = self.entry_custom_title.text().strip() or f_p.name
                    if self.chk_strip_group.isChecked():
                        raw_title = re.sub(r'\[[^\]]*\]', '', raw_title).strip()
                    tasks.append({
                        'folder': f_p,
                        'title': raw_title,
                        'season': self.spin_season.value(),
                        'pattern': pat,
                        'exts': exts
                    })
        else:
            for r in range(self.table_batch.rowCount()):
                item_folder = self.table_batch.item(r, 0)
                item_title = self.table_batch.item(r, 1)
                widget_season = self.table_batch.cellWidget(r, 2)
                if item_folder:
                    f_p = Path(item_folder.text())
                    if f_p.is_dir():
                        raw_title = item_title.text().strip() if item_title else f_p.name
                        if self.chk_strip_group.isChecked():
                            raw_title = re.sub(r'\[[^\]]*\]', '', raw_title).strip()
                        s_val = widget_season.value() if widget_season else 1
                        tasks.append({
                            'folder': f_p,
                            'title': raw_title,
                            'season': s_val,
                            'pattern': pat,
                            'exts': exts
                        })

        all_entries = []
        for task in tasks:
            folder = task['folder']
            try:
                files = [f for f in folder.iterdir() if f.is_file() and ('*' in task['exts'] or f.suffix.lower() in task['exts'])]
                files = natsorted(files, key=lambda x: x.name)
                for idx, f in enumerate(files):
                    meta = parse_filename_metadata(f.name)
                    new_name = self._apply_pattern(task['pattern'], task['title'], task['season'], meta, idx)
                    all_entries.append((folder.name, f, new_name))
            except Exception:
                continue

        self.table_preview.setRowCount(len(all_entries))
        for row, (f_dir_name, orig_p, new_n) in enumerate(all_entries):
            item_dir = QTableWidgetItem(f_dir_name)
            item_dir.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            item_orig = QTableWidgetItem(orig_p.name)
            item_orig.setData(Qt.UserRole, orig_p)
            item_orig.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            item_new = QTableWidgetItem(new_n)
            item_new.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)

            self.table_preview.setItem(row, 0, item_dir)
            self.table_preview.setItem(row, 1, item_orig)
            self.table_preview.setItem(row, 2, item_new)

        self.lbl_stats.setText(t("renamer_ready_stats").format(len(all_entries)))

    def execute_rename(self):
        total_rows = self.table_preview.rowCount()
        if total_rows == 0:
            QMessageBox.warning(self, t("warning"), t("renamer_ready_stats").format(0))
            return

        reply = QMessageBox.question(
            self, t("renamer_confirm_title"),
            t("renamer_confirm_msg").format(total_rows),
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        success, errs = 0, []
        for r in range(total_rows):
            orig_p = self.table_preview.item(r, 1).data(Qt.UserRole)
            new_n = self.table_preview.item(r, 2).text().strip()
            if not new_n or orig_p.name == new_n:
                continue
            new_p = orig_p.with_name(new_n)
            try:
                orig_p.rename(new_p)
                success += 1
            except Exception as ex:
                errs.append(f"{orig_p.name}: {ex}")

        pat = self.combo_pattern.currentText().strip()
        save_config({"last_pattern": pat, "last_extensions": self.entry_exts.text()})
        self._on_inputs_changed()

        msg = t("renamer_success").format(success)
        if errs: msg += f"\n\nErrors ({len(errs)}):\n" + "\n".join(errs[:3])
        QMessageBox.information(self, t("completed"), msg)
