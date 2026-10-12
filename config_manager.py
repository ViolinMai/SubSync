import json
import sys
import os
from pathlib import Path
from i18n import set_language

def get_user_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        path = Path(base) / "SubSync"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        path = Path(base) / "SubSync"
    path.mkdir(parents=True, exist_ok=True)
    return path

CONFIG_FILE = get_user_data_dir() / "suite_config.json"

DEFAULT_CONFIG = {
    "language": "en",
    "startup_tab": 0,
    "remember_geometry": True,
    "window_geometry": "",
    "default_encoding": "utf-8-sig",
    "default_format": "Keep Original",
    "keep_styles_default": True,
    "default_shift_ms": 0,
    "auto_save_enabled": False,
    "watchdog_subs_dir": "",
    "watchdog_videos_dir": "",
    "linux_scale_factor": "Auto / Native",
    "patterns": [
        "{title} S{season:02d}E{ep:02d}{ext}",
        "{title} - {ep:02d}{ext}",
        "{title} E{ep:03d}{ext}",
        "[{season:02d}x{ep:02d}] {title}{ext}",
        "{orig}_{ep:02d}{ext}"
    ],
    "last_pattern": "{title} S{season:02d}E{ep:02d}{ext}",
    "last_extensions": ".mp4, .mkv, .srt, .ass"
}

def load_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                config.update(data)
        except Exception:
            pass
    set_language(config.get("language", "en"))
    return config

def save_config(config_data: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
        set_language(config_data.get("language", "en"))
    except Exception:
        pass
