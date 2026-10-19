import shutil
import os
import sys
from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QComboBox, QCheckBox, QSpinBox, QPushButton, 
    QFrame, QMessageBox, QLineEdit, QFileDialog, QScrollArea
)
from PySide6.QtCore import Qt, Signal, QProcess
from config_manager import load_config, save_config
from i18n import t

def register_context_menu():
    if sys.platform != 'win32':
        return False, "Context menu integration is currently supported on Windows only."
    try:
        import winreg
        executable_path = sys.executable
        # إذا كان بايثون مشغلاً عبر ملف سكربت أو مجمع كـ exe
        script_path = os.path.abspath(sys.argv[0])
        base_cmd = f'"{executable_path}" "{script_path}"' if script_path.endswith('.py') else f'"{executable_path}"'

        sub_actions = [
            ("Studio", "🎙️ Open in SubSync Studio", f'{base_cmd} --tab studio "%1"'),
            ("Merger", "🎬 Send to Subtitle Merger", f'{base_cmd} --tab merger "%1"'),
            ("Renamer", "🏷️ Open in Bulk Renamer", f'{base_cmd} --tab renamer "%1"'),
            ("MediaInfo", "🔍 Inspect with MediaInfo", f'{base_cmd} --tab mediainfo "%1"'),
        ]

        # التسجيل لكافة الملفات وللمجلدات والخلفيات
        target_roots = [r"*\shell\SubSync", r"Directory\shell\SubSync"]

        for root_path in target_roots:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root_path) as main_key:
                winreg.SetValueEx(main_key, "MUIVerb", 0, winreg.REG_SZ, "SubSync Suite")
                winreg.SetValueEx(main_key, "SubCommands", 0, winreg.REG_SZ, "")
                winreg.SetValueEx(main_key, "Icon", 0, winreg.REG_SZ, executable_path)

            shell_sub = root_path + r"\shell"
            for action_key, label, cmd_str in sub_actions:
                action_path = shell_sub + f"\\{action_key}"
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, action_path) as act_k:
                    winreg.SetValueEx(act_k, "MUIVerb", 0, winreg.REG_SZ, label)
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, action_path + r"\command") as cmd_k:
                    winreg.SetValue(cmd_k, "", winreg.REG_SZ, cmd_str)

        return True, "Successfully registered Cascading SubSync Context Menu!"
    except Exception as e:
        return False, f"Failed to register context menu: {e}"

def unregister_context_menu():
    if sys.platform != 'win32':
        return False, "Unsupported platform."
    try:
        import winreg
        def delete_key_tree(root, subkey):
            try:
                with winreg.OpenKey(root, subkey, 0, winreg.KEY_ALL_ACCESS) as hkey:
                    while True:
                        try:
                            sub = winreg.EnumKey(hkey, 0)
                            delete_key_tree(root, subkey + "\\" + sub)
                        except OSError:
                            break
                winreg.DeleteKey(root, subkey)
            except FileNotFoundError:
                pass

        delete_key_tree(winreg.HKEY_CURRENT_USER, r"*\shell\SubSync")
        delete_key_tree(winreg.HKEY_CURRENT_USER, r"Directory\shell\SubSync")
        return True, "Successfully removed SubSync from Windows Context Menu!"
    except Exception as e:
        return False, f"Failed to unregister context menu: {e}"

class SettingsTab(QWidget):
    settings_saved = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = load_config()
        self._init_ui()

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root_layout.addWidget(scroll)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(35, 25, 35, 35)
        layout.setSpacing(18)

        lbl_head = QLabel(t("preferences"))
        lbl_head.setStyleSheet("font-size: 22px; font-weight: 900; color: #00a4dc;")
        layout.addWidget(lbl_head)

        panel = QFrame(content)
        panel.setStyleSheet("background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 18px;")
        p_layout = QVBoxLayout(panel)
        p_layout.setSpacing(14)

        r0 = QHBoxLayout()
        lbl_lang = QLabel(t("language"))
        lbl_lang.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.combo_lang = QComboBox()
        self.combo_lang.addItem("العربية (الفصحى)", "ar")
        self.combo_lang.addItem("English", "en")
        curr_lang = self.config.get("language", "en")
        idx_lang = self.combo_lang.findData(curr_lang)
        if idx_lang >= 0:
            self.combo_lang.setCurrentIndex(idx_lang)
        r0.addWidget(lbl_lang)
        r0.addSpacing(15)
        r0.addWidget(self.combo_lang, 1)
        p_layout.addLayout(r0)

        if sys.platform.startswith("linux"):
            r_scale = QHBoxLayout()
            lbl_scale = QLabel(t("linux_scaling"))
            lbl_scale.setStyleSheet("font-weight: bold; font-size: 14px; color: #00a4dc;")
            self.combo_scale = QComboBox()
            scale_options = ["Auto / Native", "1.0x (100%)", "1.25x (125%)", "1.5x (150%)", "1.75x (175%)", "2.0x (200%)"]
            self.combo_scale.addItems(scale_options)
            saved_scale = self.config.get("linux_scale_factor", "Auto / Native")
            idx_s = self.combo_scale.findText(saved_scale)
            if idx_s >= 0:
                self.combo_scale.setCurrentIndex(idx_s)
            r_scale.addWidget(lbl_scale)
            r_scale.addSpacing(15)
            r_scale.addWidget(self.combo_scale, 1)
            p_layout.addLayout(r_scale)

        r1 = QHBoxLayout()
        lbl_startup = QLabel(t("default_startup"))
        lbl_startup.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.combo_startup = QComboBox()
        self.combo_startup.addItems([t("tab_subsync"), t("tab_merger"), t("tab_renamer"), "MediaInfo"])
        self.combo_startup.setCurrentIndex(self.config.get("startup_tab", 0))
        r1.addWidget(lbl_startup)
        r1.addSpacing(15)
        r1.addWidget(self.combo_startup, 1)
        p_layout.addLayout(r1)

        self.chk_geom = QCheckBox(t("remember_geom"))
        self.chk_geom.setChecked(self.config.get("remember_geometry", True))
        p_layout.addWidget(self.chk_geom)

        r2 = QHBoxLayout()
        lbl_enc = QLabel(t("default_encoding"))
        lbl_enc.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.combo_enc = QComboBox()
        self.combo_enc.addItems(["utf-8-sig", "utf-8", "cp1256", "latin-1"])
        self.combo_enc.setCurrentText(self.config.get("default_encoding", "utf-8-sig"))
        r2.addWidget(lbl_enc)
        r2.addSpacing(15)
        r2.addWidget(self.combo_enc, 1)
        p_layout.addLayout(r2)

        self.chk_styles = QCheckBox(t("keep_styles"))
        self.chk_styles.setChecked(self.config.get("keep_styles_default", True))
        p_layout.addWidget(self.chk_styles)

        self.chk_autosave = QCheckBox(f"{t('auto_save')} - {t('auto_save_desc')}")
        self.chk_autosave.setChecked(self.config.get("auto_save_enabled", False))
        p_layout.addWidget(self.chk_autosave)

        r3 = QHBoxLayout()
        lbl_shift = QLabel(t("default_shift"))
        lbl_shift.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.spin_shift = QSpinBox()
        self.spin_shift.setRange(-3600000, 3600000)
        self.spin_shift.setSingleStep(100)
        self.spin_shift.setValue(self.config.get("default_shift_ms", 0))
        r3.addWidget(lbl_shift)
        r3.addSpacing(15)
        r3.addWidget(self.spin_shift, 1)
        p_layout.addLayout(r3)

        layout.addWidget(panel)

        if sys.platform == 'win32':
            ctx_panel = QFrame(content)
            ctx_panel.setStyleSheet("background: rgba(0, 164, 220, 0.04); border: 1px solid rgba(0, 164, 220, 0.2); border-radius: 12px; padding: 16px;")
            ctx_l = QHBoxLayout(ctx_panel)
            ctx_l.setSpacing(15)
            
            lbl_ctx = QLabel("<b>Windows Context Menu (Cascading Submenu):</b><br/>Add nested options: <i>Studio, Merger, Renamer, MediaInfo</i> on files/folders.")
            lbl_ctx.setStyleSheet("color: white;")
            
            btn_add_ctx = QPushButton("Register Submenu")
            btn_add_ctx.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
            btn_add_ctx.clicked.connect(self.add_context_menu)

            btn_rem_ctx = QPushButton("Remove Submenu")
            btn_rem_ctx.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
            btn_rem_ctx.clicked.connect(self.remove_context_menu)

            ctx_l.addWidget(lbl_ctx, 1)
            ctx_l.addWidget(btn_add_ctx)
            ctx_l.addWidget(btn_rem_ctx)
            layout.addWidget(ctx_panel)

        watch_panel = QFrame(content)
        watch_panel.setStyleSheet("background: rgba(0, 164, 220, 0.05); border: 1px solid rgba(0, 164, 220, 0.22); border-radius: 12px; padding: 18px;")
        w_layout = QVBoxLayout(watch_panel)
        w_layout.setSpacing(12)

        lbl_w_title = QLabel(t("watchdog_title"))
        lbl_w_title.setStyleSheet("font-weight: bold; color: #00a4dc; font-size: 15px;")
        w_layout.addWidget(lbl_w_title)

        w_sub_box = QHBoxLayout()
        lbl_w_sub = QLabel(t("watch_subs"))
        lbl_w_sub.setStyleSheet("font-weight: bold;")
        self.txt_w_sub = QLineEdit(self.config.get("watchdog_subs_dir", ""))
        self.txt_w_sub.setPlaceholderText("...")
        btn_browse_w_sub = QPushButton(t("select_folder"))
        btn_browse_w_sub.clicked.connect(lambda: self._browse_dir(self.txt_w_sub))
        w_sub_box.addWidget(lbl_w_sub)
        w_sub_box.addSpacing(10)
        w_sub_box.addWidget(self.txt_w_sub, 1)
        w_sub_box.addWidget(btn_browse_w_sub)
        w_layout.addLayout(w_sub_box)

        w_vid_box = QHBoxLayout()
        lbl_w_vid = QLabel(t("watch_vids"))
        lbl_w_vid.setStyleSheet("font-weight: bold;")
        self.txt_w_vid = QLineEdit(self.config.get("watchdog_videos_dir", ""))
        self.txt_w_vid.setPlaceholderText("...")
        btn_browse_w_vid = QPushButton(t("select_folder"))
        btn_browse_w_vid.clicked.connect(lambda: self._browse_dir(self.txt_w_vid))
        w_vid_box.addWidget(lbl_w_vid)
        w_vid_box.addSpacing(10)
        w_vid_box.addWidget(self.txt_w_vid, 1)
        w_vid_box.addWidget(btn_browse_w_vid)
        w_layout.addLayout(w_vid_box)

        layout.addWidget(watch_panel)

        btn_box = QHBoxLayout()
        btn_save = QPushButton(t("save_settings"))
        btn_save.setFixedHeight(44)
        btn_save.setStyleSheet("background-color: #00a4dc; color: white; font-weight: bold; padding: 0 30px; font-size: 14px; border: none; border-radius: 8px;")
        btn_save.clicked.connect(self.save_preferences)
        btn_box.addStretch()
        btn_box.addWidget(btn_save)
        layout.addLayout(btn_box)

        layout.addStretch()

    def add_context_menu(self):
        success, msg = register_context_menu()
        if success:
            QMessageBox.information(self, "Success", msg)
        else:
            QMessageBox.warning(self, "Notice", msg)

    def remove_context_menu(self):
        success, msg = unregister_context_menu()
        if success:
            QMessageBox.information(self, "Success", msg)
        else:
            QMessageBox.warning(self, "Notice", msg)

    def _browse_dir(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, t("select_folder"))
        if folder:
            line_edit.setText(os.path.normpath(folder))

    def save_preferences(self):
        old_lang = self.config.get("language", "en")
        new_lang = self.combo_lang.currentData()

        old_scale = self.config.get("linux_scale_factor", "Auto / Native")
        new_scale = self.combo_scale.currentText() if hasattr(self, "combo_scale") else old_scale

        self.config["language"] = new_lang
        self.config["startup_tab"] = self.combo_startup.currentIndex()
        self.config["remember_geometry"] = self.chk_geom.isChecked()
        self.config["default_encoding"] = self.combo_enc.currentText()
        self.config["keep_styles_default"] = self.chk_styles.isChecked()
        self.config["auto_save_enabled"] = self.chk_autosave.isChecked()
        self.config["default_shift_ms"] = self.spin_shift.value()
        self.config["watchdog_subs_dir"] = self.txt_w_sub.text().strip()
        self.config["watchdog_videos_dir"] = self.txt_w_vid.text().strip()
        if hasattr(self, "combo_scale"):
            self.config["linux_scale_factor"] = new_scale

        save_config(self.config)
        self.settings_saved.emit(self.config)

        needs_restart = (old_lang != new_lang) or (sys.platform.startswith("linux") and old_scale != new_scale)

        if needs_restart:
            QMessageBox.information(self, t("notice"), t("lang_restart_msg"))
            QProcess.startDetached(sys.executable, sys.argv)
            from PySide6.QtWidgets import QApplication
            QApplication.instance().quit()
        else:
            QMessageBox.information(self, t("completed"), t("settings_saved_msg"))
