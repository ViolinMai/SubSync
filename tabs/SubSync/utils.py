import re
import os
import json
import shutil
import codecs
import subprocess
from pathlib import Path
import pysubs2
from pysubs2 import SSAStyle, Color
import logging
from PySide6.QtGui import QFontDatabase

logger = logging.getLogger("SubSyncTab")

def read_sub_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode('utf-16')
    for enc in ('utf-8-sig', 'cp1256'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode('cp1256', errors='replace')

def clean_sub_text(text: str) -> str:
    cleaned = re.sub(r"\{.*?\}", "", text)
    return cleaned.replace("\\N", " ").replace("\\n", " ").strip()

def sanitize_font_name(name: str) -> str:
    """تنظيف اسم الخط من بادئات الـ Subsetting المشفرة مثل ABCDEF+Arial أو الرموز العشوائية."""
    if not name:
        return "Arial"
    clean = re.sub(r"^[A-Z]{6,8}\+", "", name.strip())
    clean = re.sub(r"[\r\n\t]+", "", clean)
    # تجاهل الخطوط المشفرة العشوائية التي تكون مجرد 8 أحرف كبيرة بدون مسافات
    if re.fullmatch(r"[A-Z0-9]{7,10}", clean):
        return ""
    return clean.strip()

def apply_custom_cr_template(subs: pysubs2.SSAFile):
    subs.info["Title"] = "العربية"
    subs.info["Original Script"] = "cr_ar  [http://www.crunchyroll.com/user/cr_ar]"
    subs.info["ScriptType"] = "v4.00+"
    subs.info["PlayResX"] = "1920"
    subs.info["PlayResY"] = "1080"
    subs.info["WrapStyle"] = "0"
    subs.info["YCbCr Matrix"] = "TV.601"

    subs.styles.clear()

    subs.styles["Default"] = SSAStyle(
        fontname="Adobe Arabic", fontsize=72,
        primarycolor=Color(255, 255, 255, 0), secondarycolor=Color(255, 0, 0, 0),
        outlinecolor=Color(0, 0, 0, 0), backcolor=Color(0, 0, 0, 0),
        bold=True, italic=False, underline=False, strikeout=False,
        scalex=100, scaley=100, spacing=0, angle=0,
        borderstyle=1, outline=3, shadow=0,
        alignment=2, marginl=30, marginr=30, marginv=54, encoding=1
    )

    subs.styles["Main"] = SSAStyle(
        fontname="Adobe Arabic", fontsize=72,
        primarycolor=Color(255, 255, 255, 0), secondarycolor=Color(255, 0, 0, 0),
        outlinecolor=Color(0, 0, 0, 0), backcolor=Color(0, 0, 0, 0),
        bold=False, italic=False, underline=False, strikeout=False,
        scalex=100, scaley=100, spacing=0, angle=0,
        borderstyle=1, outline=6, shadow=3,
        alignment=2, marginl=30, marginr=30, marginv=54, encoding=1
    )

    subs.styles["Italics"] = SSAStyle(
        fontname="Adobe Arabic", fontsize=72,
        primarycolor=Color(255, 255, 255, 0), secondarycolor=Color(255, 0, 0, 0),
        outlinecolor=Color(0, 0, 0, 0), backcolor=Color(0, 0, 0, 0),
        bold=False, italic=True, underline=False, strikeout=False,
        scalex=100, scaley=100, spacing=0, angle=0,
        borderstyle=1, outline=6, shadow=3,
        alignment=2, marginl=30, marginr=30, marginv=54, encoding=1
    )

    subs.styles["Flashback"] = SSAStyle(
        fontname="Adobe Arabic", fontsize=72,
        primarycolor=Color(255, 255, 255, 0), secondarycolor=Color(255, 0, 0, 0),
        outlinecolor=Color(0, 0, 64, 0), backcolor=Color(0, 0, 64, 0),
        bold=True, italic=False, underline=False, strikeout=False,
        scalex=100, scaley=100, spacing=0, angle=0,
        borderstyle=1, outline=3, shadow=0,
        alignment=2, marginl=30, marginr=30, marginv=54, encoding=1
    )

    subs.styles["Flashback_Italics"] = SSAStyle(
        fontname="Adobe Arabic", fontsize=72,
        primarycolor=Color(255, 255, 255, 0), secondarycolor=Color(255, 0, 0, 0),
        outlinecolor=Color(0, 0, 80, 0), backcolor=Color(0, 0, 80, 0),
        bold=False, italic=True, underline=False, strikeout=False,
        scalex=100, scaley=100, spacing=0, angle=0,
        borderstyle=1, outline=6, shadow=3,
        alignment=2, marginl=30, marginr=30, marginv=54, encoding=1
    )

    subs.styles["Narration"] = SSAStyle(
        fontname="Adobe Arabic", fontsize=72,
        primarycolor=Color(255, 255, 255, 0), secondarycolor=Color(255, 0, 0, 0),
        outlinecolor=Color(80, 0, 0, 0), backcolor=Color(80, 0, 0, 0),
        bold=False, italic=True, underline=False, strikeout=False,
        scalex=100, scaley=100, spacing=0, angle=0,
        borderstyle=1, outline=6, shadow=3,
        alignment=2, marginl=30, marginr=30, marginv=54, encoding=1
    )

    subs.styles["OP_ED"] = SSAStyle(
        fontname="Adobe Arabic", fontsize=72,
        primarycolor=Color(255, 255, 255, 0), secondarycolor=Color(255, 0, 0, 0),
        outlinecolor=Color(0, 0, 0, 0), backcolor=Color(0, 0, 0, 0),
        bold=False, italic=True, underline=False, strikeout=False,
        scalex=100, scaley=100, spacing=0, angle=0,
        borderstyle=1, outline=6, shadow=3,
        alignment=2, marginl=30, marginr=30, marginv=54, encoding=0
    )

    subs.styles["Default - Top"] = SSAStyle(
        fontname="Adobe Arabic", fontsize=72,
        primarycolor=Color(255, 255, 255, 0), secondarycolor=Color(255, 0, 0, 0),
        outlinecolor=Color(0, 0, 0, 0), backcolor=Color(0, 0, 0, 0),
        bold=True, italic=False, underline=False, strikeout=False,
        scalex=100, scaley=100, spacing=0, angle=0,
        borderstyle=1, outline=3, shadow=0,
        alignment=8, marginl=30, marginr=30, marginv=60, encoding=1
    )

    for ev in subs.events:
        txt = ev.text.strip()
        if (txt.startswith("<i>") and txt.endswith("</i>")) or "\\i1" in txt:
            ev.style = "Italics"
            ev.text = re.sub(r"</?i>", "", txt)
        elif not ev.style or ev.style.lower() in ("default", "main"):
            ev.style = "Main"

def load_subtitles_safe(path: str):
    detected_enc = None
    try:
        from charset_normalizer import from_bytes
        with open(path, "rb") as bf:
            sample = bf.read(65536)
            res = from_bytes(sample).best()
            if res:
                detected_enc = res.encoding
    except Exception:
        pass

    candidates = [detected_enc] if detected_enc else []
    for enc in ["utf-8-sig", "utf-8", "cp1256", "windows-1256", "latin-1"]:
        if enc not in candidates:
            candidates.append(enc)

    for enc in candidates:
        if not enc:
            continue
        try:
            return pysubs2.load(path, encoding=enc)
        except Exception:
            continue

    text_content = read_sub_text(Path(path))
    return pysubs2.SSAFile.from_string(text_content)

def ms_to_timestamp(ms: int) -> str:
    ms = max(0, int(ms))
    secs, milliseconds = divmod(ms, 1000)
    mins, secs = divmod(secs, 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}.{milliseconds:03d}"

def timestamp_to_ms(ts: str) -> int:
    try:
        parts = ts.replace(",", ".").split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(float(h) * 3600000 + float(m) * 60000 + float(s) * 1000)
    except Exception:
        pass
    return 0

def get_episode_number(filename: str):
    clean_name = re.sub(r'\[[a-fA-F0-9]{8}\]', '', filename)
    clean_name = re.sub(r'(?i)(1080p|720p|480p|2160p|4k|x264|x265|hevc|10bit|dual\s?audio)', '', clean_name)
    m = re.search(r'(?i)(?:s\d{1,2})?[ex](\d{1,4})', clean_name)
    if m: return int(m.group(1))
    m = re.search(r'(?i)(?:episode|ep|حلقة)[\s_.-]*(\d{1,4})', clean_name)
    if m: return int(m.group(1))
    m = re.search(r'-\s0*(\d{1,4})(?:v\d)?', clean_name)
    if m: return int(m.group(1))
    matches = re.findall(r'(?<!\d)0*(\d{1,4})(?!\d)', Path(clean_name).stem)
    if matches: return int(matches[-1])
    return None

def get_video_duration_fast(video_path: str, is_windows: bool = True) -> float:
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if is_windows else 0
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags)
        if res.returncode == 0 and res.stdout.strip():
            return float(res.stdout.strip())
    except Exception:
        pass
    return 0.0

def get_embedded_subtitle_tracks(video_path: str, is_windows: bool = True) -> list:
    tracks = []
    p_vid = Path(video_path)
    creationflags = subprocess.CREATE_NO_WINDOW if is_windows else 0

    if p_vid.suffix.lower() == '.mkv' and shutil.which("mkvmerge"):
        try:
            cmd = ["mkvmerge", "-J", str(video_path)]
            res = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags)
            if res.returncode in (0, 1) and res.stdout:
                data = json.loads(res.stdout)
                for t in data.get("tracks", []):
                    if t.get("type") == "subtitles":
                        props = t.get("properties", {})
                        codec_raw = t.get("codec", "").lower()
                        lang = (props.get("language") or props.get("language_ietf") or "und").lower()
                        title = props.get("track_name") or ""
                        is_def = bool(props.get("default_track", False))
                        track_id = t.get("id")

                        if "subrip" in codec_raw or "utf8" in codec_raw:
                            ext = ".srt"
                        elif "ass" in codec_raw or "ssa" in codec_raw:
                            ext = ".ass"
                        elif "pgs" in codec_raw or "hdmv" in codec_raw:
                            ext = ".sup"
                        elif "vtt" in codec_raw:
                            ext = ".vtt"
                        else:
                            ext = ".srt"

                        tracks.append({
                            "stream_idx": track_id,
                            "track_id": track_id,
                            "is_mkv_tool": True,
                            "codec": codec_raw,
                            "ext": ext,
                            "lang": lang,
                            "title": title,
                            "is_default": is_def,
                            "label": f"#{track_id}: {lang.upper()}" + (f" ({title})" if title else "") + f" [{codec_raw}]" + (" [Default]" if is_def else "")
                        })
                return tracks
        except Exception as e:
            logger.debug(f"mkvmerge -J failed: {e}")

    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "s",
            "-show_entries", "stream=index,codec_name:stream_tags=language,title:stream=disposition",
            "-of", "json",
            str(video_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags)
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout)
            for idx, s in enumerate(data.get("streams", [])):
                tags = s.get("tags") or {}
                disp = s.get("disposition") or {}
                codec = s.get("codec_name", "subrip").lower()
                lang = (tags.get("language") or f"track_{idx+1}").lower()
                title = tags.get("title") or ""
                is_def = bool(disp.get("default", 0))

                ext = ".ass" if ("ass" in codec or "ssa" in codec) else (".srt" if ("subrip" in codec or "text" in codec) else ".vtt")
                tracks.append({
                    "stream_idx": s.get("index", idx),
                    "track_id": s.get("index", idx),
                    "is_mkv_tool": False,
                    "codec": codec,
                    "ext": ext,
                    "lang": lang,
                    "title": title,
                    "is_default": is_def,
                    "label": f"#{idx + 1}: {lang.upper()}" + (f" ({title})" if title else "") + f" [{codec}]" + (" [Default]" if is_def else "")
                })
    except Exception as e:
        logger.debug(f"ffprobe inspection failed: {e}")
    return tracks

def extract_embedded_subtitle(video_path: str, stream_idx: int, out_path: str, is_windows: bool = True) -> bool:
    creationflags = subprocess.CREATE_NO_WINDOW if is_windows else 0
    p_vid = Path(video_path)
    out_p = Path(out_path)

    if p_vid.suffix.lower() == '.mkv' and shutil.which("mkvextract"):
        try:
            cmd = ["mkvextract", str(video_path), "tracks", f"{stream_idx}:{str(out_p)}"]
            res = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags)
            if res.returncode == 0 and out_p.exists():
                if out_p.suffix.lower() == '.ass' and shutil.which("mkvmerge"):
                    try:
                        r_info = subprocess.run(["mkvmerge", "-J", str(video_path)], capture_output=True, text=True, creationflags=creationflags)
                        if r_info.returncode in (0, 1):
                            mkv_json = json.loads(r_info.stdout)
                            attachments = mkv_json.get("attachments", [])
                            if attachments:
                                fonts_dir = out_p.parent / f"{out_p.stem}_fonts"
                                fonts_dir.mkdir(parents=True, exist_ok=True)
                                att_args = [f"{a['id']}:{str(fonts_dir / a['file_name'])}" for a in attachments if any(a['file_name'].lower().endswith(ext) for ext in ('.ttf', '.otf', '.ttc'))]
                                if att_args:
                                    subprocess.run(["mkvextract", str(video_path), "attachments"] + att_args, capture_output=True, text=True, creationflags=creationflags)
                    except Exception:
                        pass
                return True
        except Exception as e:
            logger.debug(f"mkvextract failed: {e}")

    try:
        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-map", f"0:{stream_idx}"]
        out_ext = out_p.suffix.lower()
        if out_ext == '.srt':
            cmd.extend(["-c:s", "srt"])
        elif out_ext == '.ass':
            cmd.extend(["-c:s", "ass"])
        else:
            cmd.extend(["-c:s", "copy"])
        cmd.append(str(out_p))

        res = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags)
        return res.returncode == 0 and out_p.exists()
    except Exception as e:
        logger.error(f"Error extracting subtitle: {e}")
        return False

def get_event_font_info(event, subs_data) -> tuple[str, bool]:
    if not event:
        return "Unknown", False
    font_name = ""
    fn_matches = re.findall(r"\\fn([^\\\}]+)", getattr(event, "text", ""))
    if fn_matches:
        font_name = sanitize_font_name(fn_matches[-1])
        
    if not font_name and subs_data and hasattr(subs_data, "styles"):
        style_name = getattr(event, "style", "")
        if style_name in subs_data.styles:
            style_obj = subs_data.styles[style_name]
            if hasattr(style_obj, "fontname") and style_obj.fontname:
                font_name = sanitize_font_name(style_obj.fontname)
                
    if not font_name:
        font_name = "Arial"

    sys_fonts = set(f.lower() for f in QFontDatabase.families())
    is_installed = font_name.lower() in sys_fonts
    return font_name, is_installed
