"""Кружок: Telegram-бот, который делает из присланного видео видеосообщение-кружок.

Человек присылает видео — бот вырезает квадрат по центру, сжимает под Telegram (video.py)
и отвечает кружком. Видео длиннее минуты бот предлагает разрезать на кружки по минуте.
Если в подписи к видео время («1:20») — кружок начнётся оттуда.

Бесплатно, без рекламы и подписок на каналы; звёзды — только по /donate.
Видео не храним: сделал кружок — удалил и исходник, и результат.

Обычный Telegram отдаёт ботам файлы только до 20 МБ. Со своим сервером Telegram
(telegram-bot-api --local, KRUGI_TG_LOCAL=1) — до 2000 МБ, файл берётся прямо с диска.

Запуск: python3 krugi.py (на сервере — служба krugi-bot).
Токен — переменная KRUGI_TOKEN или файл bot.token в папке данных (KRUGI_DATA).
Аватарка ставится один раз: python3 krugi.py avatar [путь.jpg]
Только стандартная библиотека Python (+ certifi, если есть) и ffmpeg.
"""
import hashlib
import http.client
import json
import os
import shutil
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # python3 -I не видит соседние файлы
import video  # noqa: E402

try:
    import certifi
    TLS = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    TLS = ssl.create_default_context()

# с серверов до Telegram по IPv6 запрос иногда висит по минуте (так было у Качалки) — ходим по IPv4
_getaddrinfo = socket.getaddrinfo


def _ipv4_for_telegram(host, *args, **kwargs):
    if host == "api.telegram.org":
        return [a for a in _getaddrinfo(host, *args, **kwargs) if a[0] == socket.AF_INET] or \
            _getaddrinfo(host, *args, **kwargs)
    return _getaddrinfo(host, *args, **kwargs)


socket.getaddrinfo = _ipv4_for_telegram

ENV = os.environ.get
HERE = Path(__file__).resolve().parent
DATA = Path(ENV("KRUGI_DATA") or Path.home() / "Library" / "Application Support" / "Krugi")
WORK = Path(ENV("KRUGI_WORK") or Path.home() / "Library" / "Caches" / "Krugi")
TG_API = ENV("KRUGI_TG_API", "https://api.telegram.org")
TG_LOCAL = ENV("KRUGI_TG_LOCAL") == "1"
IN_MAX = int(ENV("KRUGI_MAX_MB") or (1000 if TG_LOCAL else 20)) * 1024 * 1024   # сколько Telegram отдаёт боту
AVATAR = HERE / "brand" / "avatar-640.jpg"

# Лимиты. Бот открыт всем, а сервер один — никто не должен забить очередь остальным.
JOBS = threading.Semaphore(int(ENV("KRUGI_JOBS") or 2))     # ffmpeg разом
MAX_PARTS = 10              # длинное видео — не больше 10 кружков (10 минут)
LINE = 3                    # видео одного человека в очереди
QUEUE_MAX = 30              # видео в работе и в очереди всего
PER_HOUR, PER_DAY = 40, 150     # кружков на человека
FLOOD, MUTE = 20, 600       # больше 20 сообщений в минуту — бот молчит 10 минут
PENDING_TTL = 30 * 60       # сколько ждём ответа «резать или нет», потом удаляем видео
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp", ".gif", ".mpeg", ".mpg", ".ts", ".flv")

# имя бота: по этим словам его находят в поиске Telegram (до 64 знаков)
NAME = ENV("KRUGI_NAME", "Кружок — видео в кружочек")
SHORT_ABOUT = "Превращаю видео в кружок. Бесплатно, без рекламы, водяных знаков и подписок на каналы."
ABOUT = (
    "Пришли видео — верну его кружком.\n\n"
    "Обрежу в круг по центру и сожму, чтобы не тормозил. "
    "Длиннее минуты — разрежу на кружки по минуте. "
    "Нужен другой кусок — подпиши видео временем, например 1:20.\n\n"
    "Бесплатно. Без рекламы, водяных знаков, регистрации и «подпишись на 4 канала». "
    "Видео не храню: сделал кружок — удалил."
)
HELLO = (
    "<b>Пришли видео — сделаю из него кружок.</b>\n\n"
    "Обрежу в круг по центру и сожму, чтобы не тормозил. "
    "Длиннее минуты — разрежу на несколько кружков.\n\n"
    "Нужно не с начала — подпиши видео временем: <code>1:20</code>.\n\n"
    "Отправить кружок от себя: Переслать → выбери чат → нажми на пересылаемое сообщение "
    "→ «Скрыть имя отправителя».\n\n"
    "Бесплатно, без рекламы и водяных знаков. Видео не храню."
)
HINT = "Пришли видео (можно файлом) — сделаю из него кружок."
NO_LINKS = ("Ссылки не открываю — пришли сам файл видео.\n\n"
            "Скачать видео по ссылке из TikTok, YouTube или Instagram умеет @kachalka_videobot.")
IS_NOTE = "Это уже кружок 🙂 Пришли обычное видео."
IS_PHOTO = "Кружок делается из видео, а это фото. Пришли видео."
TOO_BIG = ("Видео {mb} МБ, а Telegram даёт ботам скачать файл только до {max} МБ.\n\n"
           "Обрежь видео в галерее до нужного куска или сожми — и пришли ещё раз.")
FORBIDDEN = ("Не могу прислать кружок: у тебя в настройках закрыты видеосообщения. "
             "Присылаю обычным видео.\n\n"
             "Открыть: Настройки → Конфиденциальность → Голосовые сообщения → Все.")
BROKEN = "Что-то сломалось у меня. Попробуй ещё раз через минуту."
GONE = "Это видео я уже удалил. Пришли его ещё раз."
STARS = (10, 50, 100, 500)
DONATE = (
    "Бот бесплатный и таким останется: без рекламы, подписок и платных функций.\n\n"
    "Если пригодился и хочется сказать спасибо — кинь звёздочку. "
    "Это добровольно и ни на что не влияет, всё работает одинаково для всех."
)
PAYSUPPORT = (
    "Звёзды здесь — просто спасибо, за них ничего не покупается и не открывается. "
    "Кинул случайно или передумал — нажми кнопку, верну последний донат."
)

THANKS = DATA / "thanks.jsonl"      # кто и сколько звёзд кинул (для возврата)
STATS = DATA / "stats.json"         # сколько кружков сделано и сколько людей (номера людей — только хешем)

lock = threading.Lock()
lines = {}                  # чат → очередь его видео (делаются по одному)
pending = {}                # номер → видео длиннее минуты, ждёт ответа «резать или нет»
people = {}                 # человек → когда писал, когда получал кружки, до скольки молчим
star_links = {}
bot_name = ""


class TgError(Exception):
    def __init__(self, text, code=0):
        super().__init__(text)
        self.code = code


def read_token():
    token = ENV("KRUGI_TOKEN")
    if not token:
        try:
            token = (DATA / "bot.token").read_text("utf-8").strip()
        except OSError:
            pass
    if not token:
        sys.exit(f"Нет токена бота: положи его в {DATA / 'bot.token'}")
    return token


TOKEN = read_token()


def multipart(fields, files):
    boundary = uuid.uuid4().hex
    chunks = []
    for k, v in fields.items():
        v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (name, data, ctype) in files.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                      f"Content-Type: {ctype}\r\n\r\n".encode())
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


# сбой связи: urllib, TLS и разорванный ответ — всё, что не ответ Telegram
NET = (OSError, http.client.HTTPException, ValueError)


def tg(method, files=None, timeout=30, tries=3, once=False, **params):
    """Вызов Telegram Bot API. Сам ждёт, если Telegram просит притормозить.
    once — отправка файла: после сбоя связи не повторяем, кружок мог уже дойти (иначе придёт два)."""
    params = {k: v for k, v in params.items() if v is not None}
    if files:
        body, ctype = multipart(params, files)
    else:
        body, ctype = json.dumps(params, ensure_ascii=False).encode(), "application/json"
    for attempt in range(tries):
        req = urllib.request.Request(f"{TG_API}/bot{TOKEN}/{method}", data=body, headers={"Content-Type": ctype})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=timeout, context=TLS).read())
        except urllib.error.HTTPError as e:
            try:
                r = json.loads(e.read() or b"{}")
            except ValueError:
                r = {}
            r.setdefault("error_code", e.code)
            r.setdefault("description", str(e))
        except NET as e:
            if attempt == tries - 1 or once:
                raise TgError(str(e))
            time.sleep(2 + attempt * 3)
            continue
        if r.get("ok"):
            return r.get("result")
        wait = (r.get("parameters") or {}).get("retry_after")
        if wait and attempt < tries - 1:
            time.sleep(min(wait, 60) + 1)
            continue
        raise TgError(r.get("description", "ошибка Telegram"), r.get("error_code", 0))
    raise TgError("Telegram не ответил")


def quiet(method, **params):
    """Вызов, ошибка которого ничего не ломает (поправить статус, убрать сообщение)."""
    try:
        return tg(method, tries=1, **params)
    except TgError:
        return None


def reply_to(message_id):
    return {"message_id": message_id, "allow_sending_without_reply": True}


def clock(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def plural(n, one, few, many):
    n10, n100 = n % 10, n % 100
    return one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many


class Status:
    """Одно сообщение «что сейчас происходит», которое бот правит по ходу дела, а в конце убирает."""

    def __init__(self, chat, reply_id, text):
        self.chat, self.text, self.last = chat, text, 0.0
        self.id = tg("sendMessage", chat_id=chat, text=text, reply_parameters=reply_to(reply_id))["message_id"]

    def set(self, text, markup=None, force=True):
        if text == self.text and not markup:
            return
        if not force and time.monotonic() - self.last < 1.5:    # Telegram не любит частые правки
            return
        self.text, self.last = text, time.monotonic()
        quiet("editMessageText", chat_id=self.chat, message_id=self.id, text=text, parse_mode="HTML",
              reply_markup=markup)

    def drop(self):
        quiet("deleteMessage", chat_id=self.chat, message_id=self.id)


def bar(share):
    k = round(share * 10)
    return "●" * k + "○" * (10 - k) + f" {round(share * 100)}%"


# --- люди и лимиты ---

def person(user):
    p = people.get(user)
    if not p:
        p = people[user] = {"said": deque(), "made": deque(), "muted": 0.0, "told": {}}
    return p


def flood(user):
    """True — человек пишет слишком часто, и бот какое-то время его не слышит."""
    now = time.time()
    with lock:
        p = person(user)
        if p["muted"] > now:
            return True
        said = p["said"]
        said.append(now)
        while said and said[0] < now - 60:
            said.popleft()
        if len(said) > FLOOD:
            p["muted"] = now + MUTE
            said.clear()
            return "mute"
    return False


def fresh(user, kind, every):
    """Не повторять одну и ту же подсказку чаще, чем раз в every секунд."""
    now = time.time()
    with lock:
        told = person(user)["told"]
        if now - told.get(kind, 0) < every:
            return False
        told[kind] = now
        return True


def made_recently(user):
    now = time.time()
    with lock:
        made = person(user)["made"]
        while made and made[0] < now - 86400:
            made.popleft()
        return sum(t > now - 3600 for t in made), len(made)


def limit_text(user, want=1):
    hour, day = made_recently(user)
    if day + want > PER_DAY:
        return f"На сегодня хватит: ты сделал {day} {plural(day, 'кружок', 'кружка', 'кружков')}. Приходи завтра."
    if hour + want > PER_HOUR:
        return f"За час ты сделал {hour} {plural(hour, 'кружок', 'кружка', 'кружков')}. Передохни немного и приходи."
    return None


def count_note(user):
    with lock:
        person(user)["made"].append(time.time())
        try:
            stats = json.loads(STATS.read_text("utf-8"))
        except (OSError, ValueError):
            stats = {"notes": 0, "users": []}
        stats["notes"] += 1
        h = hashlib.sha256(f"krugi:{user}".encode()).hexdigest()[:16]
        if h not in stats["users"]:
            stats["users"].append(h)
        tmp = STATS.with_suffix(".tmp")
        tmp.write_text(json.dumps(stats), "utf-8")
        tmp.replace(STATS)


# --- очередь ---

def queued():
    return sum(len(q) for q in lines.values())


def enqueue(chat, task):
    """Поставить видео в очередь чата. Вернёт текст отказа или None."""
    with lock:
        line = lines.get(chat)
        if line is not None and len(line) >= LINE:
            return f"У тебя уже {len(line)} видео в работе. Дождись их — и присылай ещё."
        if queued() >= QUEUE_MAX:
            return "Сейчас много желающих. Пришли видео ещё раз через пару минут."
        if line is None:
            line = lines[chat] = deque()
            threading.Thread(target=run_line, args=(chat,), daemon=True).start()
        line.append(task)
    return None


def run_line(chat):
    """Видео одного чата делаем по очереди, а разных чатов — разом (но не больше JOBS)."""
    while True:
        with lock:
            line = lines[chat]
            if not line:
                del lines[chat]
                return
            task = line[0]
        try:
            if not JOBS.acquire(blocking=False):
                task["status"].set("Жду своей очереди — сейчас делаю кружки другим…")
                JOBS.acquire()
            try:
                work(task)
            finally:
                JOBS.release()
        except Exception as e:      # что бы ни случилось — очередь чата не встаёт
            print("work:", type(e).__name__, e, flush=True)
            task["status"].set(BROKEN)
        finally:
            with lock:
                lines[chat].popleft()


# --- работа ---

def fetch(file_id, size):
    """Скачать видео у Telegram. Со своим сервером Telegram файл уже на диске — просто путь к нему."""
    try:
        f = tg("getFile", file_id=file_id, timeout=120 if TG_LOCAL else 30)
    except TgError as e:
        if "too big" in str(e).lower():
            raise video.VideoError(TOO_BIG.format(mb=round(size / 1048576), max=IN_MAX // 1048576))
        raise
    path = f.get("file_path") or ""
    if TG_LOCAL and path.startswith("/"):
        return Path(path), False
    dst = WORK / f"{uuid.uuid4().hex}.src"
    url = f"{TG_API}/file/bot{TOKEN}/{urllib.parse.quote(path)}"
    got = 0
    try:
        with urllib.request.urlopen(url, timeout=60, context=TLS) as r, dst.open("wb") as out:
            while chunk := r.read(1 << 16):
                got += len(chunk)
                if got > IN_MAX:
                    raise video.VideoError(TOO_BIG.format(mb=round(got / 1048576), max=IN_MAX // 1048576))
                out.write(chunk)
    except BaseException:
        dst.unlink(missing_ok=True)
        raise
    return dst, True


def work(task):
    if task["kind"] == "cut":
        return render(task, task["src"], task["own"], task["info"], task["pieces"])
    status, chat = task["status"], task["chat"]
    status.set("Скачиваю видео…")
    try:
        src, own = fetch(task["file_id"], task["size"])
    except video.VideoError as e:
        return status.set(str(e))
    except (TgError, *NET) as e:
        print("fetch:", e, flush=True)
        return status.set("Не получилось скачать видео у Telegram. Пришли его ещё раз.")
    try:
        info = video.probe(src)
    except video.VideoError as e:
        if own:
            src.unlink(missing_ok=True)
        return status.set(str(e))
    duration, start, note = info["duration"], task["start"], ""
    if start and start >= duration - 0.5:
        note = f"В видео всего {clock(duration)}, поэтому начал с начала.\n\n"
        start = 0.0
    every = video.parts(duration, start, limit=1000)
    if len(every) <= 1:
        return render(task, src, own, info, every or [(start, video.PART)])
    # длиннее минуты — спросить: резать на кружки или хватит одного
    key = uuid.uuid4().hex[:10]
    n = min(len(every), MAX_PARTS)
    with lock:
        pending[key] = {**task, "kind": "cut", "src": src, "own": own, "info": info,
                        "all": every[:MAX_PARTS], "time": time.time()}
    whole = f"на {n} {plural(n, 'кружок', 'кружка', 'кружков')}" if n == len(every) else \
        f"на первые {n} кружков (это {n} минут)"
    first = "Только первую минуту" if not start else f"Только минуту с {clock(start)}"
    status.set(f"{note}Видео длиной {clock(duration - start)}, а в кружок влезает только минута.",
               markup={"inline_keyboard": [[{"text": f"✂️ Разрезать {whole}", "callback_data": f"cut:{key}:all"}],
                                           [{"text": first, "callback_data": f"cut:{key}:one"}]]})


def render(task, src, own, info, pieces):
    status, chat, user = task["status"], task["chat"], task["user"]
    total = len(pieces)
    forbidden = False
    try:
        for i, (start, length) in enumerate(pieces, 1):
            why = limit_text(user)
            if why:
                return status.set(why)
            label = "Делаю кружок" if total == 1 else f"Делаю кружок {i} из {total}"
            status.set(f"{label}\n{bar(0)}")
            dst = WORK / f"{uuid.uuid4().hex}.mp4"
            try:
                res = video.make_note(src, dst, info, start, length,
                                      progress=lambda s: status.set(f"{label}\n{bar(s)}", force=False))
                forbidden = send_note(chat, dst, res, task["msg_id"], forbidden)
            finally:
                dst.unlink(missing_ok=True)
            count_note(user)
        status.drop()
    except video.VideoError as e:
        status.set(str(e))
    except TgError as e:
        print("send:", e, flush=True)
        status.set("Telegram не принял кружок. Попробуй ещё раз через минуту.")
    finally:
        if own:
            src.unlink(missing_ok=True)


def send_note(chat, path, res, reply_id, forbidden):
    """Отправить кружок. Если у человека закрыты видеосообщения — обычным видео (объяснив один раз)."""
    data = path.read_bytes()
    if not forbidden:
        quiet("sendChatAction", chat_id=chat, action="upload_video_note")
        try:
            tg("sendVideoNote", files={"video_note": ("krug.mp4", data, "video/mp4")}, chat_id=chat,
               duration=max(1, round(res["duration"])), length=res["side"], timeout=120, once=True)
            return False
        except TgError as e:
            if "VOICE_MESSAGES_FORBIDDEN" not in str(e) and "video messages" not in str(e).lower():
                raise
        tg("sendMessage", chat_id=chat, text=FORBIDDEN, reply_parameters=reply_to(reply_id))
    tg("sendVideo", files={"video": ("krug.mp4", data, "video/mp4")}, chat_id=chat,
       duration=max(1, round(res["duration"])), width=res["side"], height=res["side"],
       supports_streaming=True, timeout=120, once=True)
    return True


def janitor():
    """Раз в минуту: убрать видео, по которым так и не ответили «резать или нет»."""
    while True:
        time.sleep(60)
        now = time.time()
        with lock:
            old = [k for k, p in pending.items() if now - p["time"] > PENDING_TTL]
            gone = [pending.pop(k) for k in old]
        for p in gone:
            if p["own"]:
                p["src"].unlink(missing_ok=True)
            p["status"].set("Видео удалил, не дождавшись ответа. Пришли ещё раз, если нужно.")


# --- сообщения ---

def media_of(msg):
    """Видео из сообщения: само видео, гифка или файл-видео. → (file_id, размер, имя) или None."""
    for kind in ("video", "animation"):
        if msg.get(kind):
            return msg[kind]["file_id"], msg[kind].get("file_size") or 0
    doc = msg.get("document")
    if doc and (str(doc.get("mime_type", "")).startswith("video/") or
                str(doc.get("file_name", "")).lower().endswith(VIDEO_EXT)):
        return doc["file_id"], doc.get("file_size") or 0
    return None


def on_message(msg):
    chat = msg["chat"]["id"]
    user = (msg.get("from") or {}).get("id", chat)
    if msg.get("successful_payment"):
        return on_paid(msg)
    if msg["chat"].get("type") != "private":
        return      # бот живёт в личке: в группах молчит
    text = msg.get("text") or ""
    cmd = (text.split() or [""])[0].lower().partition("@")[0]
    if cmd in ("/start", "/help"):
        if fresh(user, "start", 5):
            tg("sendMessage", chat_id=chat, text=HELLO, parse_mode="HTML")
        return
    if cmd == "/donate":
        if fresh(user, cmd, 5):
            donate(chat)
        return
    if cmd == "/paysupport":
        tg("sendMessage", chat_id=chat, text=PAYSUPPORT, reply_markup={"inline_keyboard": [[
            {"text": "Вернуть звёзды", "callback_data": "refund:"}]]})
        return
    found = media_of(msg)
    if not found:
        answer = IS_NOTE if msg.get("video_note") else IS_PHOTO if msg.get("photo") else \
            NO_LINKS if "http://" in text or "https://" in text or "t.me/" in text else HINT
        if fresh(user, answer[:10], 20):
            tg("sendMessage", chat_id=chat, text=answer, reply_parameters=reply_to(msg["message_id"]))
        return
    file_id, size = found
    why = TOO_BIG.format(mb=round(size / 1048576), max=IN_MAX // 1048576) if size > IN_MAX else limit_text(user)
    if why:
        tg("sendMessage", chat_id=chat, text=why, reply_parameters=reply_to(msg["message_id"]))
        return
    task = {"kind": "video", "chat": chat, "user": user, "msg_id": msg["message_id"], "file_id": file_id,
            "size": size, "start": video.start_from(msg.get("caption"))}
    task["status"] = Status(chat, msg["message_id"], "Принял видео…")
    why = enqueue(chat, task)
    if why:
        task["status"].set(why)


def on_button(q):
    data = q.get("data") or ""
    if data == "refund:":
        return refund(q)
    if not data.startswith("cut:"):
        return quiet("answerCallbackQuery", callback_query_id=q["id"])
    _, key, how = data.split(":", 2)
    with lock:
        task = pending.get(key)
        if task and task["user"] != q["from"]["id"]:
            task = None
        elif task:
            del pending[key]
    if not task:
        return quiet("answerCallbackQuery", callback_query_id=q["id"], text=GONE, show_alert=True)
    quiet("answerCallbackQuery", callback_query_id=q["id"])
    hour, day = made_recently(task["user"])
    task["pieces"] = (task["all"] if how == "all" else task["all"][:1])[:max(1, min(PER_HOUR - hour, PER_DAY - day))]
    task["status"].set("В очереди…")
    why = enqueue(task["chat"], task)
    if why:
        if task["own"]:
            task["src"].unlink(missing_ok=True)
        task["status"].set(why)


# --- звёзды ---

def donate(chat):
    """Кнопки «⭐ 10 / 50 / 100 / 500». Каждая сразу открывает оплату звёздами."""
    for n in STARS:
        if n not in star_links:
            star_links[n] = tg("createInvoiceLink", title="Спасибо Кружку", payload=f"thanks:{n}",
                               description="Добровольное спасибо, ничего не покупается. "
                                           "Передумал — /paysupport, верну.",
                               currency="XTR", prices=[{"label": "Спасибо", "amount": n}])
    tg("sendMessage", chat_id=chat, text=DONATE, reply_markup={"inline_keyboard": [[
        {"text": f"⭐ {n}", "url": star_links[n]} for n in STARS]]})


def thanks_log():
    try:
        return [json.loads(line) for line in THANKS.read_text("utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def on_paid(msg):
    pay, user = msg["successful_payment"], msg["from"]["id"]
    with lock:
        with THANKS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"user": user, "stars": pay["total_amount"], "time": int(time.time()),
                                "charge": pay["telegram_payment_charge_id"]}) + "\n")
    print(f"Донат: {pay['total_amount']} ⭐", flush=True)
    tg("sendMessage", chat_id=msg["chat"]["id"], text="Спасибо! ❤️ Звёздочка долетела, автору приятно.")


def refund(q):
    user = q["from"]["id"]
    with lock:
        log = thanks_log()
        last = next((t for t in reversed(log) if t["user"] == user and not t.get("refunded")), None)
        if last:
            try:
                tg("refundStarPayment", user_id=user, telegram_payment_charge_id=last["charge"])
            except TgError:
                last = None
            else:
                last["refunded"] = True
                THANKS.write_text("".join(json.dumps(t) + "\n" for t in log), "utf-8")
    quiet("answerCallbackQuery", callback_query_id=q["id"], show_alert=True,
          text=f"Вернул {last['stars']} ⭐" if last else "Возвращать нечего — донатов от тебя не нашёл.")


# --- главный цикл ---

def sender(update):
    for kind in ("message", "callback_query", "pre_checkout_query"):
        if kind in update:
            return (update[kind].get("from") or {}).get("id")
    return None


def handle(update):
    try:
        if "message" in update:
            on_message(update["message"])
        elif "callback_query" in update:
            on_button(update["callback_query"])
        elif "pre_checkout_query" in update:     # звёзды: Telegram ждёт «да» не дольше 10 секунд
            q = update["pre_checkout_query"]
            ok = q.get("currency") == "XTR" and str(q.get("invoice_payload", "")).startswith("thanks:")
            tg("answerPreCheckoutQuery", pre_checkout_query_id=q["id"], ok=ok,
               error_message=None if ok else "Этот платёж не от Кружка.")
    except Exception as e:
        print("update:", type(e).__name__, e, flush=True)


def setup():
    """Имя, описание и меню бота — то, что человек видит до первого сообщения."""
    me = tg("getMe")

    def texts():    # Telegram отвечает на это медленно — не держим из-за них приём сообщений
        if (quiet("getMyName") or {}).get("name") != NAME:     # имя Telegram даёт менять редко
            quiet("setMyName", name=NAME)
        quiet("setMyCommands", commands=[{"command": "start", "description": "Как пользоваться"},
                                         {"command": "donate", "description": "Сказать спасибо звёздочкой"}])
        quiet("setMyShortDescription", short_description=SHORT_ABOUT)
        quiet("setMyDescription", description=ABOUT)
    threading.Thread(target=texts, daemon=True).start()
    return me


def tg_updates(offset):
    """Долгий опрос: Telegram держит запрос до 50 секунд, пока не появится сообщение."""
    body = json.dumps({"offset": offset, "timeout": 50,
                       "allowed_updates": ["message", "callback_query", "pre_checkout_query"]})
    req = urllib.request.Request(f"{TG_API}/bot{TOKEN}/getUpdates", data=body.encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=70, context=TLS).read())
    except urllib.error.HTTPError as e:
        raise TgError(f"HTTP {e.code}", e.code)
    except NET as e:
        raise TgError(str(e))
    if not r.get("ok"):
        raise TgError(r.get("description", ""), r.get("error_code", 0))
    return r["result"]


def main():
    global bot_name
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("Нужен ffmpeg (brew install ffmpeg / apt install ffmpeg)")
    DATA.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    for old in WORK.iterdir():          # недоделанное с прошлого запуска
        if old.suffix in (".src", ".mp4"):
            old.unlink(missing_ok=True)
    while True:
        try:
            bot_name = setup().get("username")
            break
        except TgError as e:
            if e.code == 401:
                sys.exit("Telegram не принял токен — проверь bot.token")
            print("Telegram недоступен:", e, flush=True)
            time.sleep(15)
    print(f"Бот @{bot_name} запущен, Telegram: "
          f"{'свой сервер' if TG_LOCAL else 'обычный'}, видео до {IN_MAX // 1048576} МБ", flush=True)
    threading.Thread(target=janitor, daemon=True).start()
    offset = 0
    while True:
        try:
            updates = tg_updates(offset)
        except TgError as e:
            if e.code == 401:
                sys.exit("Telegram не принял токен — проверь bot.token")
            print("getUpdates:", e, flush=True)
            time.sleep(5)
            continue
        for u in updates or []:
            offset = max(offset, u["update_id"] + 1)
            user = sender(u)
            if user is None:
                continue
            if "pre_checkout_query" not in u:
                muted = flood(user)
                if muted == "mute" and "message" in u:
                    quiet("sendMessage", chat_id=u["message"]["chat"]["id"],
                          text="Слишком много сообщений подряд. Передохну 10 минут — потом продолжим.")
                if muted:
                    continue
            threading.Thread(target=handle, args=(u,), daemon=True).start()


def set_avatar(path=AVATAR):
    """Аватарка бота. Telegram копит аватарки, поэтому старую сначала убираем."""
    quiet("removeMyProfilePhoto")
    tg("setMyProfilePhoto", files={"avatar": (path.name, path.read_bytes(), "image/jpeg")},
       photo=json.dumps({"type": "static", "photo": "attach://avatar"}))
    print("Аватарка поставлена:", path.name)


if __name__ == "__main__":
    if sys.argv[1:2] == ["avatar"]:
        set_avatar(Path(sys.argv[2]) if len(sys.argv) > 2 else AVATAR)
    else:
        main()
