import os
import sys
import json
import subprocess
import shutil
import zipfile
import tempfile
import concurrent.futures
from datetime import datetime
from pathlib import Path
from natsort import natsorted

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QPushButton, QCheckBox, QProgressBar, QFileDialog, 
    QMessageBox, QDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QGroupBox
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from config_manager import load_config
from i18n import t
from tabs.SubSync.utils import read_sub_text

class DropLineEdit(QLineEdit):
    def __init__(self, expected_exts=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        self.expected_exts = expected_exts or []

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = Path(urls[0].toLocalFile())
            if path.is_file() and self.expected_exts and path.suffix.lower() not in self.expected_exts:
                QMessageBox.critical(self, t("error"), f"Invalid file type. Expected: {', '.join(self.expected_exts)}")
                return
            self.setText(str(path))

class RecentFilePickerDialog(QDialog):
    def __init__(self, root_folder, file_extensions, title="Select Recent Files", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(880, 600)
        self.root_folder = Path(root_folder)
        self.file_extensions = [e.lower() for e in file_extensions]
        self.selected_paths = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        h_info = QHBoxLayout()
        lbl_dir = QLabel(f"<b>Scanning Directory:</b> {self.root_folder} (Includes .zip archives)")
        lbl_dir.setStyleSheet("color: #00a4dc;")
        h_info.addWidget(lbl_dir)
        h_info.addStretch()
        layout.addLayout(h_info)

        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("Quick search / filter by name...")
        self.txt_filter.textChanged.connect(self._filter_table)
        layout.addWidget(self.txt_filter)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["File Name", "Relative Subfolder / Container", "Last Modified", "Size"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.table)

        btn_box = QHBoxLayout()
        lbl_hint = QLabel("💡 Tip: Select multiple files with Shift/Ctrl, or double-click to select.")
        lbl_hint.setStyleSheet("color: #888; font-size: 11px;")
        btn_box.addWidget(lbl_hint)
        btn_box.addStretch()

        btn_use_folder = QPushButton("Use Parent Folder")
        btn_use_folder.clicked.connect(self._on_use_folder)
        btn_box.addWidget(btn_use_folder)

        btn_select = QPushButton("Select File(s)")
        btn_select.setStyleSheet("background-color: #00a4dc; color: white; font-weight: bold; padding: 6px 16px;")
        btn_select.clicked.connect(self._on_confirm_selection)
        btn_box.addWidget(btn_select)

        layout.addLayout(btn_box)

        self.all_scanned_files = []
        self._scan_files()
        self.table.itemDoubleClicked.connect(lambda: self._on_confirm_selection())

    def _scan_files(self):
        if not self.root_folder.exists() or not self.root_folder.is_dir():
            return
        found = []
        try:
            for root, _, files in os.walk(self.root_folder):
                for f in files:
                    full_p = Path(root) / f
                    ext = full_p.suffix.lower()

                    if ext in self.file_extensions:
                        try:
                            stat = full_p.stat()
                            found.append((stat.st_mtime, stat.st_size, full_p, False, ""))
                        except Exception:
                            continue

                    elif ext == ".zip":
                        try:
                            stat = full_p.stat()
                            with zipfile.ZipFile(full_p, 'r') as zf:
                                for zinfo in zf.infolist():
                                    if not zinfo.is_dir():
                                        z_ext = os.path.splitext(zinfo.filename)[1].lower()
                                        if z_ext in self.file_extensions:
                                            found.append((stat.st_mtime, zinfo.file_size, full_p, True, zinfo.filename))
                        except Exception:
                            continue
        except Exception:
            pass

        found.sort(key=lambda x: x[0], reverse=True)
        self.all_scanned_files = found[:100]
        self._populate_table(self.all_scanned_files)

    def _populate_table(self, file_list):
        self.table.setRowCount(len(file_list))
        for row, (mtime, size, p, is_zip, internal_name) in enumerate(file_list):
            display_name = f"📦 [{p.name}] -> {internal_name}" if is_zip else p.name
            item_name = QTableWidgetItem(display_name)
            item_name.setData(Qt.UserRole, (str(p), is_zip, internal_name))

            try:
                rel_dir = str(p.parent.relative_to(self.root_folder))
                if rel_dir == ".": rel_dir = "[Root]"
            except Exception:
                rel_dir = str(p.parent.name)

            if is_zip:
                rel_dir += f" / {p.name}"

            item_subfolder = QTableWidgetItem(rel_dir)
            time_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            item_time = QTableWidgetItem(time_str)
            size_mb = size / (1024 * 1024)
            size_str = f"{size_mb:.2f} MB" if size_mb >= 1.0 else f"{size / 1024:.1f} KB"
            item_size = QTableWidgetItem(size_str)

            self.table.setItem(row, 0, item_name)
            self.table.setItem(row, 1, item_subfolder)
            self.table.setItem(row, 2, item_time)
            self.table.setItem(row, 3, item_size)

    def _filter_table(self, query):
        q = query.strip().lower()
        if not q:
            self._populate_table(self.all_scanned_files)
            return
        filtered = [f for f in self.all_scanned_files if q in f[2].name.lower() or q in f[4].lower() or q in str(f[2]).lower()]
        self._populate_table(filtered)

    def _on_confirm_selection(self):
        selected_rows = set(index.row() for index in self.table.selectedIndexes())
        self.selected_paths = []
        for r in sorted(selected_rows):
            item = self.table.item(r, 0)
            if item:
                path_str, is_zip, internal_name = item.data(Qt.UserRole)
                if is_zip:
                    try:
                        temp_dir = Path(tempfile.gettempdir()) / "SubSync_Zip_Extracted"
                        temp_dir.mkdir(parents=True, exist_ok=True)
                        with zipfile.ZipFile(path_str, 'r') as zf:
                            extracted_path = zf.extract(internal_name, path=temp_dir)
                            self.selected_paths.append(str(Path(extracted_path).resolve()))
                    except Exception as e:
                        QMessageBox.warning(self, "Zip Error", f"Failed extracting {internal_name}: {e}")
                else:
                    self.selected_paths.append(path_str)

        if self.selected_paths:
            self.accept()
        else:
            QMessageBox.information(self, t("notice"), "Please select at least one file from the list.")

    def _on_use_folder(self):
        selected_rows = [index.row() for index in self.table.selectedIndexes()]
        if selected_rows:
            data = self.table.item(selected_rows[0], 0).data(Qt.UserRole)
            p = Path(data[0])
            self.selected_paths = [str(p.parent)]
        else:
            self.selected_paths = [str(self.root_folder)]
        self.accept()

class ProcessWorker(QThread):
    progress = Signal(int)
    finished = Signal(int, list, list)

    def __init__(self, paired_files, embed_subs, remove_subs):
        super().__init__()
        self.paired_files = paired_files
        self.embed_subs = embed_subs
        self.remove_subs = remove_subs
        self.has_mkvmerge = bool(shutil.which("mkvmerge"))

    def _process_single_pair(self, t_path, v_path, creationflags):
        if not v_path:
            return True, None, None

        temp_video = v_path.with_name(v_path.stem + f"_tmp_{os.getpid()}_{id(v_path)}" + v_path.suffix)
        try:
            if self.embed_subs and t_path:
                vid_ext = v_path.suffix.lower()
                sub_ext = t_path.suffix.lower()

                sub_text = read_sub_text(t_path)
                t_path_utf8 = t_path.with_name(t_path.stem + f"_temp_utf8_{os.getpid()}_{id(t_path)}" + t_path.suffix)
                with open(t_path_utf8, 'w', encoding='utf-8') as f:
                    f.write(sub_text)

                try:
                    if vid_ext == '.mkv' and self.has_mkvmerge:
                        info_cmd = ['mkvmerge', '-J', str(v_path)]
                        r_info = subprocess.run(info_cmd, capture_output=True, text=True, creationflags=creationflags)
                        info = json.loads(r_info.stdout) if (r_info.returncode in (0, 1) and r_info.stdout) else {}

                        cmd = ['mkvmerge', '-o', str(temp_video)]
                        for t_item in info.get('tracks', []):
                            if t_item.get('type') == 'subtitles':
                                cmd.extend(['--default-track-flag', f"{t_item['id']}:0"])

                        cmd.extend([
                            str(v_path),
                            '--language', '0:ara',
                            '--track-name', '0:Arabic',
                            '--default-track-flag', '0:1',
                            '--sub-charset', '0:UTF-8',
                            str(t_path_utf8)
                        ])

                        res = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags)
                        if res.returncode not in (0, 1):
                            err = res.stderr.strip() or res.stdout.strip()
                            if temp_video.exists(): temp_video.unlink()
                            return False, None, f"[{v_path.name}] mkvmerge Failed:\n{err}"
                        else:
                            if temp_video.exists():
                                os.replace(temp_video, v_path)
                            return True, None, None

                    cmd = [
                        'ffmpeg', '-y',
                        '-i', str(v_path),
                        '-i', str(t_path_utf8),
                        '-map', '0:v',
                        '-map', '0:a?',
                        '-map', '1:0',
                        '-map', '0:s?',
                        '-map', '0:t?',
                        '-map', '1:t?',
                        '-c', 'copy',
                        '-max_interleave_delta', '0'
                    ]

                    if vid_ext == '.mp4':
                        cmd.extend(['-c:s', 'mov_text'])
                    else:
                        if sub_ext == '.ass':
                            cmd.extend(['-c:s:0', 'ass'])
                        elif sub_ext == '.srt':
                            cmd.extend(['-c:s:0', 'srt'])

                    cmd.extend([
                        '-disposition:s', '-default',
                        '-disposition:s:0', 'default',
                        '-metadata:s:s:0', 'language=ara',
                        '-metadata:s:s:0', 'title=Arabic',
                        str(temp_video)
                    ])

                    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags)
                    if result.returncode != 0:
                        err = result.stderr.strip()
                        short_err = "\n".join(err.split('\n')[-6:])
                        if temp_video.exists(): temp_video.unlink()
                        return False, None, f"[{v_path.name}] FFmpeg Failed:\n{short_err}"
                    else:
                        if temp_video.exists():
                            os.replace(temp_video, v_path)

                finally:
                    if t_path_utf8.exists():
                        t_path_utf8.unlink()

            elif self.remove_subs:
                cmd = ['ffmpeg', '-y', '-i', str(v_path), '-c', 'copy', '-sn', str(temp_video)]
                result = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags)
                if result.returncode != 0:
                    err = result.stderr.strip()
                    short_err = "\n".join(err.split('\n')[-6:])
                    if temp_video.exists(): temp_video.unlink()
                    return False, None, f"[{v_path.name}] FFmpeg Failed:\n{short_err}"
                else:
                    if temp_video.exists():
                        os.replace(temp_video, v_path)

            if not self.embed_subs and not self.remove_subs and t_path:
                new_tran_name = v_path.parent / (v_path.stem + ".ar" + t_path.suffix)
                if not new_tran_name.exists():
                    shutil.copy2(t_path, new_tran_name)
                    if sys.platform == 'win32':
                        import ctypes
                        ctypes.windll.kernel32.SetFileAttributesW(str(new_tran_name), 0x02)

            return True, None, None

        except FileNotFoundError:
            return False, None, "Required tools (FFmpeg / mkvmerge) are missing from PATH!"
        except PermissionError:
            if temp_video.exists(): temp_video.unlink()
            return False, v_path.name, None
        except Exception as e:
            if temp_video.exists(): temp_video.unlink()
            return False, None, f"[{v_path.name}] Error: {str(e)}"

    def run(self):
        valid_pairs = [(t, v) for t, v in self.paired_files if v]
        total = len(valid_pairs)
        if total == 0:
            self.finished.emit(0, [], [])
            return

        processed_count = 0
        locked_files = []
        error_logs = []
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

        max_workers = min(4, max(1, os.cpu_count() or 2))

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_pair = {
                executor.submit(self._process_single_pair, t_p, v_p, creationflags): (t_p, v_p)
                for t_p, v_p in valid_pairs
            }
            for fut in concurrent.futures.as_completed(future_to_pair):
                success, locked, err = fut.result()
                if success:
                    processed_count += 1
                else:
                    if locked:
                        locked_files.append(locked)
                    if err:
                        error_logs.append(err)
                self.progress.emit(int((processed_count + len(locked_files) + len(error_logs)) / total * 100))

        self.finished.emit(processed_count, locked_files, error_logs)

class InteractivePreviewDialog(QDialog):
    edit_in_studio_clicked = Signal(str, str)

    def __init__(self, video_files, tran_files, action_msg, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("smart_match"))
        self.resize(1050, 580)
        self.confirmed = False
        self.final_pairs = []

        layout = QVBoxLayout(self)
        lbl_info = QLabel("Review and adjust pairings. Select subtitle and use Up/Down to realign, or click 'Edit in Studio' to fine-tune.")
        lbl_info.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(lbl_info)

        lbl_action = QLabel(action_msg)
        lbl_action.setStyleSheet("color: #ff8888; font-size: 13px; margin-bottom: 5px;")
        layout.addWidget(lbl_action)

        ctrl_layout = QHBoxLayout()
        btn_up = QPushButton("Move Subtitle Up")
        btn_down = QPushButton("Move Subtitle Down")
        btn_studio = QPushButton("🎙️ Edit Selected in Studio")
        btn_studio.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        
        btn_up.clicked.connect(self.move_up)
        btn_down.clicked.connect(self.move_down)
        btn_studio.clicked.connect(self.edit_selected_in_studio)

        ctrl_layout.addWidget(btn_up)
        ctrl_layout.addWidget(btn_down)
        ctrl_layout.addWidget(btn_studio)
        ctrl_layout.addStretch()
        layout.addLayout(ctrl_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Video File (Fixed)", "Subtitle File (Adjustable)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectItems)
        self.table.setSelectionMode(QTableWidget.SingleSelection)

        self.table.setRowCount(len(video_files))
        for row, (v_path, t_path) in enumerate(zip(video_files, tran_files)):
            v_item = QTableWidgetItem(v_path.name if v_path else "[No Video]")
            if v_path: v_item.setData(Qt.UserRole, v_path)
            else: v_item.setFlags(Qt.NoItemFlags)
            self.table.setItem(row, 0, v_item)

            t_item = QTableWidgetItem(t_path.name if t_path else "[No Subtitle]")
            t_item.setData(Qt.UserRole, t_path if t_path else None)
            self.table.setItem(row, 1, t_item)

        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        btn_confirm = QPushButton(t("confirm_proceed"))
        btn_confirm.setStyleSheet("background-color: #00a4dc; color: white; font-weight: bold; padding: 8px;")
        btn_confirm.clicked.connect(self.accept_action)
        btn_cancel = QPushButton(t("cancel"))
        btn_cancel.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_confirm)
        layout.addLayout(btn_layout)

    def move_up(self):
        row = self.table.currentRow()
        col = self.table.currentColumn()
        if row > 0 and col == 1:
            cur = self.table.takeItem(row, 1)
            above = self.table.takeItem(row - 1, 1)
            if cur: self.table.setItem(row - 1, 1, cur)
            if above: self.table.setItem(row, 1, above)
            self.table.setCurrentCell(row - 1, 1)

    def move_down(self):
        row = self.table.currentRow()
        col = self.table.currentColumn()
        if row >= 0 and row < self.table.rowCount() - 1 and col == 1:
            cur = self.table.takeItem(row, 1)
            below = self.table.takeItem(row + 1, 1)
            if cur: self.table.setItem(row + 1, 1, cur)
            if below: self.table.setItem(row, 1, below)
            self.table.setCurrentCell(row + 1, 1)

    def edit_selected_in_studio(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Notice", "Please select a row first.")
            return
        v_item = self.table.item(row, 0)
        t_item = self.table.item(row, 1)
        v_p = str(v_item.data(Qt.UserRole)) if v_item and v_item.data(Qt.UserRole) else ""
        t_p = str(t_item.data(Qt.UserRole)) if t_item and t_item.data(Qt.UserRole) else ""
        
        if not t_p:
            QMessageBox.warning(self, "Warning", "This row has no subtitle file assigned.")
            return
            
        self.edit_in_studio_clicked.emit(v_p, t_p)
        self.reject()

    def accept_action(self):
        self.final_pairs = []
        for row in range(self.table.rowCount()):
            v_item = self.table.item(row, 0)
            t_item = self.table.item(row, 1)
            v_p = v_item.data(Qt.UserRole) if v_item else None
            t_p = t_item.data(Qt.UserRole) if t_item else None
            if v_p:
                self.final_pairs.append((t_p, v_p))
        if not self.final_pairs:
            QMessageBox.warning(self, t("warning"), "No files to process.")
            return
        self.confirmed = True
        self.accept()

class MergerTab(QWidget):
    edit_in_studio_requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = load_config()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        h_split = QHBoxLayout()
        h_split.setSpacing(15)

        grp_subs = QGroupBox(t("merger_subs_grp"))
        grp_subs.setStyleSheet("QGroupBox { font-weight: bold; color: #2ecc71; border: 1px solid rgba(46, 204, 113, 0.3); border-radius: 8px; padding-top: 15px; }")
        l_subs = QVBoxLayout(grp_subs)
        l_subs.setSpacing(10)

        l_subs.addWidget(QLabel(t("merger_drop_sub_lbl")))
        self.entry_tran = DropLineEdit(['.srt', '.ass'])
        self.entry_tran.setMinimumHeight(35)
        l_subs.addWidget(self.entry_tran)

        btn_box_subs = QHBoxLayout()
        btn_sel_tran_file = QPushButton(t("select_file"))
        btn_sel_tran_file.clicked.connect(lambda: self.select_file(self.entry_tran, "Subtitles (*.srt *.ass)"))
        btn_sel_tran_folder = QPushButton(t("select_folder"))
        btn_sel_tran_folder.clicked.connect(lambda: self.select_folder(self.entry_tran))
        btn_watch_subs = QPushButton(t("watchdog_scan"))
        btn_watch_subs.setStyleSheet("background-color: rgba(46, 204, 113, 0.15); color: #2ecc71; font-weight: bold;")
        btn_watch_subs.clicked.connect(lambda: self.open_watchdog_picker("subs"))

        btn_box_subs.addWidget(btn_sel_tran_file)
        btn_box_subs.addWidget(btn_sel_tran_folder)
        btn_box_subs.addWidget(btn_watch_subs)
        l_subs.addLayout(btn_box_subs)
        l_subs.addStretch()

        grp_vids = QGroupBox(t("merger_vids_grp"))
        grp_vids.setStyleSheet("QGroupBox { font-weight: bold; color: #00a4dc; border: 1px solid rgba(0, 164, 220, 0.3); border-radius: 8px; padding-top: 15px; }")
        l_vids = QVBoxLayout(grp_vids)
        l_vids.setSpacing(10)

        l_vids.addWidget(QLabel(t("merger_drop_vid_lbl")))
        self.entry_video = DropLineEdit(['.mp4', '.mkv'])
        self.entry_video.setMinimumHeight(35)
        l_vids.addWidget(self.entry_video)

        btn_box_vids = QHBoxLayout()
        btn_sel_video_file = QPushButton(t("select_file"))
        btn_sel_video_file.clicked.connect(lambda: self.select_file(self.entry_video, "Videos (*.mp4 *.mkv)"))
        btn_sel_video_folder = QPushButton(t("select_folder"))
        btn_sel_video_folder.clicked.connect(lambda: self.select_folder(self.entry_video))
        btn_watch_vids = QPushButton(t("watchdog_scan"))
        btn_watch_vids.setStyleSheet("background-color: rgba(0, 164, 220, 0.15); color: #00a4dc; font-weight: bold;")
        btn_watch_vids.clicked.connect(lambda: self.open_watchdog_picker("videos"))

        btn_box_vids.addWidget(btn_sel_video_file)
        btn_box_vids.addWidget(btn_sel_video_folder)
        btn_box_vids.addWidget(btn_watch_vids)
        l_vids.addLayout(btn_box_vids)
        l_vids.addStretch()

        h_split.addWidget(grp_subs, 1)
        h_split.addWidget(grp_vids, 1)
        layout.addLayout(h_split)

        self.chk_embed_subs = QCheckBox(t("embed_subs"))
        self.chk_embed_subs.setChecked(False)
        self.chk_remove_subs = QCheckBox(t("remove_subs"))
        self.chk_embed_subs.toggled.connect(self.on_embed_toggle)

        layout.addWidget(self.chk_embed_subs)
        layout.addWidget(self.chk_remove_subs)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(20)
        self.progress_bar.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.progress_bar)

        self.btn_start = QPushButton(t("smart_match"))
        self.btn_start.setMinimumHeight(45)
        self.btn_start.setStyleSheet("background-color: #00a4dc; font-size: 14px; font-weight: bold; color: white;")
        self.btn_start.clicked.connect(self.start_process)
        layout.addWidget(self.btn_start)
        layout.addStretch()

    def open_watchdog_picker(self, target_type):
        self.config = load_config()
        cfg_key = "watchdog_subs_dir" if target_type == "subs" else "watchdog_videos_dir"
        watch_dir = self.config.get(cfg_key, "")

        if not watch_dir or not os.path.exists(watch_dir):
            reply = QMessageBox.question(
                self, t("notice"),
                f"Watchdog folder path for ({target_type}) is not configured.\nWould you like to select the directory now?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                chosen = QFileDialog.getExistingDirectory(self, t("select_folder"))
                if chosen:
                    self.config[cfg_key] = os.path.normpath(chosen)
                    from config_manager import save_config
                    save_config(self.config)
                    watch_dir = chosen
                else: return
            else: return

        exts = [".srt", ".ass", ".ssa", ".vtt"] if target_type == "subs" else [".mp4", ".mkv", ".avi", ".mov", ".webm"]
        dlg = RecentFilePickerDialog(root_folder=watch_dir, file_extensions=exts, title=f"Latest 100 Files ({target_type.upper()})", parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.selected_paths:
            target_entry = self.entry_tran if target_type == "subs" else self.entry_video
            if len(dlg.selected_paths) == 1:
                target_entry.setText(dlg.selected_paths[0])
            else:
                target_entry.setText(str(Path(dlg.selected_paths[0]).parent))

    def on_embed_toggle(self, checked):
        if checked:
            self.chk_remove_subs.setChecked(False)
            self.chk_remove_subs.setEnabled(False)
        else:
            self.chk_remove_subs.setEnabled(True)

    def select_file(self, line_edit, filter_str):
        file, _ = QFileDialog.getOpenFileName(self, t("select_file"), "", filter_str)
        if file: line_edit.setText(file)

    def select_folder(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, t("select_folder"))
        if folder: line_edit.setText(folder)

    def start_process(self):
        embed_subs = self.chk_embed_subs.isChecked()
        remove_subs = self.chk_remove_subs.isChecked()
        tran_input = self.entry_tran.text().strip('"{}" ')
        video_input = self.entry_video.text().strip('"{}" ')

        if not video_input:
            QMessageBox.critical(self, t("error"), t("merger_no_vids_path"))
            return

        video_path = Path(video_input)
        if not video_path.exists():
            QMessageBox.critical(self, t("error"), t("merger_no_vids_path"))
            return

        aligned_videos, aligned_subs = [], []

        if remove_subs and not embed_subs:
            video_files = [video_path] if video_path.is_file() else natsorted([f for ext in ("*.mp4", "*.mkv") for f in video_path.glob(ext)])
            if not video_files:
                QMessageBox.warning(self, t("warning"), t("merger_no_vids"))
                return
            aligned_videos = video_files
            aligned_subs = [None] * len(video_files)
            action_msg = t("merger_action_remove")
        else:
            if not tran_input:
                QMessageBox.critical(self, t("error"), t("merger_no_subs"))
                return
            tran_path = Path(tran_input)
            if not tran_path.exists():
                QMessageBox.critical(self, t("error"), t("merger_no_subs"))
                return

            if tran_path.is_file() and video_path.is_file():
                aligned_videos.append(video_path)
                aligned_subs.append(tran_path)
            elif tran_path.is_dir() and video_path.is_dir():
                tran_files = natsorted([f for ext in ("*.srt", "*.ass") for f in tran_path.glob(ext)])
                video_files = natsorted([f for ext in ("*.mp4", "*.mkv") for f in video_path.glob(ext)])

                from tabs.renamer_tab import get_episode_number
                video_dict = {get_episode_number(v.name): v for v in video_files if get_episode_number(v.name) is not None}
                tran_dict = {get_episode_number(t.name): t for t in tran_files if get_episode_number(t.name) is not None}

                matched_v, matched_t = [], []
                for ep_num, v_p in video_dict.items():
                    if ep_num in tran_dict:
                        matched_v.append(v_p)
                        matched_t.append(tran_dict[ep_num])

                unmatched_v = [v for v in video_files if v not in matched_v]
                unmatched_t = [t for t in tran_files if t not in matched_t]

                aligned_videos = matched_v + unmatched_v + [None] * len(unmatched_t)
                aligned_subs = matched_t + [None] * len(unmatched_v) + unmatched_t
            else:
                QMessageBox.critical(self, t("error"), t("merger_mismatch"))
                return

            action_msg = t("merger_action_embed") if embed_subs else t("merger_action_copy")

        dlg = InteractivePreviewDialog(aligned_videos, aligned_subs, action_msg, self)
        dlg.edit_in_studio_clicked.connect(self.edit_in_studio_requested.emit)
        if dlg.exec() == QDialog.Rejected or not dlg.confirmed:
            return

        self.btn_start.setEnabled(False)
        self.progress_bar.setValue(0)
        self.worker = ProcessWorker(dlg.final_pairs, embed_subs, remove_subs)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.finished.connect(self.process_finished)
        self.worker.start()

    def process_finished(self, count, locked_files, error_logs):
        self.btn_start.setEnabled(True)
        self.progress_bar.setValue(100)
        if error_logs or locked_files:
            msg = f"Processed {count} file(s) with notices:\n\n"
            if error_logs: msg += "Errors:\n" + "\n".join(error_logs[:3])
            QMessageBox.warning(self, t("warning"), msg)
        else:
            QMessageBox.information(self, t("completed"), f"Successfully processed {count} file(s)!")
        self.progress_bar.setValue(0)
