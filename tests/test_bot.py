"""Бот целиком на поддельном Telegram: присылаем видео — проверяем, что пришёл правильный кружок.

Запуск: python3 -m unittest discover -s tests (из папки krugi).
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import samples  # noqa: E402
from mock_tg import MockTelegram  # noqa: E402


def square(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    w, h = out.strip().split(",")
    return int(w) == int(h) and int(w) <= 640


class Bot(unittest.TestCase):
    local = False

    @classmethod
    def setUpClass(cls):
        cls.s = samples.make()
        cls.tg = MockTelegram()
        cls.tg.local_paths = cls.local
        for name, path in cls.s.items():
            cls.tg.files[name] = path
        cls.dir = Path(tempfile.mkdtemp(prefix="krugi-bot-"))
        env = {**os.environ, "KRUGI_TOKEN": "123:test", "KRUGI_TG_API": cls.tg.url,
               "KRUGI_DATA": str(cls.dir / "data"), "KRUGI_WORK": str(cls.dir / "work"),
               "KRUGI_TG_LOCAL": "1" if cls.local else "", "PYTHONUNBUFFERED": "1"}
        cls.log = open(cls.dir / "bot.log", "w+")
        cls.bot = subprocess.Popen([sys.executable, "-I", str(HERE.parent / "krugi.py")], env=env,
                                   stdout=cls.log, stderr=subprocess.STDOUT)
        assert cls.tg.wait(lambda c: any(m == "setMyDescription" for m, _ in c), 20), "бот не запустился"

    @classmethod
    def tearDownClass(cls):
        cls.bot.terminate()
        cls.bot.wait()
        cls.tg.stop()
        cls.log.seek(0)
        print("\n--- журнал бота ---\n" + cls.log.read())

    chat = 100

    def setUp(self):
        Bot.chat += 1
        self.chat = Bot.chat

    def video(self, name, caption=None, kind="video", size=None):
        media = {"file_id": name, "file_size": size or Path(self.s[name]).stat().st_size}
        if kind == "document":
            media["mime_type"] = "video/mp4"
        return self.tg.message(self.chat, **{kind: media}, **({"caption": caption} if caption else {}))

    def notes(self, n=1, timeout=60):
        got = self.tg.wait(lambda c: len(self.tg.of("sendVideoNote", self.chat)) >= n and
                           self.tg.of("deleteMessage", self.chat), timeout)
        self.assertTrue(got, f"не дождался {n} кружков")
        return self.tg.of("sendVideoNote", self.chat)

    def texts(self):
        return [p.get("text", "") for p in self.tg.of("sendMessage", self.chat) + self.tg.of("editMessageText", self.chat)]

    def last_text(self, word, timeout=30):
        got = self.tg.wait(lambda c: any(word in t for t in self.texts()), timeout)
        self.assertTrue(got, f"нет сообщения со словом «{word}»: {self.texts()}")

    def test_start(self):
        self.tg.message(self.chat, text="/start")
        self.last_text("Пришли видео")
        self.assertNotIn("donate", " ".join(self.texts()), "в приветствии не просим денег")

    def test_one_note(self):
        self.video("wide")
        sent = self.notes()
        self.assertEqual(len(sent), 1)
        self.assertTrue(square(sent[0]["video_note"]))
        self.assertEqual(sent[0]["length"], 640)
        self.assertEqual(sent[0]["duration"], 10)
        self.assertTrue(any("Делаю кружок\n○○○" in t for t in self.texts()), "был прогресс")

    def test_document_and_gif(self):
        self.video("webm", kind="document")
        self.notes(1)
        self.video("gif", kind="animation")
        self.notes(2)

    def test_long_offers_split(self):
        self.video("long")
        got = self.tg.wait(lambda c: [p for p in self.tg.of("editMessageText", self.chat) if p.get("reply_markup")])
        self.assertTrue(got, "должен спросить, резать ли")
        markup = got[-1]["reply_markup"]["inline_keyboard"]
        self.assertIn("3 кружка", markup[0][0]["text"])
        self.assertIn("2:30", got[-1]["text"])
        self.tg.push("callback_query", {"id": "q1", "from": {"id": self.chat}, "data": markup[0][0]["callback_data"],
                                        "message": {"message_id": 1, "chat": {"id": self.chat}}})
        sent = self.notes(3, timeout=120)
        self.assertEqual([p["duration"] for p in sent], [60, 60, 30])
        # второе нажатие на ту же кнопку — видео уже удалено
        self.tg.push("callback_query", {"id": "q2", "from": {"id": self.chat}, "data": markup[0][0]["callback_data"],
                                        "message": {"message_id": 1, "chat": {"id": self.chat}}})
        self.assertTrue(self.tg.wait(lambda c: any("удалил" in p.get("text", "")
                                                   for p in self.tg.of("answerCallbackQuery"))))

    def test_long_first_minute_only(self):
        self.video("long", caption="с 1:10")
        got = self.tg.wait(lambda c: [p for p in self.tg.of("editMessageText", self.chat) if p.get("reply_markup")])
        one = got[-1]["reply_markup"]["inline_keyboard"][1][0]
        self.assertIn("1:10", one["text"])
        self.tg.push("callback_query", {"id": "q3", "from": {"id": self.chat}, "data": one["callback_data"],
                                        "message": {"message_id": 1, "chat": {"id": self.chat}}})
        sent = self.notes(1)
        time.sleep(1)
        self.assertEqual(len(self.tg.of("sendVideoNote", self.chat)), 1)
        self.assertEqual(sent[0]["duration"], 60)

    def test_caption_start(self):
        self.video("wide", caption="0:04")
        sent = self.notes()
        self.assertEqual(sent[0]["duration"], 6)

    def test_too_big(self):
        self.video("wide", size=5000 * 1024 * 1024)
        self.last_text("5000 МБ")
        self.assertFalse(self.tg.of("editMessageText", self.chat), "огромный файл даже не берём в работу")

    def test_too_big_by_telegram(self):
        self.tg.too_big.add("tiny")
        try:
            self.video("tiny", size=1)
            self.last_text("только до")
        finally:
            self.tg.too_big.discard("tiny")

    def test_bad_files(self):
        self.video("audio", kind="document")
        self.last_text("аудио")
        self.video("junk", kind="document")
        self.last_text("MP4")
        self.video("hls", kind="document")
        self.tg.wait(lambda c: sum("MP4" in t for t in self.texts()) >= 2)
        self.assertFalse(self.tg.of("sendVideoNote", self.chat))

    def test_forbidden_video_notes(self):
        self.tg.no_video_notes.add(self.chat)
        self.video("tiny")
        got = self.tg.wait(lambda c: self.tg.of("sendVideo", self.chat))
        self.assertTrue(got, "должен прислать обычным видео")
        self.last_text("закрыты видеосообщения")

    def test_other_messages(self):
        self.tg.message(self.chat, text="https://www.tiktok.com/@x/video/1")
        self.last_text("kachalka")
        self.tg.message(self.chat, video_note={"file_id": "x", "length": 240, "duration": 3})
        self.last_text("уже кружок")
        self.tg.message(self.chat, photo=[{"file_id": "p", "width": 10, "height": 10}])
        self.last_text("фото")

    def test_donate(self):
        self.tg.message(self.chat, text="/donate")
        got = self.tg.wait(lambda c: [p for p in self.tg.of("sendMessage", self.chat) if p.get("reply_markup")])
        self.assertEqual(len(got[0]["reply_markup"]["inline_keyboard"][0]), 4)

    def test_queue_line_limit(self):
        for _ in range(6):
            self.video("tiny")
        self.last_text("Дождись", timeout=30)
        self.notes(3, timeout=60)

    def test_flood_mute(self):
        for i in range(25):
            self.tg.message(self.chat, text=f"привет {i}")
        self.last_text("Передохну")

    def test_groups_ignored(self):
        self.tg.push("message", {"message_id": 5, "chat": {"id": -5, "type": "group"}, "from": {"id": 7},
                                 "video": {"file_id": "wide", "file_size": 10}})
        time.sleep(2)
        self.assertFalse(self.tg.of("sendMessage", -5))


class BotLocalServer(Bot):
    """То же, но как со своим сервером Telegram: getFile отдаёт путь к файлу на диске."""
    local = True

    def test_big_file_ok(self):
        self.video("wide", size=300 * 1024 * 1024)
        self.notes()


if __name__ == "__main__":
    unittest.main()
