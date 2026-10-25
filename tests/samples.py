"""Тестовые видео: ffmpeg рисует их сам. Настоящие ролики — пути через «:» в KRUGI_REAL_VIDEOS."""
import os
import subprocess
import tempfile
from pathlib import Path

DIR = Path(tempfile.gettempdir()) / "krugi-samples"
REAL = [Path(p) for p in os.environ.get("KRUGI_REAL_VIDEOS", "").split(":") if p]


def ff(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


def make():
    DIR.mkdir(exist_ok=True)
    s = {k: DIR / v for k, v in {
        "wide": "wide-1920x1080.mp4", "rotated": "rotated-phone.mp4", "tiny": "tiny-160x120-60fps.mp4",
        "long": "long-150s.mp4", "minute": "minute-60.4s.mov", "audio": "audio-only.m4a",
        "webm": "clip.webm", "gif": "anim.gif", "junk": "junk.mp4", "hls": "evil.m4v"}.items()}
    if not s["wide"].exists():
        # центр зелёный, края красный и синий: после обрезки по центру должен остаться только зелёный
        ff("-f", "lavfi", "-i", "color=c=0x00c000:s=1920x1080:r=30:d=10",
           "-f", "lavfi", "-i", "sine=f=440:d=10",
           "-vf", "drawbox=x=0:y=0:w=400:h=1080:c=red:t=fill,drawbox=x=1520:y=0:w=400:h=1080:c=blue:t=fill",
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(s["wide"]))
    if not s["rotated"].exists():
        # как снимает телефон: кадр 1280×720, но с пометкой «повернуть на 90°» — на экране вертикальный
        tmp = DIR / "rot-src.mp4"
        ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=5", "-f", "lavfi", "-i", "sine=d=5",
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(tmp))
        ff("-display_rotation:v:0", "90", "-i", str(tmp), "-c", "copy", str(s["rotated"]))
    if not s["tiny"].exists():
        ff("-f", "lavfi", "-i", "testsrc=s=160x120:r=60:d=4", "-c:v", "libx264", "-preset", "ultrafast",
           str(s["tiny"]))
    if not s["long"].exists():
        ff("-f", "lavfi", "-i", "testsrc=s=480x360:r=25:d=150", "-f", "lavfi", "-i", "sine=d=150",
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(s["long"]))
    if not s["minute"].exists():
        ff("-f", "lavfi", "-i", "testsrc=s=720x1280:r=30:d=60.4", "-f", "lavfi", "-i", "sine=d=60.4",
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(s["minute"]))
    if not s["audio"].exists():
        ff("-f", "lavfi", "-i", "sine=d=3", "-c:a", "aac", str(s["audio"]))
    if not s["webm"].exists():
        ff("-f", "lavfi", "-i", "testsrc2=s=854x480:r=30:d=3", "-c:v", "libvpx-vp9", "-b:v", "300k",
           "-deadline", "realtime", str(s["webm"]))
    if not s["gif"].exists():
        ff("-f", "lavfi", "-i", "testsrc=s=320x240:r=10:d=2", str(s["gif"]))
    s["junk"].write_bytes(b"\x00\x01not a video at all" * 1000)
    # «видео», которое на самом деле плейлист HLS со ссылкой на чужой файл на диске
    secret = DIR / "secret.ts"
    if not secret.exists():
        ff("-f", "lavfi", "-i", "testsrc=s=320x240:r=10:d=2", "-c:v", "libx264", "-f", "mpegts", str(secret))
    s["hls"].write_text(f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nfile://{secret}\n#EXT-X-ENDLIST\n")
    return s


def real():
    return [p for p in REAL if p.exists()]
