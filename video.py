"""Кружок из видео: ffprobe смотрит, что прислали, ffmpeg режет квадрат по центру и сжимает.

Telegram показывает видеосообщение (кружок), только если это квадратный MP4 (H.264 + AAC)
не длиннее минуты и не больше 640×640. Чтобы кружок не подтормаживал на телефоне,
держим поток около 1,5 Мбит/с — минута выходит до ~12 МБ.

Файлы присылают чужие люди, поэтому ffmpeg читает только обычные видеоконтейнеры
и только с диска: плейлист HLS или «concat» с путями к чужим файлам не откроется.
"""
import json
import math
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

PART = 60.0                 # Telegram: кружок до минуты
SIDE_MAX, SIDE_MIN = 640, 240
FPS_MAX = 30
THREADS = os.environ.get("KRUGI_FFMPEG_THREADS", "2")
# демуксеры, которым можно доверить чужой файл (mov — это и mp4/3gp/m4v, matroska — это и webm)
FORMATS = "mov,mp4,matroska,webm,avi,gif,mpegts,flv,3gp,ogg"
SAFE_INPUT = ["-protocol_whitelist", "file", "-format_whitelist", FORMATS]


class VideoError(Exception):
    """Понятная человеку причина, почему кружок не получится."""


def probe(path):
    """Что за видео: длина, размер кадра (уже с учётом поворота телефона), fps, есть ли звук."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", *SAFE_INPUT, "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        raise VideoError("Файл не читается — похоже, он повреждён.")
    try:
        info = json.loads(out.stdout or b"{}")
    except ValueError:
        info = {}
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if out.returncode or not info.get("format"):
        raise VideoError("Не получилось открыть файл как видео. Пришли MP4, MOV или WEBM.")
    if not video:
        raise VideoError("В файле нет картинки — похоже, это аудио. Нужно видео.")
    w, h = int(video.get("width") or 0), int(video.get("height") or 0)
    if w < 16 or h < 16:
        raise VideoError("Не получилось открыть файл как видео. Пришли MP4, MOV или WEBM.")
    rotate = 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            rotate = int(side["rotation"])
    if "rotate" in (video.get("tags") or {}):
        rotate = int(video["tags"]["rotate"])
    if abs(rotate) % 180 == 90:
        w, h = h, w
    duration = _num(info["format"].get("duration")) or _num(video.get("duration"))
    return {
        "duration": duration,
        "width": w, "height": h,
        "fps": _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate")),
        "audio": any(s.get("codec_type") == "audio" for s in streams),
        "format": info["format"].get("format_name", ""),
    }


def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) and v > 0 else 0.0


def _rate(v):
    try:
        a, b = str(v).split("/")
        return float(a) / float(b) if float(b) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def side_for(info):
    """Диаметр кружка: квадрат по меньшей стороне, но не больше 640 и не меньше 240 (мелкое растягиваем)."""
    s = min(info["width"], info["height"], SIDE_MAX)
    return max(SIDE_MIN, s - s % 2)


def parts(duration, start=0.0, limit=10):
    """Куски по минуте: [(начало, длина), …]. Хвост короче 1 с не отдельный кружок — он ни о чём."""
    out, t = [], start
    while t < duration - 0.05 and len(out) < limit:
        left = duration - t
        if out and left < 1.0:
            break
        out.append((round(t, 3), round(min(PART, left), 3)))
        t += PART
    return out


TIME_RE = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,3}):(\d{2})(?![\d:])|(?<![\d:.,])(\d{1,4})\s*(?:с|сек|s)\b", re.I)


def start_from(text):
    """Время в подписи к видео: «1:20», «с 0:45», «1:02:03», «90с» → секунды (или 0)."""
    m = TIME_RE.search(text or "")
    if not m:
        return 0.0
    if m.group(4):
        return float(m.group(4))
    h, mnt, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return float(h * 3600 + mnt * 60 + s) if s < 60 else 0.0


def bitrate(side):
    return 1400 if side > 480 else 1000 if side > 320 else 700


def make_note(src, dst, info, start=0.0, length=PART, progress=None, timeout=None):
    """Один кружок из куска [start, start+length). progress(доля 0…1) зовётся по ходу работы."""
    side = side_for(info)
    length = min(length, PART)
    if info.get("duration"):        # «до минуты», а видео короче — прогресс считаем от настоящей длины
        length = max(0.1, min(length, info["duration"] - start))
    vf = (f"crop='min(iw,ih)':'min(iw,ih)',scale={side}:{side}:flags=lanczos,setsar=1")
    if info.get("fps", 0) > FPS_MAX + 0.5:
        vf += f",fps={FPS_MAX}"
    vf += ",format=yuv420p"
    kbps = bitrate(side)
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y",
           *SAFE_INPUT, "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{length:.3f}",
           "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
           "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "main", "-crf", "23",
           "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k", "-g", "60",
           "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-ar", "48000",
           "-movflags", "+faststart", "-threads", THREADS,
           "-progress", "pipe:1", "-nostats", str(dst)]
    timeout = timeout or max(180.0, length * 8)
    # ошибки ffmpeg — в файл, а не в трубу: на битом видео их бывает много, и полная труба его остановит
    with tempfile.TemporaryFile() as log, \
            subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log, text=True) as proc:
        watchdog = threading.Timer(timeout, proc.kill)    # завис и молчит — всё равно остановим
        watchdog.start()
        try:
            for line in proc.stdout:
                key, _, val = line.strip().partition("=")
                if key == "out_time_us" and progress and val.isdigit():
                    progress(min(1.0, int(val) / 1e6 / length))
            proc.wait()
        finally:
            killed = not watchdog.is_alive()
            watchdog.cancel()
        log.seek(0)
        err = log.read().decode(errors="replace")
    if killed:
        Path(dst).unlink(missing_ok=True)
        raise VideoError("Видео обрабатывается слишком долго — попробуй файл покороче или поменьше.")
    if proc.returncode or not Path(dst).exists() or Path(dst).stat().st_size < 1000:
        Path(dst).unlink(missing_ok=True)
        print("ffmpeg:", err.strip()[-500:], flush=True)
        raise VideoError("Не получилось переделать это видео. Попробуй другой файл или сохрани его заново.")
    return {"side": side, "duration": probe(dst)["duration"], "size": Path(dst).stat().st_size}
