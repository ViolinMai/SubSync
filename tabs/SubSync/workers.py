import os
import sys
import subprocess
import heapq
import hashlib
import json
from pathlib import Path
import numpy as np
import logging
from PySide6.QtCore import QThread, Signal
from config_manager import get_user_data_dir
from .utils import load_subtitles_safe

logger = logging.getLogger("SubSyncTab")

def get_disk_cache_dir() -> Path:
    cache_dir = get_user_data_dir() / "waveform_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir

def get_cache_key(video_path: str, audio_stream_idx: int) -> str:
    try:
        p = Path(video_path)
        stat = p.stat()
        raw_key = f"{p.resolve()}_{stat.st_size}_{stat.st_mtime}_{audio_stream_idx}"
    except Exception:
        raw_key = f"{video_path}_{audio_stream_idx}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

class MediaProbeWorker(QThread):
    finished = Signal(float, list)
    error = Signal(str)

    def __init__(self, video_path: str, is_windows: bool = True):
        super().__init__()
        self.video_path = video_path
        self.is_windows = is_windows

    def run(self):
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if self.is_windows else 0
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration:stream=index,codec_type,codec_name,disposition:stream_tags=language,title",
                "-of", "json",
                str(self.video_path)
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags)
            if res.returncode != 0 or not res.stdout:
                self.finished.emit(0.0, [])
                return

            data = json.loads(res.stdout)
            format_info = data.get("format", {})
            try:
                duration = float(format_info.get("duration", 0.0))
            except (ValueError, TypeError):
                duration = 0.0

            streams = data.get("streams", [])
            tracks = []
            audio_idx = 0
            for s in streams:
                if s.get("codec_type") == "audio":
                    tags = s.get("tags") or {}
                    disposition = s.get("disposition") or {}
                    lang = tags.get("language") or tags.get("title") or f"Track {audio_idx + 1}"
                    title = tags.get("title") or ""
                    codec = s.get("codec_name") or ""
                    is_default = bool(disposition.get("default", 0))

                    label = f"#{audio_idx + 1}: {lang}" + (f" ({title})" if title and title != lang else "") + (f" [{codec}]" if codec else "")
                    if is_default:
                        label += " [Default]"

                    tracks.append({
                        "stream_idx": audio_idx,
                        "label": label,
                        "mpv_aid": audio_idx + 1,
                        "is_default": is_default
                    })
                    audio_idx += 1

            self.finished.emit(duration, tracks)
        except Exception as e:
            logger.exception(f"MediaProbeWorker error: {e}")
            self.error.emit(str(e))

class SubtitleLoaderWorker(QThread):
    finished = Signal(object, list, list, list)
    error = Signal(str)

    def __init__(self, sub_path: str):
        super().__init__()
        self.sub_path = sub_path

    def run(self):
        try:
            subs = load_subtitles_safe(self.sub_path)
            subs.events.sort(key=lambda s: s.start)
            n = len(subs.events)
            lanes = [0] * n
            starts = [s.start for s in subs.events]
            ends = [s.end for s in subs.events]

            # توزيع مسارات O(N log K) سريع جداً بواسطة heap خفيف
            lane_heap = []
            for idx in range(n):
                st = starts[idx]
                en = ends[idx]
                if lane_heap and lane_heap[0][0] <= st:
                    earliest_end, lane_id = heapq.heappop(lane_heap)
                    lanes[idx] = lane_id
                    heapq.heappush(lane_heap, (en, lane_id))
                else:
                    new_lane_id = len(lane_heap)
                    lanes[idx] = new_lane_id
                    heapq.heappush(lane_heap, (en, new_lane_id))

            self.finished.emit(subs, lanes, starts, ends)
        except Exception as e:
            logger.exception(f"SubtitleLoaderWorker error: {e}")
            self.error.emit(str(e))

class FastAudioWorker(QThread):
    chunk_ready = Signal(np.ndarray, int, int)
    finished = Signal(int, np.ndarray, float)
    error = Signal(str)

    def __init__(self, video_path: str, audio_stream_idx: int = 0, pre_duration: float = 0.0, is_windows: bool = True):
        super().__init__()
        self.video_path = video_path
        self.audio_stream_idx = audio_stream_idx
        self.duration_sec = pre_duration
        self.is_windows = is_windows

    def run(self):
        try:
            cache_file = get_disk_cache_dir() / f"{get_cache_key(self.video_path, self.audio_stream_idx)}.npz"
            if cache_file.exists():
                try:
                    data = np.load(str(cache_file))
                    peaks = data["peaks"]
                    dur = float(data["duration"])
                    self.chunk_ready.emit(peaks, 0, len(peaks))
                    self.finished.emit(self.audio_stream_idx, peaks, dur)
                    return
                except Exception:
                    pass

            creationflags = subprocess.CREATE_NO_WINDOW if self.is_windows else 0
            cmd = [
                "ffmpeg", "-v", "quiet",
                "-i", str(self.video_path),
                "-map", f"0:a:{self.audio_stream_idx}?",
                "-vn", "-ac", "1", "-ar", "8000",
                "-f", "s16le", "-"
            ]
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=1024*1024, creationflags=creationflags
            )

            chunk_size = 80
            bytes_per_batch = 8000 * 30 * 2

            total_samples = int(self.duration_sec * 8000) if self.duration_sec > 0 else 0
            processed_samples = 0
            emitted_peak_idx = 0
            collected_peaks = []

            while True:
                raw_bytes = process.stdout.read(bytes_per_batch)
                if not raw_bytes:
                    break

                samples = np.frombuffer(raw_bytes, dtype=np.int16)
                num_chunks = len(samples) // chunk_size
                if num_chunks > 0:
                    trimmed = samples[:num_chunks * chunk_size]
                    reshaped = np.abs(trimmed.reshape(num_chunks, chunk_size))
                    raw_peaks = np.max(reshaped, axis=1).astype(np.float32)

                    norm_peaks = raw_peaks / 32768.0
                    enhanced_peaks = np.power(norm_peaks, 0.55) * 1.25
                    peaks = np.clip(enhanced_peaks, 0.0, 1.0).astype(np.float32)

                    collected_peaks.append(peaks)
                    est_total = (total_samples // chunk_size) if total_samples > 0 else (emitted_peak_idx + len(peaks))
                    self.chunk_ready.emit(peaks, emitted_peak_idx, est_total)
                    emitted_peak_idx += len(peaks)
                    processed_samples += len(trimmed)

            process.stdout.close()
            process.wait()

            actual_duration = processed_samples / 8000.0 if processed_samples > 0 else self.duration_sec
            all_peaks = np.concatenate(collected_peaks) if collected_peaks else np.zeros(0, dtype=np.float32)

            if len(all_peaks) > 0 and actual_duration > 0:
                try:
                    np.savez_compressed(str(cache_file), peaks=all_peaks, duration=actual_duration)
                except Exception as e:
                    logger.debug(f"Failed to write disk cache: {e}")

            self.finished.emit(self.audio_stream_idx, all_peaks, actual_duration)
        except Exception as e:
            logger.exception(f"FastAudioWorker error: {e}")
            self.error.emit(str(e))
