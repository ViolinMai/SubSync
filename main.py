import sys
import os
from pathlib import Path
from natsort import natsorted

PROJECT_ROOT = str(Path(__file__).resolve().parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ["PATH"] = PROJECT_ROOT + os.pathsep + os.environ.get("PATH", "")
if sys.platform == 'win32' and hasattr(os, "add_dll_directory"):
    try:
        os.add_dll_directory(PROJECT_ROOT)
    except Exception:
        pass
    for sub in [Path(PROJECT_ROOT) / "installer_output", Path(PROJECT_ROOT) / "libs"]:
        if sub.exists():
            try:
                os.add_dll_directory(str(sub))
                os.environ["PATH"] = str(sub) + os.pathsep + os.environ["PATH"]
            except Exception:
                pass

if sys.platform.startswith("linux"):
    if "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"

    from config_manager import load_config as _preload_config
    _boot_config = _preload_config()
    _saved_scale = _boot_config.get("linux_scale_factor", "Auto / Native")

    scale_map = {
        "1.0x (100%)": "1.0",
        "1.25x (125%)": "1.25",
        "1.5x (150%)": "1.5",
        "1.75x (175%)": "1.75",
        "2.0x (200%)": "2.0"
    }

    if _saved_scale in scale_map:
        os.environ["QT_SCALE_FACTOR"] = scale_map[_saved_scale]
        os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
    else:
        os.environ.pop("QT_SCALE_FACTOR", None)
        os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QMessageBox, QProgressBar, QVBoxLayout, QWidget
from PySide6.QtCore import Qt, QByteArray, QSize, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QCloseEvent, QSurfaceFormat

from ui_theme import APP_STYLESHEET
from config_manager import load_config, save_config
from i18n import t, set_language
from tabs.SubSync import SubSyncTab
from tabs.merger_tab import MergerTab
from tabs.renamer_tab import RenamerTab
from tabs.settings_tab import SettingsTab
from tabs.mediainfo_tab import MediaInfoTab

class MainMediaSuiteWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        set_language(self.config.get("language", "ar"))
        
        self.setWindowTitle(t("app_title"))
        self.setMinimumSize(QSize(400, 300))
        self.resize(1280, 750)
        self.setAcceptDrops(True)

        is_wayland = sys.platform.startswith("linux") and (
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or bool(os.environ.get("WAYLAND_DISPLAY"))
        )
        geom_hex = self.config.get("window_geometry", "")
        if geom_hex and self.config.get("remember_geometry", True):
            try:
                self.restoreGeometry(QByteArray.fromHex(geom_hex.encode("utf-8")))
            except Exception:
                pass
        elif not is_wayland:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(
                (screen.width() - self.width()) // 2,
                (screen.height() - self.height()) // 2
            )

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_box_layout = QVBoxLayout(central_widget)
        main_box_layout.setContentsMargins(0, 0, 0, 0)
        main_box_layout.setSpacing(0)

        self.global_progress = QProgressBar(self)
        self.global_progress.setFixedHeight(3)
        self.global_progress.setTextVisible(False)
        self.global_progress.setStyleSheet("""
            QProgressBar {
                background-color: transparent;
                border: none;
            }
            QProgressBar::chunk {
                background-color: #00a4dc;
                border-radius: 1px;
            }
        """)
        self.global_progress.hide()
        main_box_layout.addWidget(self.global_progress)

        self.tabs = QTabWidget(self)
        main_box_layout.addWidget(self.tabs)

        self.subsync_tab = SubSyncTab(self)
        self.subsync_tab.switch_to_merger_requested.connect(self.jump_to_merger_with_files)
        self.subsync_tab.global_progress_updated.connect(self.update_global_progress)
        self.tabs.addTab(self.subsync_tab, t("tab_subsync"))

        self.merger_tab = MergerTab(self)
        self.merger_tab.edit_in_studio_requested.connect(self.jump_to_studio_with_file)
        self.tabs.addTab(self.merger_tab, t("tab_merger"))

        self.renamer_tab = RenamerTab(self)
        self.tabs.addTab(self.renamer_tab, t("tab_renamer"))

        self.mediainfo_tab = MediaInfoTab(main_window=self, parent=self)
        self.tabs.addTab(self.mediainfo_tab, "🔍 MediaInfo")

        self.settings_tab = SettingsTab(self)
        self.settings_tab.settings_saved.connect(self._on_settings_saved)
        self.tabs.addTab(self.settings_tab, t("tab_settings"))

        default_index = self.config.get("startup_tab", 0)
        if 0 <= default_index < self.tabs.count():
            self.tabs.setCurrentIndex(default_index)

        self.setStyleSheet(APP_STYLESHEET)
        QTimer.singleShot(150, self.handle_sys_args)

    def update_global_progress(self, current: int, total: int):
        if total <= 0 or current >= total:
            self.global_progress.setValue(100)
            QTimer.singleShot(400, self.global_progress.hide)
        else:
            pct = int((current / total) * 100)
            self.global_progress.setValue(pct)
            if not self.global_progress.isVisible():
                self.global_progress.show()

    def jump_to_merger_with_files(self, video_path, sub_path):
        if sub_path:
            self.merger_tab.entry_tran.setText(str(sub_path))
        if video_path:
            self.merger_tab.entry_video.setText(str(video_path))
        self.tabs.setCurrentIndex(1)

    def jump_to_studio_with_file(self, video_path, sub_path):
        if video_path and os.path.exists(video_path):
            self.subsync_tab.load_video(str(video_path))
        if sub_path and os.path.exists(sub_path):
            self.subsync_tab.load_subtitles(str(sub_path))
        self.tabs.setCurrentIndex(0)

    def _on_settings_saved(self, new_cfg):
        self.config = new_cfg
        set_language(new_cfg.get("language", "ar"))
        self.setWindowTitle(t("app_title"))
        self.tabs.setTabText(0, t("tab_subsync"))
        self.tabs.setTabText(1, t("tab_merger"))
        self.tabs.setTabText(2, t("tab_renamer"))
        self.tabs.setTabText(3, "🔍 MediaInfo")
        self.tabs.setTabText(4, t("tab_settings"))

        if hasattr(self.subsync_tab, "encoding_combo"):
            self.subsync_tab.encoding_combo.setCurrentText(new_cfg.get("default_encoding", "utf-8-sig"))
            self.subsync_tab.chk_keep_styles.setChecked(new_cfg.get("keep_styles_default", True))
            self.subsync_tab.chk_auto_save.setChecked(new_cfg.get("auto_save_enabled", False))

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            f_path = url.toLocalFile()
            ext = Path(f_path).suffix.lower()
            if ext in [".mp4", ".mkv", ".avi", ".mov", ".webm"]:
                self.subsync_tab.load_video(f_path)
                self.tabs.setCurrentIndex(0)
            elif ext in [".srt", ".ass", ".ssa", ".vtt"]:
                self.subsync_tab.load_subtitles(f_path)
                self.tabs.setCurrentIndex(0)

    def find_matching_subtitle(self, video_path: Path) -> str:
        folder = video_path.parent
        stem = video_path.stem
        sub_exts = ['.ass', '.srt', '.ssa', '.vtt']

        for ext in sub_exts:
            exact = folder / f"{stem}{ext}"
            if exact.exists(): return str(exact)
            exact_ar = folder / f"{stem}.ar{ext}"
            if exact_ar.exists(): return str(exact_ar)
            exact_ara = folder / f"{stem}.ara{ext}"
            if exact_ara.exists(): return str(exact_ara)

        from tabs.renamer_tab import get_episode_number
        ep_num = get_episode_number(video_path.name)
        if ep_num is not None:
            all_subs = [f for ext in sub_exts for f in folder.glob(f"*{ext}")]
            for s in all_subs:
                if get_episode_number(s.name) == ep_num:
                    return str(s)

        return ""

    def handle_sys_args(self):
        args = sys.argv[1:]
        if not args:
            return

        target_tab = None
        target_path = None

        idx = 0
        while idx < len(args):
            arg = args[idx]
            if arg == "--tab" and idx + 1 < len(args):
                target_tab = args[idx + 1].lower()
                idx += 2
            elif not arg.startswith("--"):
                target_path = Path(arg.strip('"{}" '))
                idx += 1
            else:
                idx += 1

        if not target_path or not target_path.exists():
            return

        video_exts = ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.m4v', '.ts']
        sub_exts = ['.srt', '.ass', '.ssa', '.vtt']

        # توجيه صريح حسب الـ Flag الممرر
        if target_tab == "mediainfo":
            self.tabs.setCurrentIndex(3)
            self.mediainfo_tab.inspect_file(str(target_path))
            return

        elif target_tab == "merger":
            self.tabs.setCurrentIndex(1)
            if target_path.is_file():
                if target_path.suffix.lower() in video_exts:
                    self.merger_tab.entry_video.setText(str(target_path))
                elif target_path.suffix.lower() in sub_exts:
                    self.merger_tab.entry_tran.setText(str(target_path))
            else:
                self.merger_tab.entry_video.setText(str(target_path))
                self.merger_tab.entry_tran.setText(str(target_path))
            return

        elif target_tab == "renamer":
            self.tabs.setCurrentIndex(2)
            if target_path.is_file():
                self.renamer_tab.entry_folder.setText(str(target_path.parent))
            else:
                self.renamer_tab.entry_folder.setText(str(target_path))
            return

        elif target_tab == "studio":
            self.tabs.setCurrentIndex(0)
            if target_path.is_file():
                if target_path.suffix.lower() in video_exts:
                    self.subsync_tab.load_video(str(target_path))
                    matched = self.find_matching_subtitle(target_path)
                    if matched: self.subsync_tab.load_subtitles(matched)
                elif target_path.suffix.lower() in sub_exts:
                    self.subsync_tab.load_subtitles(str(target_path))
            return

        # توجيه تلقائي ذكي افتراضي في حال لم يُمرر Flag
        if target_path.is_file():
            ext = target_path.suffix.lower()
            if ext in video_exts:
                self.tabs.setCurrentIndex(0)
                self.subsync_tab.load_video(str(target_path))
                matched_sub = self.find_matching_subtitle(target_path)
                if matched_sub:
                    self.subsync_tab.load_subtitles(matched_sub)
            elif ext in sub_exts:
                self.tabs.setCurrentIndex(0)
                self.subsync_tab.load_subtitles(str(target_path))

        elif target_path.is_dir():
            vids = natsorted([f for ext in video_exts for f in target_path.glob(f"*{ext}")])
            subs = natsorted([f for ext in sub_exts for f in target_path.glob(f"*{ext}")])

            if vids and subs:
                self.tabs.setCurrentIndex(1)
                self.merger_tab.entry_video.setText(str(target_path))
                self.merger_tab.entry_tran.setText(str(target_path))
                self.subsync_tab.load_video(str(vids[0]))
                self.subsync_tab.load_subtitles(str(subs[0]))
            elif vids and not subs:
                self.tabs.setCurrentIndex(1)
                self.merger_tab.entry_video.setText(str(target_path))
                self.subsync_tab.load_video(str(vids[0]))
            elif subs and not vids:
                self.tabs.setCurrentIndex(0)
                self.merger_tab.entry_tran.setText(str(target_path))
                self.subsync_tab.load_subtitles(str(subs[0]))
                QTimer.singleShot(300, lambda: self.subsync_tab._open_batch_dialog())
            else:
                self.tabs.setCurrentIndex(0)
                QTimer.singleShot(300, lambda: self.subsync_tab._open_batch_dialog())

    def closeEvent(self, event: QCloseEvent):
        if hasattr(self, 'subsync_tab'):
            if not self.subsync_tab.prompt_unsaved_changes():
                event.ignore()
                return

        self.config["window_geometry"] = self.saveGeometry().toHex().data().decode("utf-8")
        save_config(self.config)

        if hasattr(self, 'subsync_tab'):
            self.subsync_tab.cleanup()
        event.accept()

if __name__ == "__main__":
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    _fmt = QSurfaceFormat()
    _fmt.setDepthBufferSize(0)
    _fmt.setStencilBufferSize(0)
    _fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(_fmt)

    app = QApplication(sys.argv)
    app.setApplicationName("SubSync")
    app.setDesktopFileName("SubSync")
    window = MainMediaSuiteWindow()
    window.show()
    sys.exit(app.exec())
