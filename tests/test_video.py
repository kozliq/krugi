"""Обработка видео: кружок квадратный, не больше 640, не длиннее минуты, H.264, лёгкий.

Запуск: python3 -m unittest discover -s tests (из папки krugi).
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import video  # noqa: E402
import samples  # noqa: E402


def stream(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,profile,pix_fmt,width,height,sample_aspect_ratio",
                          "-of", "default=nw=1", str(path)], capture_output=True, text=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines())


def middle_color(path, at=1.0):
    """Средний цвет кадра (R, G, B)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path), "-frames:v", "1",
                          "-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    return tuple(raw[:3])


class Video(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = samples.make()
        cls.tmp = Path(tempfile.mkdtemp(prefix="krugi-test-"))

    def note(self, src, start=0.0, length=60.0):
        info = video.probe(src)
        dst = self.tmp / f"{Path(src).stem}-{start}.mp4"
        seen = []
        res = video.make_note(src, dst, info, start, length, progress=seen.append)
        self.check(dst, res)
        return dst, res, seen

    def check(self, dst, res):
        st = stream(dst)
        self.assertEqual(st["codec_name"], "h264")
        self.assertEqual(st["pix_fmt"], "yuv420p")
        self.assertEqual(st["width"], st["height"], "кружок должен быть квадратом")
        self.assertLessEqual(int(st["width"]), 640)
        self.assertEqual(int(st["width"]), res["side"])
        self.assertLessEqual(res["duration"], 60.1)
        self.assertLessEqual(res["size"], 12.5 * 1024 * 1024)
        self.assertIn(st.get("sample_aspect_ratio", "1:1"), ("1:1", "N/A"))

    def test_wide_cropped_to_center(self):
        dst, res, seen = self.note(self.s["wide"])
        self.assertEqual(res["side"], 640)
        r, g, b = middle_color(dst)
        self.assertGreater(g, 150)
        self.assertLess(r, 40, "красный край должен уйти — режем по центру")
        self.assertLess(b, 40, "синий край должен уйти — режем по центру")
        self.assertAlmostEqual(res["duration"], 10, delta=0.2)
        self.assertTrue(seen and seen[-1] > 0.9, "прогресс должен доходить до конца")

    def test_rotated_phone_video(self):
        info = video.probe(self.s["rotated"])
        self.assertEqual((info["width"], info["height"]), (720, 1280), "поворот телефона учтён")
        self.note(self.s["rotated"])

    def test_tiny_60fps_upscaled_and_30fps(self):
        dst, res, _ = self.note(self.s["tiny"])
        self.assertEqual(res["side"], 240)
        rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=avg_frame_rate", "-of", "csv=p=0", str(dst)],
                              capture_output=True, text=True).stdout.strip()
        a, b = rate.split("/")
        self.assertLessEqual(float(a) / float(b), 30.5)

    def test_no_audio_ok(self):
        self.assertFalse(video.probe(self.s["tiny"])["audio"])

    def test_webm_and_gif(self):
        self.note(self.s["webm"])
        self.note(self.s["gif"])

    def test_minute_and_a_bit_is_one_note(self):
        info = video.probe(self.s["minute"])
        self.assertEqual(len(video.parts(info["duration"])), 1)
        _, res, _ = self.note(self.s["minute"])
        self.assertLessEqual(res["duration"], 60.1)

    def test_long_split(self):
        info = video.probe(self.s["long"])
        p = video.parts(info["duration"])
        self.assertEqual([round(a) for a, _ in p], [0, 60, 120])
        self.assertAlmostEqual(p[-1][1], 30, delta=0.2)
        _, res, _ = self.note(self.s["long"], *p[1])
        self.assertAlmostEqual(res["duration"], 60, delta=0.15)
        _, res, _ = self.note(self.s["long"], *p[2])
        self.assertAlmostEqual(res["duration"], 30, delta=0.2)

    def test_parts_rules(self):
        self.assertEqual(video.parts(30), [(0, 30)])
        self.assertEqual(video.parts(120.5), [(0, 60), (60, 60)])     # хвост 0,5 с не отдельный кружок
        self.assertEqual(len(video.parts(3600, limit=10)), 10)
        self.assertEqual(video.parts(100, start=70), [(70, 30)])

    def test_start_from_caption(self):
        cases = {"1:20": 80, "с 0:45 плиз": 45, "1:02:03": 3723, "90с": 90, "": 0, "привет": 0,
                 "3:75": 0, "2024": 0}
        for text, sec in cases.items():
            self.assertEqual(video.start_from(text), sec, text)

    def test_bad_files_explained(self):
        for name, word in (("audio", "аудио"), ("junk", "открыть"), ("hls", "открыть")):
            with self.assertRaises(video.VideoError) as e:
                video.probe(self.s[name])
            self.assertIn(word, str(e.exception), name)

    def test_hls_trick_not_converted(self):
        """Плейлист HLS под видом видео: ffmpeg не должен пойти за чужим файлом на диске."""
        info = {"width": 320, "height": 240, "fps": 10, "audio": False, "duration": 2}
        with self.assertRaises(video.VideoError):
            video.make_note(self.s["hls"], self.tmp / "evil.mp4", info, 0, 2)
        self.assertFalse((self.tmp / "evil.mp4").exists())

    def test_hung_ffmpeg_stopped(self):
        info = video.probe(self.s["long"])
        with self.assertRaises(video.VideoError) as e:
            video.make_note(self.s["long"], self.tmp / "slow.mp4", info, 0, 60, timeout=0.3)
        self.assertIn("долго", str(e.exception))
        self.assertFalse((self.tmp / "slow.mp4").exists())

    def test_real_videos(self):
        found = samples.real()
        if not found:
            self.skipTest("настоящих видео на этом компьютере нет")
        for src in found:
            with self.subTest(src.name):
                info = video.probe(src)
                start = max(0.0, min(info["duration"] / 2, info["duration"] - 15))
                _, res, _ = self.note(src, start, 15)
                print(f"\n  {src.name}: {info['width']}×{info['height']} {info['duration']:.0f} с → "
                      f"{res['side']}px, {res['size'] / 1048576:.1f} МБ", end="")


if __name__ == "__main__":
    unittest.main()
