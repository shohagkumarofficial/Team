"""
╔══════════════════════════════════════════════════════════════╗
║        🚀 MULTI-ADMIN FILE SHARE BOT v7.0                    ║
║   Owner → Admin (request/approve) → User                     ║
║   JSON Storage · No Firebase/Mongo/Web-Panel                 ║
║   Fixed Channels (ENV) · Per-Admin Shortener Earning          ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, re, time, json, uuid, threading, requests, telebot, logging, io
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from urllib.parse import quote
from http.server import BaseHTTPRequestHandler, HTTPServer
try:
    from PIL import Image
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

# ══════════════════════════════════════════════════
#  লগিং
# ══════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════
#  কনফিগারেশন (.env থেকে)
# ══════════════════════════════════════════════════
BOT_TOKEN      = os.environ.get("BOT_TOKEN")
BOT_USERNAME   = os.environ.get("BOT_USERNAME", "YourBotUsername")
MAIN_ADMIN_ID  = str(os.environ.get("MAIN_ADMIN_ID", "5991854507"))  # Owner chat_id

# ফিক্সড চ্যানেল — শুধু owner env থেকে সেট করবে, admin রা এটা দেখতে/বদলাতে পারবে না
AD_CHANNEL_IDS      = [c.strip() for c in os.environ.get("AD_CHANNEL_IDS", "").split(",") if c.strip()]
PREMIUM_CHANNEL_IDS = [c.strip() for c in os.environ.get("PREMIUM_CHANNEL_IDS", "").split(",") if c.strip()]
LOG_CHANNEL_ID       = os.environ.get("LOG_CHANNEL_ID", "").strip()

SHORTENER_API_BASE = os.environ.get("SHORTENER_API_BASE", "https://teraboxlinks.com/api")

DATA_FILE   = os.environ.get("DATA_FILE", os.path.join(os.path.dirname(__file__), "data", "database.json"))
BOT_VERSION = "7.0.0"

bot = telebot.TeleBot(BOT_TOKEN or "DUMMY_TOKEN", parse_mode="HTML")

# ══════════════════════════════════════════════════
#  Cloudflare D1 ও JSON ডাটাবেস
# ══════════════════════════════════════════════════
CF_ACCOUNT_ID     = os.environ.get("CF_ACCOUNT_ID", "").strip()
CF_D1_DATABASE_ID = os.environ.get("CF_D1_DATABASE_ID", "").strip()
CF_API_TOKEN      = os.environ.get("CF_API_TOKEN", "").strip()

def is_cf_enabled():
    return bool(CF_ACCOUNT_ID and CF_D1_DATABASE_ID and CF_API_TOKEN)

_db_lock = threading.RLock()
_cf_dirty_sections = set()
_cf_sync_lock = threading.Lock()
_cf_sync_event = threading.Event()

def _empty_db():
    return {
        "users": {},            # chat_id -> user dict
        "files": {},             # uid -> file dict
        "queue": [],             # auto-delete queue
        "scheduled": {},         # sched_id -> scheduled post
        "admin_requests": {},    # chat_id -> request dict
        "force_sub": {},         # fs_id -> channel dict
        "ad_channels": {},       # ad_id -> {id,name,channel_id,added_by} — Admin-দের যোগ করা Ads চ্যানেল
        "custom_buttons": {},    # btn_id -> {id,text,url,status,added_by} — পোস্টের সাথে যাওয়ার কাস্টম বাটন
        "settings": {},          # key -> value
        "stats": {},             # date -> counters
    }

def _cf_d1_query(sql, params=None):
    """Cloudflare D1 REST API-তে কোয়েরি পাঠায়।"""
    if not is_cf_enabled():
        return None
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_D1_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json"
    }
    body = {"sql": sql, "params": params or []}
    try:
        r = requests.post(url, headers=headers, json=body, timeout=12)
        data = r.json()
        if data.get("success"):
            res = data.get("result", [])
            if res and isinstance(res, list):
                return res[0].get("results", [])
            return []
        else:
            logger.error(f"Cloudflare D1 query failed: {data.get('errors')}")
            return None
    except Exception as e:
        logger.error(f"Cloudflare D1 request error: {e}")
        return None

def _init_cf_d1():
    """Cloudflare D1 টেবিল তৈরি/ইনিশিয়ালাইজ করে।"""
    if not is_cf_enabled():
        return False
    sql = """
    CREATE TABLE IF NOT EXISTS bot_sections (
        section TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """
    res = _cf_d1_query(sql)
    if res is not None:
        logger.info("✅ Cloudflare D1 database connected and initialized!")
        return True
    else:
        logger.warning("⚠️ Cloudflare D1 connection failed. Falling back to local JSON.")
        return False

def _cf_save_section_direct(section, val):
    """সরাসরি Cloudflare D1-এ একটি সেকশন সেভ করে।"""
    sql = """
    INSERT INTO bot_sections (section, data, updated_at)
    VALUES (?, ?, ?)
    ON CONFLICT(section) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at;
    """
    raw_json = json.dumps(val, ensure_ascii=False)
    now_str = datetime.now().isoformat()
    return _cf_d1_query(sql, [section, raw_json, now_str])

def _load_db():
    data = _empty_db()
    loaded_from_cf = False

    if is_cf_enabled():
        try:
            if _init_cf_d1():
                rows = _cf_d1_query("SELECT section, data FROM bot_sections;")
                if rows:
                    for row in rows:
                        sec = row.get("section")
                        raw = row.get("data")
                        if sec and raw:
                            try:
                                data[sec] = json.loads(raw)
                            except Exception as e:
                                logger.error(f"Failed to parse section {sec} from CF: {e}")
                    logger.info(f"✅ Loaded {len(rows)} sections from Cloudflare D1!")
                    loaded_from_cf = True
                else:
                    logger.info("Cloudflare D1 is currently empty.")
        except Exception as e:
            logger.error(f"Error loading from Cloudflare D1: {e}")

    # যদি ক্লাউডফ্লেয়ার সক্রিয় না থাকে বা খালি থাকে, তখন লোকাল ফাইল থেকে লোড হবে
    if not loaded_from_cf and os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                local_data = json.load(f)
            for k, v in local_data.items():
                if k in data:
                    data[k] = v
            logger.info("Loaded database from local JSON file.")

            # যদি ক্লাউডফ্লেয়ার যুক্ত থাকে কিন্তু D1 খালি ছিল, তাহলে লোকাল ডাটা স্বয়ংক্রিয়ভাবে D1-এ আপলোড হবে (Auto-Migration)
            if is_cf_enabled():
                logger.info("☁️ Auto-migrating local database to Cloudflare D1...")
                for sec, val in data.items():
                    _cf_save_section_direct(sec, val)
        except Exception as e:
            logger.error(f"DB load error, starting fresh: {e}")

    for k, v in _empty_db().items():
        data.setdefault(k, v)
    return data

DB = _load_db()

def save_db(section=None):
    with _db_lock:
        os.makedirs(os.path.dirname(DATA_FILE) or ".", exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(DB, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)

        # ক্লাউডফ্লেয়ারে ব্যাকগ্রাউন্ড সিঙ্কের জন্য ডার্টি সেকশন চিহ্নিত করা
        if is_cf_enabled():
            with _cf_sync_lock:
                if section:
                    _cf_dirty_sections.add(section)
                else:
                    for k in DB.keys():
                        _cf_dirty_sections.add(k)
            _cf_sync_event.set()

def _cf_sync_worker():
    """ব্যাকগ্রাউন্ডে ক্লাউডফ্লেয়ারে পরিবর্তিত সেকশনগুলো সেভ করে (বটের গতিতে কোনো প্রভাব ফেলে না)।"""
    while True:
        _cf_sync_event.wait(timeout=3)
        _cf_sync_event.clear()
        if not is_cf_enabled():
            time.sleep(5)
            continue

        with _cf_sync_lock:
            to_sync = list(_cf_dirty_sections)
            _cf_dirty_sections.clear()

        if not to_sync:
            continue

        for sec in to_sync:
            with _db_lock:
                val = DB.get(sec)
            if val is not None:
                res = _cf_save_section_direct(sec, val)
                if res is None:
                    # ব্যর্থ হলে পরবর্তী বারের জন্য তালিকায় ফিরিয়ে রাখা
                    with _cf_sync_lock:
                        _cf_dirty_sections.add(sec)
            time.sleep(0.3)  # রেট লিমিট নিরাপদ রাখতে হালকা বিরতি

# Owner কে সবসময় admins-এর মধ্যে ধরে নেওয়া হয়, ডাটাবেসে আলাদা করে রাখারও দরকার নেই।

# ══════════════════════════════════════════════════
#  গ্লোবাল সেটিংস হেল্পার
# ══════════════════════════════════════════════════
def get_setting(key, default=0):
    return DB["settings"].get(key, default)

def set_setting(key, value):
    with _db_lock:
        DB["settings"][key] = value
        save_db()

def toggle_setting(key):
    new = 0 if get_setting(key, 0) else 1
    set_setting(key, new)
    return new

def _ico(val):
    return "🟢" if val else "🔴"

# ══════════════════════════════════════════════════
#  ফিল্টার ইউটিলিটি
# ══════════════════════════════════════════════════
URL_RE = re.compile(r'(https?://[^\s]+|t\.me/[^\s]+|@[A-Za-z0-9_]{5,})', re.IGNORECASE)

def filter_links(text):
    if not text: return text
    return re.sub(r'\n{3,}', '\n\n', URL_RE.sub('', text)).strip()

def clean_html(text):
    if not text: return ""
    return re.sub(r'<[^>]+>', '', text)

def apply_filters(text, uploader_id):
    u = get_user(uploader_id)
    if not text: return text
    if u.get("text_filter"): return ""
    if u.get("link_filter"): return filter_links(text)
    return text

# ══════════════════════════════════════════════════
#  ইউজার হেল্পার / রোল সিস্টেম
# ══════════════════════════════════════════════════
_DEFAULTS = {
    "role": "user",              # "owner" | "admin" | "user"
    "linked_admin": "",          # এই ইউজার কোন admin এর অধীনে গণ্য (broadcast টার্গেটিং এর জন্য)
    "header": "", "footer": "",
    "post_title": "",
    "auto_delete": 0,
    "step": "none", "batch_id": "",
    "link_filter": 0, "text_filter": 0,
    "link_repeat_count": 1,
    "terabox_key": "",           # প্রতিটা admin এর নিজস্ব শর্টেনার API key (আর্নিং এর জন্য)
    "temp_media_id": "", "temp_media_type": "", "temp_thumb_id": "",
    "pending_link": "", "pending_short_link": "",
    "_adch_name": "",
    "_btn_text": "",
    "joined_at": "", "last_active": "",
    "total_downloads": 0, "total_uploads": 0,
}

def get_user(chat_id):
    chat_id = str(chat_id)
    now = datetime.now().isoformat()
    with _db_lock:
        user = DB["users"].get(chat_id)
        if not user:
            role = "owner" if chat_id == MAIN_ADMIN_ID else "user"
            user = {**_DEFAULTS, "chat_id": chat_id, "role": role, "joined_at": now, "last_active": now}
            DB["users"][chat_id] = user
            _inc_stat("new_users")
        else:
            for k, v in _DEFAULTS.items():
                if k not in user:
                    user[k] = v
            user["last_active"] = now
            if chat_id == MAIN_ADMIN_ID and user.get("role") != "owner":
                user["role"] = "owner"
        save_db()
        return user

def update_user(chat_id, updates):
    with _db_lock:
        u = DB["users"].setdefault(str(chat_id), {**_DEFAULTS, "chat_id": str(chat_id)})
        u.update(updates)
        save_db()

def update_step(chat_id, step):
    update_user(chat_id, {"step": step})

def is_owner(chat_id): return str(chat_id) == MAIN_ADMIN_ID
def is_admin(chat_id):
    u = get_user(chat_id)
    return u.get("role") in ("admin", "owner")
def role_of(chat_id): return get_user(chat_id).get("role", "user")

def all_admins():
    return [u for u in DB["users"].values() if u.get("role") == "admin"]

# ══════════════════════════════════════════════════
#  Ads চ্যানেল (env-ফিক্সড + Admin-দের বট থেকে যোগ করা)
# ══════════════════════════════════════════════════
def all_ad_channel_ids():
    """env-এর ফিক্সড AD_CHANNEL_IDS + Admin-রা বট থেকে যোগ করা চ্যানেল — ডুপ্লিকেট বাদে।"""
    extra = [c.get("channel_id") for c in DB.get("ad_channels", {}).values() if c.get("channel_id")]
    return list(dict.fromkeys(AD_CHANNEL_IDS + extra))

# ══════════════════════════════════════════════════
#  স্ট্যাটিস্টিক্স
# ══════════════════════════════════════════════════
def _inc_stat(field, n=1):
    today = datetime.now().strftime("%Y-%m-%d")
    with _db_lock:
        DB["stats"].setdefault(today, {})
        DB["stats"][today][field] = DB["stats"][today].get(field, 0) + n
        save_db()

def get_stats():
    today = datetime.now().strftime("%Y-%m-%d")
    td = DB["stats"].get(today, {})
    active = sum(1 for u in DB["users"].values() if u.get("last_active", "").startswith(today))
    return {
        "total_users":  len(DB["users"]),
        "total_files":  len(DB["files"]),
        "total_admins": len(all_admins()),
        "active_today": active,
        "dl_today":     td.get("downloads", 0),
        "ul_today":     td.get("uploads", 0),
    }

# ══════════════════════════════════════════════════
#  ফোর্স সাবস্ক্রাইব (Owner + Admin দুজনেই ম্যানেজ করতে পারবে)
# ══════════════════════════════════════════════════
def check_force_sub(chat_id):
    chs = [c for c in DB["force_sub"].values() if c.get("status") == "on"]
    if not chs: return True, []
    not_joined = []
    for ch in chs:
        try:
            m = bot.get_chat_member(ch['channel_id'], int(chat_id))
            if m.status in ['left', 'kicked']: not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return len(not_joined) == 0, not_joined

def send_force_sub_msg(chat_id, not_joined, file_key=None):
    mk = InlineKeyboardMarkup()
    for ch in not_joined:
        mk.add(InlineKeyboardButton(f"📢 {ch['name']} — Join করুন", url=ch['url']))
    mk.add(InlineKeyboardButton("✅ Join করেছি — যাচাই করুন", callback_data=f"check_sub_{file_key or 'none'}"))
    bot.send_message(chat_id, "🔒 <b>ফাইল পেতে নিচের চ্যানেলগুলোতে Join করুন!</b>\n\nJoin করার পর ✅ বাটনে ক্লিক করুন।", reply_markup=mk)

# ══════════════════════════════════════════════════
#  অটো-ডিলিট ওয়ার্কার
# ══════════════════════════════════════════════════
def _auto_delete_worker():
    while True:
        try:
            now = int(time.time())
            due = [q for q in DB["queue"] if q["delete_at"] <= now]
            for item in due:
                try:
                    bot.delete_message(item['chat_id'], item['message_id'])
                    bot.send_message(item['chat_id'], "⚠️ <b>সময় শেষ! ফাইলটি মুছে গেছে।</b>\n🔁 আবার পেতে লিংকে ক্লিক করুন।")
                except Exception as e:
                    logger.warning(f"AutoDelete: {e}")
            if due:
                with _db_lock:
                    DB["queue"] = [q for q in DB["queue"] if q not in due]
                    save_db()
        except Exception as e:
            logger.error(f"AutoDelete worker: {e}")
        time.sleep(10)

threading.Thread(target=_auto_delete_worker, daemon=True).start()

# ══════════════════════════════════════════════════
#  সিডিউল পোস্ট ওয়ার্কার
# ══════════════════════════════════════════════════
def _scheduled_post_worker():
    while True:
        try:
            now_iso = datetime.now().isoformat()
            due_ids = [sid for sid, s in DB["scheduled"].items()
                       if s.get("status") == "pending" and s.get("scheduled_at", "") <= now_iso]
            for sid in due_ids:
                item = DB["scheduled"].get(sid)
                if not item: continue
                try:
                    admin_id = item['admin_id']
                    user = get_user(admin_id)
                    _publish_to_channels(admin_id, user, item['media_type'], item['media_id'],
                                          item.get('d_link', ''), item.get('title', ''),
                                          manual_short_link=item.get('manual_short_link') or None,
                                          thumb_id=item.get('thumb_id', ''))
                    try:
                        bot.send_message(admin_id, "⏰ <b>সিডিউল পোস্ট সম্পন্ন হয়েছে!</b>")
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f"Scheduled post error: {e}")
                finally:
                    with _db_lock:
                        DB["scheduled"][sid]["status"] = "done"
                        save_db()
        except Exception as e:
            logger.error(f"Scheduled worker: {e}")
        time.sleep(20)

threading.Thread(target=_scheduled_post_worker, daemon=True).start()

# ══════════════════════════════════════════════════
#  শর্টেনার (প্রতিটা admin এর নিজস্ব key দিয়ে আর্নিং)
# ══════════════════════════════════════════════════
def get_short_link(url, terabox_key):
    if not terabox_key:
        return url  # key না থাকলে direct link-ই ফেরত যাবে
    try:
        r = requests.get(f"{SHORTENER_API_BASE}?api={terabox_key}&url={quote(url)}", timeout=8).json()
        if r and r.get("status") != "error" and r.get("shortenedUrl"):
            return r["shortenedUrl"]
    except Exception as e:
        logger.warning(f"ShortLink: {e}")
    return url

def _get_file_count_from_link(key):
    cnt = sum(1 for f in DB["files"].values() if f.get("batch_id") == key)
    if cnt: return cnt
    return sum(1 for f in DB["files"].values() if f.get("file_key") == key)

# ══════════════════════════════════════════════════
#  পোস্ট মার্কআপ বিল্ডার (Download 1 বাদ, শুধু একটাই Download বাটন)
# ══════════════════════════════════════════════════
def _build_post_markup(user, link, share_text):
    mk = InlineKeyboardMarkup()
    mk.row(InlineKeyboardButton("📥 ডাউনলোড", url=link))
    encoded = quote(share_text, safe='')
    mk.row(InlineKeyboardButton("🔗 শেয়ার করুন", url=f"https://t.me/share/url?url=&text={encoded}"))

    # কাস্টম বাটন (যদি মাস্টার সুইচ ON থাকে এবং বাটনের স্ট্যাটাস ON থাকে)
    if get_setting("custom_buttons_enabled", 1):
        for btn in DB.get("custom_buttons", {}).values():
            if btn.get("status") == "on" and btn.get("text") and btn.get("url"):
                mk.row(InlineKeyboardButton(btn["text"], url=btn["url"]))

    return mk

def _build_copy_button(link):
    mk = InlineKeyboardMarkup()
    btn_added = False
    try:
        if hasattr(telebot.types, 'CopyTextButton'):
            mk.add(InlineKeyboardButton("📋 এক ক্লিকে লিংক কপি করুন", copy_text=telebot.types.CopyTextButton(text=link)))
            btn_added = True
    except Exception as e:
        logger.warning(f"CopyTextButton error: {e}")
    if not btn_added:
        try:
            mk.add(InlineKeyboardButton("🔗 শেয়ার / কপি করুন", url=f"https://t.me/share/url?url={quote(link)}"))
        except Exception:
            pass
    return mk

def _send_media(ch_id, mtype, mid, caption, markup, protect=False, thumb_id=""):
    kw = {"caption": caption, "reply_markup": markup, "protect_content": protect}
    if thumb_id:
        try:
            return bot.send_photo(ch_id, thumb_id, **kw)
        except Exception as e:
            logger.warning(f"send_photo with thumb_id failed, trying document/fallback: {e}")
            try:
                return bot.send_document(ch_id, thumb_id, **kw)
            except Exception as e2:
                logger.warning(f"send_document with thumb_id failed: {e2}")
    if mtype == 'photo':      return bot.send_photo(ch_id, mid, **kw)
    elif mtype == 'video':    return bot.send_video(ch_id, mid, **kw)
    elif mtype == 'document': return bot.send_document(ch_id, mid, **kw)
    elif mtype == 'audio':    return bot.send_audio(ch_id, mid, **kw)
    else:                     return bot.send_message(ch_id, caption or "...", reply_markup=markup)

def _prepare_thumb_bytes(raw_bytes):
    """টেলিগ্রামের নিয়ম অনুযায়ী থাম্বনেইল ঠিক করে দেয়: max 320x320px এবং max 200KB, JPEG ফরম্যাটে।
    এই নিয়ম না মানলে টেলিগ্রাম কাস্টম থাম্বনেইল চুপচাপ বাতিল করে ভিডিও থেকে নিজে একটা বানিয়ে নেয় —
    এটাই এতদিন ভিডিও-ফ্রেম থাম্বনেইল দেখানোর আসল কারণ ছিল।
    """
    if not raw_bytes:
        return None
    if not _PIL_OK:
        logger.warning("Pillow ইনস্টল করা নেই — কাস্টম থাম্বনেইল রিসাইজ করা যাচ্ছে না। requirements.txt-এ Pillow যোগ করুন।")
        return None
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img = img.convert("RGB")
        img.thumbnail((320, 320))  # অনুপাত ঠিক রেখে max 320x320
        quality = 90
        while True:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            data = buf.getvalue()
            if len(data) <= 200 * 1024 or quality <= 30:
                return data
            quality -= 10
    except Exception as e:
        logger.warning(f"Thumbnail resize failed: {e}")
        return None

def _send_video_with_thumb(ch_id, file_id, thumb_id, **kw):
    """ভিডিও পাঠানোর সময় থাম্বনেইল যোগ করে।
    ⚠️ টেলিগ্রাম থাম্বনেইলের জন্য পুরনো file_id রিইউজ করতে দেয় না — এটা অবশ্যই নতুন করে আপলোড (raw bytes) করতে হয়,
    এবং সেটা অবশ্যই max 320x320px ও max 200KB হতে হবে — নাহলে টেলিগ্রাম সেটা চুপচাপ ইগনোর করে ভিডিও থেকে
    নিজেই একটা থাম্বনেইল বানিয়ে নেয়। তাই প্রতিবার পোস্ট করার সময় থাম্বনেইল ছবিটা ডাউনলোড করে, রিসাইজ/কম্প্রেস
    করে raw bytes হিসেবে পাঠানো হচ্ছে।
    """
    thumb_bytes = None
    if thumb_id:
        try:
            finfo = bot.get_file(thumb_id)
            raw = bot.download_file(finfo.file_path)
            thumb_bytes = _prepare_thumb_bytes(raw)
        except Exception as e:
            logger.warning(f"Thumbnail download failed: {e}")
            thumb_bytes = None

    if thumb_bytes:
        thumb_file = ("thumb.jpg", thumb_bytes)
        try:
            return bot.send_video(ch_id, file_id, thumbnail=thumb_file, **kw)
        except TypeError:
            try:
                return bot.send_video(ch_id, file_id, thumb=thumb_file, **kw)
            except Exception as e:
                logger.warning(f"send_video with custom thumb failed, sending without: {e}")
        except Exception as e:
            logger.warning(f"send_video with custom thumb failed, sending without: {e}")
    return bot.send_video(ch_id, file_id, **kw)

def _publish_to_channels(admin_id, user, mtype, mid, d_link, title, manual_short_link=None, thumb_id=""):
    """ফিক্সড Ad/Premium/Log চ্যানেলে (ENV থেকে) পোস্ট করে।"""
    ph = apply_filters(title or user.get("header", ""), admin_id)
    pf = apply_filters(user.get("footer", ""), admin_id)
    ph_t = f"{ph}\n\n" if ph else ""
    pf_t = f"\n\n{pf}" if pf else ""
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    protect = bool(get_setting("protect_content", 0))

    fkey = d_link.split("start=")[-1] if "start=" in d_link else ""
    file_count = _get_file_count_from_link(fkey)
    fc_txt = f"📁 <b>মোট ফাইল: {file_count}টি</b>\n" if file_count > 0 else ""

    if manual_short_link:
        # অ্যাডমিন নিজে শর্টেনার সাইট থেকে বানিয়ে দেওয়া লিংক — এটাই ব্যবহার হবে
        short_link = manual_short_link
    else:
        terabox_key = user.get("terabox_key", "")
        short_link = get_short_link(d_link, terabox_key)
        if not terabox_key and all_ad_channel_ids():
            try:
                bot.send_message(admin_id, "⚠️ আপনার TeraBox/শর্টেনার API key সেট করা নেই — আপাতত সরাসরি লিংক ব্যবহার হচ্ছে, আর্নিং হবে না।\n🔧 সেট করতে: ⚙️ সেটিংস → 🔗 শর্টেনার Key")
            except Exception:
                pass

    rpt = max(1, min(user.get("link_repeat_count", 1), 5))
    posted = 0

    # Ad চ্যানেল — monetized short link
    ad_caption = f"{ph_t}{fc_txt}⬇️ ডাউনলোড করতে নিচের বাটনে ক্লিক করুন\n\n<i>🕐 {now_str}</i>{pf_t}".strip()
    ad_markup = _build_post_markup(user, short_link, clean_html(ad_caption))
    for ch_id in all_ad_channel_ids():
        try:
            _send_media(ch_id, mtype, mid, ad_caption, ad_markup, protect, thumb_id)
            posted += 1
        except Exception as e:
            logger.warning(f"Ad channel post error [{ch_id}]: {e}")

    # Premium চ্যানেল — direct link (repeat)
    if PREMIUM_CHANNEL_IDS:
        pr_links = "\n".join([d_link] * rpt)
        pr_caption = f"{ph_t}{fc_txt}🔗 <b>Direct Download:</b>\n{pr_links}\n\n<i>🕐 {now_str}</i>{pf_t}".strip()
        pr_markup = _build_post_markup(user, d_link, clean_html(pr_caption))
        for ch_id in PREMIUM_CHANNEL_IDS:
            try:
                _send_media(ch_id, mtype, mid, pr_caption, pr_markup, protect, thumb_id)
                posted += 1
            except Exception as e:
                logger.warning(f"Premium channel post error [{ch_id}]: {e}")

    # Log চ্যানেল — শুধু ব্যাকআপ
    if LOG_CHANNEL_ID:
        try:
            log_cap = f"💾 <b>Backup</b> | 📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n👤 Uploader: <code>{admin_id}</code>"
            _send_media(LOG_CHANNEL_ID, mtype, mid, log_cap, None, False, thumb_id)
        except Exception as e:
            logger.warning(f"Log channel post error: {e}")

    _inc_stat("uploads")
    update_user(admin_id, {"total_uploads": user.get("total_uploads", 0) + 1})
    return posted

# ══════════════════════════════════════════════════
#  ফাইল ডেলিভারি (ইউজারকে)
# ══════════════════════════════════════════════════
def _deliver_files(chat_id, file_key, user):
    files = [f for f in DB["files"].values() if f.get("file_key") == file_key or f.get("batch_id") == file_key]
    if not files:
        bot.send_message(chat_id, "❌ <b>ফাইল পাওয়া যায়নি!</b>\nলিংকটি মেয়াদোত্তীর্ণ হতে পারে।")
        return

    bot.send_message(chat_id, f"⏳ {'ফাইলগুলো' if len(files) > 1 else 'ফাইলটি'} পাঠানো হচ্ছে...")
    uploader_id = files[0]['uploader']
    uploader = get_user(uploader_id)
    h_, f_ = uploader.get('header', ''), uploader.get('footer', '')
    caption = apply_filters(f"{h_}\n\n{f_}".strip() if (h_ or f_) else "", uploader_id)
    protect = bool(get_setting("protect_content", 0))

    delivered = 0
    for f in files:
        sent_id = None
        try:
            kw = {"caption": caption, "protect_content": protect}
            if f['type'] == 'document': res = bot.send_document(chat_id, f['file_id'], **kw)
            elif f['type'] == 'video':  res = _send_video_with_thumb(chat_id, f['file_id'], f.get('thumb_id', ''), **kw)
            elif f['type'] == 'photo':  res = bot.send_photo(chat_id, f['file_id'], **kw)
            elif f['type'] == 'audio':  res = bot.send_audio(chat_id, f['file_id'], **kw)
            else: res = None
            if res: sent_id = res.message_id; delivered += 1
        except Exception as e:
            logger.warning(f"Deliver error: {e}")

        if sent_id and uploader.get("auto_delete", 0) > 0:
            with _db_lock:
                DB["queue"].append({"chat_id": chat_id, "message_id": sent_id,
                                     "delete_at": int(time.time()) + uploader["auto_delete"] * 60})
                save_db()
        time.sleep(0.3)

    # প্রথমবার ফাইল ডাউনলোড করলে uploader-এর সাথে ইউজার লিংক হবে (referral না থাকলে)
    if delivered and not user.get("linked_admin"):
        linked_to = uploader_id if role_of(uploader_id) in ("admin", "owner") else ""
        if linked_to:
            update_user(chat_id, {"linked_admin": linked_to})

    if delivered:
        _inc_stat("downloads", delivered)
        update_user(chat_id, {"total_downloads": user.get("total_downloads", 0) + delivered})
        if uploader.get("auto_delete", 0) > 0:
            bot.send_message(chat_id, f"⚠️ <i>ফাইল{'গুলো' if delivered > 1 else 'টি'} <b>{uploader['auto_delete']} মিনিট</b> পর মুছে যাবে।</i>")
    else:
        bot.send_message(chat_id, "❌ ফাইল পাঠানো সম্ভব হয়নি।")

# ══════════════════════════════════════════════════
#  মেনু হেল্পার
# ══════════════════════════════════════════════════
def _mk(): return InlineKeyboardMarkup()
def _back(cb): return InlineKeyboardButton("🔙 ব্যাক", callback_data=cb)
def _btn(label, cb): return InlineKeyboardButton(label, callback_data=cb)

def _user_menu(chat_id):
    m = _mk()
    u = get_user(chat_id)
    role = u.get("role")
    if role == "user":
        # ইতিমধ্যে রিকোয়েস্ট পেন্ডিং কিনা চেক
        if chat_id in DB["admin_requests"] and DB["admin_requests"][chat_id].get("status") == "pending":
            m.add(_btn("⏳ রিকোয়েস্ট পেন্ডিং আছে", "noop"))
        else:
            m.add(_btn("🔑 Admin Access Request করুন", "req_admin"))
    return m

def _admin_menu():
    m = _mk()
    m.row(_btn("📤 আপলোড শুরু করুন", "start_upload"), _btn("📦 ব্যাচ আপলোড", "start_batch"))
    m.row(_btn("⚙️ সেটিংস", "menu_settings"), _btn("📊 আমার স্ট্যাটস", "show_stats"))
    m.row(_btn("⏰ সিডিউল পোস্ট", "menu_schedule"), _btn("🔒 ফোর্স-সাব", "menu_forcesub"))
    m.row(_btn("📢 Ads চ্যানেল", "menu_adchannels"), _btn("🔘 কাস্টম বাটন", "menu_buttons"))
    m.add(_btn("🔗 আমার রেফারেল লিংক", "my_referral"))
    return m

def _owner_menu():
    m = _admin_menu()
    m.row(_btn("👑 Admin Requests", "menu_requests"), _btn("👥 Admin লিস্ট", "menu_admins"))
    m.row(_btn("📢 ব্রডকাস্ট", "menu_broadcast"), _btn("🔐 Protect Content", "toggle_protect"))
    m.row(_btn("☁️ Cloudflare DB স্ট্যাটাস", "cf_status"))
    return m

def _settings_menu(u):
    lf, tf = _ico(u.get("link_filter")), _ico(u.get("text_filter"))
    return (
        f"⚙️ <b>সেটিংস</b>\n{'─'*24}\n"
        f"📝 হেডার: <code>{u.get('header') or 'নেই'}</code>\n"
        f"📝 ফুটার: <code>{u.get('footer') or 'নেই'}</code>\n"
        f"⏱️ অটো-ডিলিট: <b>{u.get('auto_delete',0)} মিনিট</b> (0 = বন্ধ)\n"
        f"🔄 লিংক রিপিট: <b>{u.get('link_repeat_count',1)}x</b>\n"
        f"🔗 লিংক ফিল্টার: {lf} | 📝 টেক্সট ফিল্টার: {tf}\n"
        f"🔑 শর্টেনার Key: {'✅ সেট করা আছে' if u.get('terabox_key') else '❌ সেট করা নেই'}\n"
    ), InlineKeyboardMarkup(row_width=2).add(
        _btn("📝 হেডার সেট", "set_header"), _btn("📝 ফুটার সেট", "set_footer")
    ).add(
        _btn("⏱️ অটো-ডিলিট", "set_autodelete"), _btn("🔄 লিংক রিপিট", "set_linkrepeat")
    ).add(
        _btn(f"🔗 লিংক ফিল্টার {lf}", "tog_linkfilter"), _btn(f"📝 টেক্সট ফিল্টার {tf}", "tog_textfilter")
    ).add(
        _btn("🔑 শর্টেনার Key সেট", "set_terabox")
    ).add(_back("main_menu"))

def _show_main_menu(chat_id, edit_msg_id=None):
    u = get_user(chat_id)
    role = u.get("role")
    s = get_stats()
    header = (f"╔══════════════════════════╗\n║   🤖 <b>এডমিন প্যানেল</b>   ║\n╚══════════════════════════╝\n\n"
              f"👥 মোট ইউজার : <b>{s['total_users']}</b>\n📁 মোট ফাইল  : <b>{s['total_files']}</b>\n"
              f"🟢 আজ সক্রিয় : <b>{s['active_today']}</b>\n📥 আজ ডাউনলোড: <b>{s['dl_today']}</b>")
    if role == "owner":
        text, mk = header, _owner_menu()
    elif role == "admin":
        text, mk = "🤖 <b>এডমিন প্যানেল</b>\n\nআপলোড করতে ফাইল পাঠান বা নিচের মেনু ব্যবহার করুন।", _admin_menu()
    else:
        text = "👋 <b>স্বাগতম!</b>\n\nএখানে শেয়ার করা লিংক থেকে ফাইল ডাউনলোড করতে পারবেন।\nAdmin হতে চাইলে নিচের বাটনে রিকোয়েস্ট করুন।"
        mk = _user_menu(chat_id)
    if edit_msg_id:
        try:
            bot.edit_message_text(text, chat_id, edit_msg_id, reply_markup=mk)
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=mk)

# ══════════════════════════════════════════════════
#  /start কমান্ড
# ══════════════════════════════════════════════════
@bot.message_handler(commands=['start'])
def cmd_start(message):
    chat_id = str(message.chat.id)
    user = get_user(chat_id)
    parts = message.text.split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    if not is_admin(chat_id):  # Owner/Admin দের ফোর্স-সাব লাগবে না
        ok, not_joined = check_force_sub(chat_id)
        if not ok:
            send_force_sub_msg(chat_id, not_joined, payload or None)
            return

    if payload.startswith("ref_"):
        admin_id = payload[4:]
        if role_of(admin_id) in ("admin", "owner") and not user.get("linked_admin"):
            update_user(chat_id, {"linked_admin": admin_id})
        bot.send_message(chat_id, "👋 স্বাগতম!")
        _show_main_menu(chat_id)
        return

    if payload:
        _deliver_files(chat_id, payload, user)
        return

    _show_main_menu(chat_id)

@bot.message_handler(commands=['cancel'])
def cmd_cancel(message):
    chat_id = str(message.chat.id)
    update_step(chat_id, "none")
    bot.send_message(chat_id, "❌ বাতিল করা হয়েছে।")

@bot.message_handler(commands=['myid'])
def cmd_myid(message):
    bot.send_message(message.chat.id, f"🆔 আপনার Chat ID: <code>{message.chat.id}</code>")

# ══════════════════════════════════════════════════
#  কলব্যাক হ্যান্ডলার
# ══════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: True)
def cb(call):
    cid = str(call.message.chat.id)
    mid = call.message.message_id
    data = call.data
    user = get_user(cid)

    if data == "noop":
        bot.answer_callback_query(call.id); return

    if data.startswith("check_sub_"):
        fk = data[10:]
        joined, nj = check_force_sub(cid)
        if joined:
            bot.answer_callback_query(call.id, "✅ Join নিশ্চিত হয়েছে!", show_alert=True)
            try: bot.delete_message(cid, mid)
            except Exception: pass
            if fk and fk != "none":
                if fk.startswith("ref_"):
                    admin_id = fk[4:]
                    if role_of(admin_id) in ("admin", "owner") and not user.get("linked_admin"):
                        update_user(cid, {"linked_admin": admin_id})
                    _show_main_menu(cid)
                else:
                    _deliver_files(cid, fk, user)
            else:
                _show_main_menu(cid)
        else:
            bot.answer_callback_query(call.id, "❌ এখনো সব চ্যানেলে Join করেননি!", show_alert=True)
        return

    # ══ যেকোনো ইউজারের জন্য: Admin Access Request ══
    if data == "req_admin":
        if user.get("role") != "user":
            bot.answer_callback_query(call.id, "আপনি ইতিমধ্যে Admin/Owner!", show_alert=True); return
        existing = DB["admin_requests"].get(cid)
        if existing and existing.get("status") == "pending":
            bot.answer_callback_query(call.id, "⏳ আপনার রিকোয়েস্ট আগে থেকেই পেন্ডিং আছে!", show_alert=True); return
        with _db_lock:
            DB["admin_requests"][cid] = {"status": "pending", "requested_at": datetime.now().isoformat()}
            save_db()
        bot.answer_callback_query(call.id, "✅ রিকোয়েস্ট পাঠানো হয়েছে! Owner অনুমোদনের অপেক্ষায় থাকুন।", show_alert=True)
        m = _mk()
        m.row(_btn("✅ Accept", f"adm_accept_{cid}"), _btn("❌ Reject", f"adm_reject_{cid}"))
        try:
            bot.send_message(MAIN_ADMIN_ID, f"🔔 <b>নতুন Admin Request!</b>\n👤 Chat ID: <code>{cid}</code>\n📛 নাম: {call.from_user.first_name or ''}", reply_markup=m)
        except Exception as e:
            logger.warning(f"Notify owner failed: {e}")
        _show_main_menu(cid, mid)
        return

    # ══ নিচের সব ফিচার শুধু Admin/Owner দের জন্য (Owner-only অংশ আলাদা চেক করা হবে) ══
    if data.startswith("adm_accept_") or data.startswith("adm_reject_"):
        if not is_owner(cid):
            bot.answer_callback_query(call.id, "⛔ শুধু Owner এই কাজ করতে পারবে!", show_alert=True); return
        target = data.split("_", 2)[2]
        if data.startswith("adm_accept_"):
            update_user(target, {"role": "admin"})
            with _db_lock:
                DB["admin_requests"].pop(target, None)
                save_db()
            try:
                bot.send_message(target, "🎉 <b>অভিনন্দন! আপনি Admin হয়েছেন!</b>\n\nএখন ফাইল আপলোড, পোস্ট, শিডিউল সহ সব ফিচার আনলক।\n🔧 আর্নিং করতে ⚙️ সেটিংস থেকে আপনার শর্টেনার (TeraBox) API key যোগ করুন।")
                _show_main_menu(target)
            except Exception: pass
            bot.edit_message_text(f"✅ Accepted: <code>{target}</code>", cid, mid)
        else:
            with _db_lock:
                DB["admin_requests"].pop(target, None)
                save_db()
            try: bot.send_message(target, "❌ দুঃখিত, আপনার Admin Request গ্রহণ করা হয়নি।")
            except Exception: pass
            bot.edit_message_text(f"❌ Rejected: <code>{target}</code>", cid, mid)
        return

    if not is_admin(cid):
        bot.answer_callback_query(call.id, "⛔ এডমিন অ্যাক্সেস প্রয়োজন!", show_alert=True); return

    if data == "main_menu":
        update_step(cid, "none")
        _show_main_menu(cid, mid)

    elif data == "show_stats":
        s = get_stats(); m = _mk(); m.add(_back("main_menu"))
        bot.edit_message_text(
            f"📊 <b>বট স্ট্যাটিস্টিক্স</b>\n{'─'*26}\n👥 মোট ইউজার   : <b>{s['total_users']}</b>\n🟢 আজ সক্রিয়   : <b>{s['active_today']}</b>\n📁 মোট ফাইল    : <b>{s['total_files']}</b>\n📥 আজ ডাউনলোড : <b>{s['dl_today']}</b>\n📤 আজ আপলোড   : <b>{s['ul_today']}</b>\n👑 এডমিন       : <b>{s['total_admins']}</b>\n{'─'*26}\n📤 আপনার আপলোড: <b>{user.get('total_uploads',0)}</b>",
            cid, mid, reply_markup=m
        )

    elif data == "my_referral":
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{cid}"
        m = _mk(); m.add(_back("main_menu"))
        bot.edit_message_text(f"🔗 <b>আপনার রেফারেল লিংক</b>\n\nএই লিংক দিয়ে কেউ বটে ঢুকলে তারা আপনার সাথে লিংক হয়ে যাবে (broadcast targeting এর জন্য):\n\n<code>{link}</code>", cid, mid, reply_markup=m)

    # ══ আপলোড ══
    elif data == "start_upload":
        update_user(cid, {"step": "wait_single"})
        m = _mk(); m.add(_back("main_menu"))
        bot.edit_message_text("📤 <b>ফাইল পাঠান</b> (ছবি/ভিডিও/ডকুমেন্ট/অডিও)।", cid, mid, reply_markup=m)

    elif data == "start_batch":
        bid = str(uuid.uuid4().hex)[:10]
        update_user(cid, {"batch_id": bid, "step": "wait_batch"})
        m = _mk(); m.add(_btn("✅ আপলোড শেষ — Finish", "finish_batch")); m.add(_back("main_menu"))
        bot.edit_message_text("📦 <b>ব্যাচ আপলোড শুরু হয়েছে!</b>\n\nফাইলগুলো একে একে পাঠান।\nশেষ হলে Finish বাটনে ক্লিক করুন।", cid, mid, reply_markup=m)

    elif data == "finish_batch":
        bid = user.get("batch_id")
        cnt = _get_file_count_from_link(bid) if bid else 0
        if not bid or cnt == 0:
            bot.answer_callback_query(call.id, "⚠️ কোনো ফাইল যোগ হয়নি!", show_alert=True); return
        _ask_title(cid, mid, bid, cnt)

    elif data in ("skip_title", "use_header_title"):
        title = "" if data == "skip_title" else user.get("header", "")
        _finalize_post(cid, mid, title)

    elif data == "skip_thumbnail":
        bot.answer_callback_query(call.id)
        _handle_thumbnail_received(cid, "", user)

    elif data == "post_now":
        pass  # ব্যবহৃত হয় না, রাখা হলো ভবিষ্যতের জন্য

    elif data == "ask_schedule":
        m = _mk()
        m.add(_btn("🚫 বাতিল", "main_menu"))
        update_user(cid, {"step": "wait_schedule_time"})
        bot.edit_message_text("⏰ <b>সিডিউল সময় লিখুন</b>\nফরম্যাট: <code>YYYY-MM-DD HH:MM</code>\nউদাহরণ: <code>2026-08-20 18:30</code>", cid, mid, reply_markup=m)

    elif data == "menu_schedule":
        pending = [(sid, s) for sid, s in DB["scheduled"].items() if s.get("status") == "pending" and (is_owner(cid) or s.get("admin_id") == cid)]
        m = _mk()
        for sid, s in pending[:10]:
            m.add(_btn(f"⏰ {s.get('scheduled_at','')[:16]} — {s.get('title') or 'শিরোনামহীন'}", f"del_sched_{sid}"))
        m.add(_back("main_menu"))
        bot.edit_message_text(f"⏰ <b>সিডিউল পোস্ট ম্যানেজমেন্ট</b>\nমোট পেন্ডিং: <b>{len(pending)}</b>টি\n<i>মুছতে পোস্টে ক্লিক করুন।</i>", cid, mid, reply_markup=m)

    elif data.startswith("del_sched_"):
        sid = data[10:]
        with _db_lock:
            DB["scheduled"].pop(sid, None)
            save_db()
        bot.answer_callback_query(call.id, "🗑️ মুছে ফেলা হয়েছে!", show_alert=True)
        call.data = "menu_schedule"; cb(call)

    # ══ সেটিংস ══
    elif data == "menu_settings":
        text, mk = _settings_menu(user)
        bot.edit_message_text(text, cid, mid, reply_markup=mk)

    elif data == "set_header":
        update_step(cid, "wait_header")
        bot.edit_message_text("📝 নতুন হেডার টেক্সট লিখে পাঠান (বাতিল করতে /cancel):", cid, mid)

    elif data == "set_footer":
        update_step(cid, "wait_footer")
        bot.edit_message_text("📝 নতুন ফুটার টেক্সট লিখে পাঠান (বাতিল করতে /cancel):", cid, mid)

    elif data == "set_autodelete":
        update_step(cid, "wait_autodelete")
        bot.edit_message_text("⏱️ অটো-ডিলিট মিনিট লিখুন (0 = বন্ধ):", cid, mid)

    elif data == "set_linkrepeat":
        update_step(cid, "wait_linkrepeat")
        bot.edit_message_text("🔄 লিংক রিপিট সংখ্যা লিখুন (1-5):", cid, mid)

    elif data == "set_terabox":
        update_step(cid, "wait_terabox")
        bot.edit_message_text("🔑 আপনার শর্টেনার (TeraBox) API key পাঠান:", cid, mid)

    elif data == "tog_linkfilter":
        update_user(cid, {"link_filter": 0 if user.get("link_filter") else 1})
        text, mk = _settings_menu(get_user(cid))
        bot.edit_message_text(text, cid, mid, reply_markup=mk)

    elif data == "tog_textfilter":
        update_user(cid, {"text_filter": 0 if user.get("text_filter") else 1})
        text, mk = _settings_menu(get_user(cid))
        bot.edit_message_text(text, cid, mid, reply_markup=mk)

    elif data == "toggle_protect":
        if not is_owner(cid):
            bot.answer_callback_query(call.id, "⛔ শুধু Owner!", show_alert=True); return
        new_val = toggle_setting("protect_content")
        bot.answer_callback_query(call.id, f"🔐 Protect Content এখন {'ON' if new_val else 'OFF'}", show_alert=True)
        _show_main_menu(cid, mid)

    elif data == "cf_status":
        if not is_owner(cid):
            bot.answer_callback_query(call.id, "⛔ শুধু Owner!", show_alert=True); return
        m = _mk(); m.add(_back("main_menu"))
        if is_cf_enabled():
            test_res = _cf_d1_query("SELECT count(*) as count FROM bot_sections;")
            if test_res is not None:
                cnt = test_res[0].get("count", 0) if test_res else 0
                st_text = (
                    f"☁️ <b>Cloudflare D1 ডাটাবেস স্ট্যাটাস</b>\n{'─'*30}\n"
                    f"🟢 স্ট্যাটাস: <b>সফলভাবে সংযুক্ত (Connected)</b>\n"
                    f"🆔 Account ID: <code>{CF_ACCOUNT_ID[:6]}...{CF_ACCOUNT_ID[-4:] if len(CF_ACCOUNT_ID)>4 else ''}</code>\n"
                    f"📦 Database ID: <code>{CF_D1_DATABASE_ID[:6]}...{CF_D1_DATABASE_ID[-4:] if len(CF_D1_DATABASE_ID)>4 else ''}</code>\n"
                    f"📁 ক্লাউডে সিঙ্ক হওয়া সেকশন: <b>{cnt}</b>টি\n\n"
                    f"🎉 আপনার বটের সমস্ত ফাইল, ইউজার ও সেটিংস Render বন্ধ বা রিস্টার্ট হলেও <b>আজীবন সুরক্ষিত থাকবে</b>।"
                )
            else:
                st_text = (
                    "⚠️ <b>Cloudflare D1 কানেকশন ত্রুটি!</b>\n\n"
                    "টোকেন বা ডাটাবেস আইডি সঠিক কিনা নিশ্চিত করুন। Render Environment Variables চেক করুন।"
                )
        else:
            st_text = (
                "🔴 <b>Cloudflare D1 সংযুক্ত নেই!</b>\n\n"
                "বর্তমানে বটটি <b>লোকাল JSON ফাইল</b> ব্যবহার করছে। Render-এর ফ্রি সার্ভিসে রিস্টার্টের পর ডাটা মুছে যাওয়া রোধ করতে Cloudflare D1 যোগ করুন।\n\n"
                "📌 <b>প্রয়োজনীয় ভেরিয়েবল (.env / Render):</b>\n"
                "• <code>CF_ACCOUNT_ID</code>\n"
                "• <code>CF_D1_DATABASE_ID</code>\n"
                "• <code>CF_API_TOKEN</code>"
            )
        bot.edit_message_text(st_text, cid, mid, reply_markup=m)

    # ══ ফোর্স সাবস্ক্রাইব ══
    elif data == "menu_forcesub":
        m = _mk()
        for fs_id, ch in DB["force_sub"].items():
            m.row(_btn(f"📢 {ch['name']} {_ico(ch.get('status')=='on')}", f"fs_toggle_{fs_id}"), _btn("🗑️", f"fs_del_{fs_id}"))
        m.add(_btn("➕ নতুন চ্যানেল যোগ করুন", "fs_add"))
        m.add(_back("main_menu"))
        bot.edit_message_text("🔒 <b>ফোর্স সাবস্ক্রাইব চ্যানেল</b>", cid, mid, reply_markup=m)

    elif data == "fs_add":
        update_step(cid, "wait_fs_name")
        bot.edit_message_text("📢 চ্যানেলের নাম লিখুন:", cid, mid)

    elif data.startswith("fs_toggle_"):
        fs_id = data[10:]
        ch = DB["force_sub"].get(fs_id)
        if ch:
            ch["status"] = "off" if ch.get("status") == "on" else "on"
            save_db()
        call.data = "menu_forcesub"; cb(call)

    elif data.startswith("fs_del_"):
        fs_id = data[7:]
        with _db_lock:
            DB["force_sub"].pop(fs_id, None); save_db()
        call.data = "menu_forcesub"; cb(call)

    # ══ Ads চ্যানেল (Admin/Owner — বট থেকে যোগ/রিমুভ) ══
    elif data == "menu_adchannels":
        m = _mk()
        for a_id, ch in DB.get("ad_channels", {}).items():
            m.row(_btn(f"📢 {ch.get('name','(নামহীন)')}", "noop"), _btn("🗑️", f"adch_del_{a_id}"))
        m.add(_btn("➕ নতুন Ads চ্যানেল যোগ করুন", "adch_add"))
        m.add(_back("main_menu"))
        bot.edit_message_text(
            f"📢 <b>Ads চ্যানেল</b>\n\n🔒 ফিক্সড (env, শুধু Owner বদলাতে পারবে): <b>{len(AD_CHANNEL_IDS)}</b>টি\n➕ Admin-যোগকৃত (বট থেকে): <b>{len(DB.get('ad_channels', {}))}</b>টি",
            cid, mid, reply_markup=m
        )

    elif data == "adch_add":
        update_step(cid, "wait_adch_name")
        bot.edit_message_text("📢 নতুন Ads চ্যানেলের নাম লিখুন:", cid, mid)

    elif data.startswith("adch_del_"):
        a_id = data[9:]
        with _db_lock:
            DB.get("ad_channels", {}).pop(a_id, None); save_db()
        call.data = "menu_adchannels"; cb(call)

    # ══ কাস্টম বাটন (Admin/Owner — যোগ, ডিলিট, অন/অফ) ══
    elif data == "menu_buttons":
        m = _mk()
        master_on = bool(get_setting("custom_buttons_enabled", 1))
        m.add(_btn(f"মাস্টার সুইচ: {'🟢 চালু' if master_on else '🔴 বন্ধ'}", "btn_master_toggle"))

        buttons = DB.get("custom_buttons", {})
        for b_id, btn in buttons.items():
            st_ico = _ico(btn.get("status") == "on")
            m.row(
                _btn(f"{st_ico} {btn.get('text', '(নামহীন)')}", f"btn_toggle_{b_id}"),
                _btn("🗑️", f"btn_del_{b_id}")
            )
        m.add(_btn("➕ নতুন বাটন যোগ করুন", "btn_add"))
        m.add(_back("main_menu"))
        total = len(buttons)
        active = sum(1 for b in buttons.values() if b.get("status") == "on")
        bot.edit_message_text(
            f"🔘 <b>কাস্টম পোস্ট বাটন ম্যানেজমেন্ট</b>\n\n"
            f"প্রতিটি পোস্টের নিচে এই বাটনগুলো যুক্ত হবে (যেমন: ব্যাকআপ চ্যানেল, ওয়েবসাইট, টিউটোরিয়াল)।\n\n"
            f"📊 মোট বাটন: <b>{total}</b>টি | সক্রিয়: <b>{active}</b>টি\n"
            f"💡 অন/অফ করতে বাটনের নামের উপর ক্লিক করুন।",
            cid, mid, reply_markup=m
        )

    elif data == "btn_master_toggle":
        new_val = toggle_setting("custom_buttons_enabled")
        bot.answer_callback_query(call.id, f"কাস্টম বাটন এখন {'🟢 চালু' if new_val else '🔴 বন্ধ'}", show_alert=True)
        call.data = "menu_buttons"; cb(call)

    elif data.startswith("btn_toggle_"):
        b_id = data[11:]
        btn = DB.get("custom_buttons", {}).get(b_id)
        if btn:
            btn["status"] = "off" if btn.get("status") == "on" else "on"
            save_db()
            st = "🟢 চালু" if btn["status"] == "on" else "🔴 বন্ধ"
            bot.answer_callback_query(call.id, f"বাটন {st} করা হয়েছে!")
        call.data = "menu_buttons"; cb(call)

    elif data.startswith("btn_del_"):
        b_id = data[8:]
        with _db_lock:
            DB.get("custom_buttons", {}).pop(b_id, None); save_db()
        bot.answer_callback_query(call.id, "🗑️ বাটন মুছে ফেলা হয়েছে!", show_alert=True)
        call.data = "menu_buttons"; cb(call)

    elif data == "btn_add":
        update_step(cid, "wait_btn_text")
        m = _mk(); m.add(_back("menu_buttons"))
        bot.edit_message_text(
            "🔘 <b>নতুন বাটনের নাম / টেক্সট লিখুন:</b>\n\n"
            "উদাহরণ:\n"
            "• <code>📢 Join Backup Channel</code>\n"
            "• <code>🌐 আমাদের ওয়েবসাইট</code>\n"
            "• <code>🎬 Movie Request Group</code>\n\n"
            "বাতিল করতে /cancel লিখুন বা নিচের ব্যাক বাটন চাপুন।",
            cid, mid, reply_markup=m
        )

    # ══ Owner: Admin Requests ══
    elif data == "menu_requests":
        if not is_owner(cid): bot.answer_callback_query(call.id, "⛔ শুধু Owner!", show_alert=True); return
        pend = [(k, v) for k, v in DB["admin_requests"].items() if v.get("status") == "pending"]
        m = _mk()
        for uid, req in pend:
            m.row(_btn(f"👤 {uid}", "noop"))
            m.row(_btn("✅ Accept", f"adm_accept_{uid}"), _btn("❌ Reject", f"adm_reject_{uid}"))
        m.add(_back("main_menu"))
        bot.edit_message_text(f"👑 <b>Admin Requests</b>\nমোট পেন্ডিং: <b>{len(pend)}</b>", cid, mid, reply_markup=m)

    elif data == "menu_admins":
        if not is_owner(cid): bot.answer_callback_query(call.id, "⛔ শুধু Owner!", show_alert=True); return
        admins = all_admins()
        m = _mk()
        for a in admins:
            m.row(_btn(f"👤 {a['chat_id']}", "noop"), _btn("🗑️ Remove", f"adm_remove_{a['chat_id']}"))
        m.add(_back("main_menu"))
        bot.edit_message_text(f"👥 <b>Admin লিস্ট</b>\nমোট: <b>{len(admins)}</b> জন", cid, mid, reply_markup=m)

    elif data.startswith("adm_remove_"):
        if not is_owner(cid): bot.answer_callback_query(call.id, "⛔ শুধু Owner!", show_alert=True); return
        target = data[11:]
        update_user(target, {"role": "user"})
        try: bot.send_message(target, "⚠️ আপনার Admin এক্সেস প্রত্যাহার করা হয়েছে।")
        except Exception: pass
        call.data = "menu_admins"; cb(call)

    # ══ Owner: Broadcast ══
    elif data == "menu_broadcast":
        if not is_owner(cid): bot.answer_callback_query(call.id, "⛔ শুধু Owner!", show_alert=True); return
        m = _mk()
        m.row(_btn("🌐 সব ইউজারকে", "bc_all"), _btn("🎯 নির্দিষ্ট Admin(দের)", "bc_select"))
        m.add(_back("main_menu"))
        bot.edit_message_text("📢 <b>ব্রডকাস্ট</b>\nকাদের কাছে পাঠাবেন?", cid, mid, reply_markup=m)

    elif data == "bc_all":
        if not is_owner(cid): return
        update_user(cid, {"step": "wait_broadcast_all"})
        bot.edit_message_text("📢 ব্রডকাস্টের মেসেজ/ছবি/ভিডিও পাঠান (সবাইকে যাবে):", cid, mid)

    elif data == "bc_select":
        if not is_owner(cid): return
        update_user(cid, {"_bc_selected": []})
        _render_bc_select(cid, mid)

    elif data.startswith("bc_toggle_"):
        if not is_owner(cid): return
        aid = data[10:]
        sel = set(user.get("_bc_selected", []))
        if aid in sel: sel.discard(aid)
        else: sel.add(aid)
        update_user(cid, {"_bc_selected": list(sel)})
        _render_bc_select(cid, mid)

    elif data == "bc_select_confirm":
        if not is_owner(cid): return
        sel = user.get("_bc_selected", [])
        if not sel:
            bot.answer_callback_query(call.id, "⚠️ কমপক্ষে একজন Admin সিলেক্ট করুন!", show_alert=True); return
        update_user(cid, {"step": "wait_broadcast_select"})
        bot.edit_message_text(f"📢 <b>{len(sel)}</b> জন Admin-এর ইউজারদের কাছে যাবে।\nমেসেজ/ছবি/ভিডিও পাঠান:", cid, mid)

    elif data == "help_menu":
        m = _mk(); m.add(_back("main_menu"))
        bot.edit_message_text("ℹ️ <b>সাহায্য</b>\n\nফাইল পাঠালেই আপলোড শুরু হয়। মেনু থেকে বাকি ফিচার ব্যবহার করুন।", cid, mid, reply_markup=m)

    elif data == "confirm_post_now":
        d_link = f"https://t.me/{BOT_USERNAME}?start={user.get('pending_link','')}"
        posted = _publish_to_channels(
            cid, user, user.get("temp_media_type"), user.get("temp_media_id"), d_link, user.get("post_title", ""),
            manual_short_link=user.get("pending_short_link") or None, thumb_id=user.get("temp_thumb_id", "")
        )
        bot.edit_message_text(f"✅ <b>পোস্ট সম্পন্ন!</b>\n📤 <b>{posted}</b>টি চ্যানেলে পোস্ট হয়েছে।", cid, mid)
        update_user(cid, {"pending_link": "", "post_title": "", "temp_media_id": "", "temp_media_type": "", "temp_thumb_id": "", "pending_short_link": ""})

def _render_bc_select(cid, mid):
    user = get_user(cid)
    sel = set(user.get("_bc_selected", []))
    m = _mk()
    for a in all_admins():
        chk = "☑️" if a['chat_id'] in sel else "⬜"
        m.add(_btn(f"{chk} {a['chat_id']}", f"bc_toggle_{a['chat_id']}"))
    m.row(_btn("✅ কনফার্ম", "bc_select_confirm"), _back("menu_broadcast"))
    bot.edit_message_text(f"🎯 <b>Admin সিলেক্ট করুন</b>\nসিলেক্টেড: <b>{len(sel)}</b>", cid, mid, reply_markup=m)

def _handle_thumbnail_received(chat_id, thumb_id, user):
    """ভিডিও-স্পেশাল ফ্লো ধাপ ২: থাম্বনেইল সেভ, ফাইল রেকর্ড তৈরি, বট-লিংক এক-ক্লিক কপি সহ দেখানো, তারপর ডাউনলোড লিংক চাওয়া।"""
    video_file_id = user.get("temp_media_id", "")
    if not video_file_id:
        bot.send_message(chat_id, "⚠️ ভিডিও পাওয়া যায়নি, আবার আপলোড শুরু করুন।")
        update_user(chat_id, {"step": "none"})
        return

    file_key = str(uuid.uuid4().hex)[:10]
    uid = str(uuid.uuid4().hex)[:12]
    with _db_lock:
        DB["files"][uid] = {
            "uid": uid, "file_id": video_file_id, "type": "video", "uploader": chat_id,
            "batch_id": "", "file_key": file_key, "thumb_id": thumb_id,
            "uploaded_at": datetime.now().isoformat(),
        }
        save_db()

    d_link = f"https://t.me/{BOT_USERNAME}?start={file_key}"
    update_user(chat_id, {
        "pending_link": file_key, "temp_thumb_id": thumb_id, "step": "wait_video_dl_link",
    })

    m = _build_copy_button(d_link)
    header_txt = "✅ <b>থাম্বনেইল সেট হয়েছে!</b>" if thumb_id else "✅ <b>ভিডিও সংরক্ষিত হয়েছে!</b>"
    msg_txt = (
        f"{header_txt}\n\n"
        f"🔗 <b>ফাইল স্টোর / বট শেয়ার লিংক:</b>\n"
        f"<code>{d_link}</code>\n"
        f"<i>(কোড লিংকে বা নিচের বাটনে ট্যাপ করলেই কপি হয়ে যাবে)</i>\n\n"
        f"📥 <b>এখন আপনার শর্টেনার ওয়েবসাইট থেকে বানানো ডাউনলোড লিংকটি পাঠান।</b>\n"
        f"(উপরের বট লিংকটি শর্টেনার সাইটে দিয়ে যে earning লিংক পাবেন, সেটাই এখানে পাঠান — এটাই চ্যানেলের ডাউনলোড বাটনে যাবে)"
    )
    bot.send_message(chat_id, msg_txt, reply_markup=m)

def _handle_download_link_received(chat_id, link_text, user):
    """ভিডিও-স্পেশাল ফ্লো ধাপ ৩: শর্টেনার লিংক পাওয়া গেলে Ads চ্যানেলগুলোতে অটো-পোস্ট।"""
    if not re.match(r'^https?://', link_text.strip(), re.IGNORECASE):
        bot.send_message(chat_id, "⚠️ সঠিক লিংক দিন (http:// অথবা https:// দিয়ে শুরু হতে হবে)।")
        return

    file_key = user.get("pending_link", "")
    files = [f for f in DB["files"].values() if f.get("file_key") == file_key]
    if not files:
        bot.send_message(chat_id, "❌ ফাইল পাওয়া যায়নি, আবার আপলোড করুন।")
        update_user(chat_id, {"step": "none", "pending_link": "", "temp_media_id": "", "temp_media_type": "", "temp_thumb_id": ""})
        return

    ad_ids = all_ad_channel_ids()
    if not ad_ids:
        bot.send_message(chat_id, "⚠️ কোনো Ads চ্যানেল যোগ করা নেই। আগে 📢 Ads চ্যানেল মেনু থেকে একটি যোগ করুন, তারপর আবার লিংকটি পাঠান।")
        return

    f = files[0]
    dl_link = link_text.strip()
    ph = apply_filters(user.get("header", ""), chat_id)
    pf = apply_filters(user.get("footer", ""), chat_id)
    ph_t = f"{ph}\n\n" if ph else ""
    pf_t = f"\n\n{pf}" if pf else ""
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    caption = f"{ph_t}⬇️ ডাউনলোড করতে নিচের বাটনে ক্লিক করুন\n\n<i>🕐 {now_str}</i>{pf_t}".strip()
    markup = _build_post_markup(user, dl_link, clean_html(caption))
    protect = bool(get_setting("protect_content", 0))
    thumb_id = f.get('thumb_id') or user.get('temp_thumb_id', '')
    posted = 0
    for ch_id in ad_ids:
        try:
            _send_media(ch_id, f.get('type', 'video'), f['file_id'], caption, markup, protect, thumb_id=thumb_id)
            posted += 1
        except Exception as e:
            logger.warning(f"Ad channel post error [{ch_id}]: {e}")

    _inc_stat("uploads")
    update_user(chat_id, {
        "total_uploads": user.get("total_uploads", 0) + 1,
        "step": "none", "pending_link": "", "temp_media_id": "", "temp_media_type": "", "temp_thumb_id": "",
    })
    bot.send_message(chat_id, f"✅ <b>পোস্ট সম্পন্ন!</b>\n📤 <b>{posted}</b>টি Ads চ্যানেলে পোস্ট হয়েছে।\n🔗 ডাউনলোড লিংক: {dl_link}")

def _handle_generic_dl_link_received(chat_id, link_text, user):
    """জেনেরিক ফ্লো (photo/document/audio/ব্যাচ): টাইটেলের পর ম্যানুয়াল শর্টেনার লিংক নিয়ে পোস্ট-নাউ/সিডিউল অপশন দেখানো।"""
    if not re.match(r'^https?://', link_text.strip(), re.IGNORECASE):
        bot.send_message(chat_id, "⚠️ সঠিক লিংক দিন (http:// অথবা https:// দিয়ে শুরু হতে হবে)।")
        return

    bid = user.get("pending_link", "")
    files = [f for f in DB["files"].values() if f.get("batch_id") == bid or f.get("file_key") == bid]
    if not files:
        bot.send_message(chat_id, "❌ ফাইল পাওয়া যায়নি, আবার আপলোড করুন।")
        update_user(chat_id, {"step": "none", "pending_link": "", "post_title": "", "temp_media_id": "", "temp_media_type": "", "temp_thumb_id": ""})
        return

    ad_ids = all_ad_channel_ids()
    if not ad_ids:
        bot.send_message(chat_id, "⚠️ কোনো Ads চ্যানেল যোগ করা নেই। আগে 📢 Ads চ্যানেল মেনু থেকে একটি যোগ করুন, তারপর আবার লিংকটি পাঠান।")
        return

    dl_link = link_text.strip()
    d_link = f"https://t.me/{BOT_USERNAME}?start={bid}"
    m = _mk()
    m.row(_btn("🚀 এখনই পোস্ট করুন", "confirm_post_now"), _btn("⏰ সিডিউল করুন", "ask_schedule"))
    update_user(chat_id, {"pending_short_link": dl_link, "step": "none"})
    bot.send_message(
        chat_id,
        f"💎 ডাউনলোড লিংক সেভ হয়েছে:\n<code>{dl_link}</code>\n\n🔗 বট শেয়ার লিংক: <code>{d_link}</code>\n\nএখন পোস্ট করবেন নাকি সিডিউল করবেন?",
        reply_markup=m
    )

def _ask_title(chat_id, mid, batch_id, count):
    update_user(chat_id, {"pending_link": batch_id})
    d_link = f"https://t.me/{BOT_USERNAME}?start={batch_id}"
    m = _mk()
    m.row(_btn("⏭️ Skip (কোনো শিরোনাম না)", "skip_title"), _btn("📝 Header ব্যবহার করুন", "use_header_title"))
    text = (
        f"✅ <b>{count}টি ফাইল সেভ হয়েছে!</b>\n\n"
        f"🔗 <b>ফাইল স্টোর লিংক:</b>\n<code>{d_link}</code>\n\n"
        f"এই পোস্টের জন্য শিরোনাম লিখে পাঠান, অথবা নিচের বাটন ব্যবহার করুন:"
    )
    if mid:
        try:
            bot.edit_message_text(text, chat_id, mid, reply_markup=m)
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=m)
    update_step(chat_id, "wait_post_title")

def _finalize_post(chat_id, mid, title):
    user = get_user(chat_id)
    bid = user.get("pending_link", "")
    files = [f for f in DB["files"].values() if f.get("batch_id") == bid or f.get("file_key") == bid]
    if not files:
        bot.send_message(chat_id, "❌ ফাইল পাওয়া যায়নি, আবার আপলোড করুন।")
        update_user(chat_id, {"step": "none"})
        return

    if not all_ad_channel_ids():
        bot.send_message(chat_id, "⚠️ কোনো Ads চ্যানেল যোগ করা নেই। আগে 📢 Ads চ্যানেল মেনু থেকে একটি যোগ করুন, তারপর আবার আপলোড করুন।")
        update_user(chat_id, {"step": "none"})
        return

    mtype, mid_ = files[0]['type'], files[0]['file_id']
    thumb_id = files[0].get('thumb_id', '')

    update_user(chat_id, {"post_title": title, "temp_media_id": mid_, "temp_media_type": mtype, "temp_thumb_id": thumb_id, "step": "wait_post_dl_link"})

    d_link = f"https://t.me/{BOT_USERNAME}?start={bid}"
    m = _build_copy_button(d_link)

    title_info = f"\n📝 <b>শিরোনাম:</b> {clean_html(title)}" if title else ""
    text_out = (
        f"✅ <b>টাইটেল সেভ হয়েছে!</b>{title_info}\n\n"
        f"🔗 <b>ফাইল স্টোর / বট শেয়ার লিংক:</b>\n"
        f"<code>{d_link}</code>\n"
        f"<i>(কোড লিংকে বা নিচের বাটনে ট্যাপ করলেই কপি হয়ে যাবে)</i>\n\n"
        f"📥 <b>এখন আপনার শর্টেনার ওয়েবসাইট থেকে বানানো ডাউনলোড লিংকটি পাঠান।</b>\n"
        f"(উপরের বট লিংকটি শর্টেনার সাইটে দিয়ে যে earning লিংক পাবেন, সেটাই এখানে পাঠান — এটাই চ্যানেলের ডাউনলোড বাটনে যাবে)"
    )
    if mid:
        try:
            bot.edit_message_text(text_out, chat_id, mid, reply_markup=m)
            return
        except Exception:
            pass
    bot.send_message(chat_id, text_out, reply_markup=m)

# ══════════════════════════════════════════════════
#  মেসেজ হ্যান্ডলার (টেক্সট + ফাইল)
# ══════════════════════════════════════════════════
@bot.message_handler(content_types=['text', 'photo', 'document', 'video', 'audio'])
def handle_message(message):
    chat_id = str(message.chat.id)
    user = get_user(chat_id)
    step = user.get("step", "none")
    text = (message.text or "").strip() if message.content_type == 'text' else ""

    # ══ Broadcast ইনপুট (Owner only) ══
    if step in ("wait_broadcast_all", "wait_broadcast_select") and is_owner(chat_id):
        if step == "wait_broadcast_all":
            targets = list(DB["users"].keys())
        else:
            sel = set(user.get("_bc_selected", []))
            targets = [uid for uid, u in DB["users"].items() if u.get("linked_admin") in sel or uid in sel]
        update_user(chat_id, {"step": "none", "_bc_selected": []})
        threading.Thread(target=_broadcast_worker, args=(chat_id, message.chat.id, message.message_id, targets), daemon=True).start()
        bot.send_message(chat_id, f"📡 ব্রডকাস্ট শুরু হয়েছে! মোট টার্গেট: <b>{len(targets)}</b> জন")
        return

    # ══ সেটিংস টেক্সট ইনপুট ══
    if step == "wait_header" and text:
        update_user(chat_id, {"header": text, "step": "none"})
        bot.send_message(chat_id, "✅ হেডার সেভ হয়েছে।"); return
    if step == "wait_footer" and text:
        update_user(chat_id, {"footer": text, "step": "none"})
        bot.send_message(chat_id, "✅ ফুটার সেভ হয়েছে।"); return
    if step == "wait_autodelete" and text:
        try:
            val = max(0, int(text))
            update_user(chat_id, {"auto_delete": val, "step": "none"})
            bot.send_message(chat_id, f"✅ অটো-ডিলিট সেট হয়েছে: {val} মিনিট।")
        except ValueError:
            bot.send_message(chat_id, "⚠️ শুধু সংখ্যা দিন।")
        return
    if step == "wait_linkrepeat" and text:
        try:
            val = max(1, min(5, int(text)))
            update_user(chat_id, {"link_repeat_count": val, "step": "none"})
            bot.send_message(chat_id, f"✅ লিংক রিপিট সেট হয়েছে: {val}x")
        except ValueError:
            bot.send_message(chat_id, "⚠️ শুধু সংখ্যা দিন (1-5)।")
        return
    if step == "wait_terabox" and text:
        update_user(chat_id, {"terabox_key": text, "step": "none"})
        bot.send_message(chat_id, "✅ শর্টেনার API key সেভ হয়েছে। এখন থেকে আপনার পোস্টের Download লিংক আর্নিং-এনাবলড হবে।"); return

    # ══ ফোর্স সাব যোগ (multi-step) ══
    if step == "wait_fs_name" and text:
        update_user(chat_id, {"_fs_name": text, "step": "wait_fs_channelid"})
        bot.send_message(chat_id, "📢 চ্যানেলের Channel ID দিন (যেমন: -100xxxxxxxxxx):"); return
    if step == "wait_fs_channelid" and text:
        update_user(chat_id, {"_fs_channelid": text, "step": "wait_fs_url"})
        bot.send_message(chat_id, "🔗 চ্যানেলের জয়েন লিংক (URL) দিন:"); return
    if step == "wait_fs_url" and text:
        fs_id = str(uuid.uuid4().hex)[:8]
        with _db_lock:
            DB["force_sub"][fs_id] = {"fs_id": fs_id, "name": user.get("_fs_name", ""), "channel_id": user.get("_fs_channelid", ""), "url": text, "status": "on"}
            save_db()
        update_user(chat_id, {"step": "none", "_fs_name": "", "_fs_channelid": ""})
        bot.send_message(chat_id, "✅ ফোর্স-সাব চ্যানেল যোগ হয়েছে।"); return

    # ══ Ads চ্যানেল যোগ (Admin/Owner, বট থেকে) ══
    if step == "wait_adch_name" and text:
        update_user(chat_id, {"_adch_name": text, "step": "wait_adch_channelid"})
        bot.send_message(chat_id, "📢 এবার চ্যানেলের Channel ID দিন (যেমন: -100xxxxxxxxxx)।\n⚠️ বটকে অবশ্যই ওই চ্যানেলে Admin হিসেবে যোগ করা থাকতে হবে, নাহলে পোস্ট যাবে না।"); return
    if step == "wait_adch_channelid" and text:
        a_id = str(uuid.uuid4().hex)[:8]
        with _db_lock:
            DB.setdefault("ad_channels", {})[a_id] = {"id": a_id, "name": user.get("_adch_name", ""), "channel_id": text, "added_by": chat_id}
            save_db()
        update_user(chat_id, {"step": "none", "_adch_name": ""})
        bot.send_message(chat_id, "✅ নতুন Ads চ্যানেল যোগ হয়েছে। এখন থেকে এখানেও পোস্ট হবে।"); return

    # ══ কাস্টম বাটন যোগ (Admin/Owner) ══
    if step == "wait_btn_text" and text and is_admin(chat_id):
        update_user(chat_id, {"_btn_text": text, "step": "wait_btn_url"})
        m = _mk(); m.add(_back("menu_buttons"))
        bot.send_message(
            chat_id,
            f"🔗 এবার <b>'{clean_html(text)}'</b> বাটনের লিংক (URL) পাঠান:\n"
            f"(যেমন: <code>https://t.me/yourchannel</code> বা <code>https://yoursite.com</code>)",
            reply_markup=m
        )
        return

    if step == "wait_btn_url" and text and is_admin(chat_id):
        btn_url = text.strip()
        if not re.match(r'^(https?://|t\.me/)', btn_url, re.IGNORECASE):
            bot.send_message(chat_id, "⚠️ সঠিক URL দিন (যেমন: https://t.me/... বা https://...)")
            return
        if btn_url.startswith("t.me/"):
            btn_url = f"https://{btn_url}"

        b_id = str(uuid.uuid4().hex)[:8]
        btn_text = user.get("_btn_text", "Button")
        with _db_lock:
            DB.setdefault("custom_buttons", {})[b_id] = {
                "id": b_id,
                "text": btn_text,
                "url": btn_url,
                "status": "on",
                "added_by": chat_id,
                "created_at": datetime.now().isoformat()
            }
            save_db()

        update_user(chat_id, {"step": "none", "_btn_text": ""})
        m = _mk()
        m.add(_btn("🔘 বাটন লিস্ট দেখুন", "menu_buttons"), _back("main_menu"))
        bot.send_message(
            chat_id,
            f"✅ <b>নতুন কাস্টম বাটন যোগ হয়েছে!</b>\n\n"
            f"🔘 নাম: <b>{clean_html(btn_text)}</b>\n"
            f"🔗 লিংক: <code>{btn_url}</code>\n"
            f"🟢 স্ট্যাটাস: <b>চালু (ON)</b>\n\n"
            f"এখন থেকে প্রতিটি পোস্টের সাথে এই বাটনটি যাবে।",
            reply_markup=m
        )
        return

    # ══ সিডিউল সময় ══
    if step == "wait_schedule_time" and text:
        try:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
        except ValueError:
            bot.send_message(chat_id, "⚠️ ফরম্যাট ভুল! উদাহরণ: 2026-08-20 18:30"); return
        bid = user.get("pending_link", "")
        sched_id = str(uuid.uuid4().hex)[:10]
        with _db_lock:
            DB["scheduled"][sched_id] = {
                "sched_id": sched_id, "admin_id": chat_id,
                "media_type": user.get("temp_media_type"), "media_id": user.get("temp_media_id"),
                "d_link": f"https://t.me/{BOT_USERNAME}?start={bid}", "title": user.get("post_title", ""),
                "manual_short_link": user.get("pending_short_link", ""), "thumb_id": user.get("temp_thumb_id", ""),
                "scheduled_at": dt.isoformat(), "status": "pending", "created_at": datetime.now().isoformat(),
            }
            save_db()
        update_user(chat_id, {"step": "none", "pending_link": "", "post_title": "", "temp_media_id": "", "temp_media_type": "", "temp_thumb_id": "", "pending_short_link": ""})
        bot.send_message(chat_id, f"⏰ <b>সিডিউল সেভ হয়েছে!</b>\n📅 সময়: <b>{text}</b>\n🆔 ID: <code>{sched_id}</code>"); return

    # ══ পোস্ট টাইটেল ══
    if step == "wait_post_title" and text:
        _finalize_post(chat_id, None, text); return

    # ══ ভিডিও-স্পেশাল ফ্লো ধাপ ২: থাম্বনেইল ছবি এসেছে ══
    if step == "wait_thumbnail" and is_admin(chat_id):
        if message.content_type == 'photo':
            _handle_thumbnail_received(chat_id, message.photo[-1].file_id, user)
            return
        elif message.content_type == 'document':
            _handle_thumbnail_received(chat_id, message.document.file_id, user)
            return
        elif text:
            bot.send_message(chat_id, "⚠️ অনুগ্রহ করে একটি ছবি (Photo) থাম্বনেইল হিসেবে পাঠান।")
            return

    # ══ ভিডিও-স্পেশাল ফ্লো ধাপ ৩: ডাউনলোড লিংক (শর্টেনার সাইট থেকে) এসেছে ══
    if step == "wait_video_dl_link" and text and is_admin(chat_id):
        _handle_download_link_received(chat_id, text, user)
        return

    # ══ জেনেরিক ফ্লো: টাইটেলের পর ডাউনলোড লিংক এসেছে (photo/document/audio/ব্যাচ) ══
    if step == "wait_post_dl_link" and text and is_admin(chat_id):
        _handle_generic_dl_link_received(chat_id, text, user)
        return

    # ══ ফাইল আপলোড (Admin/Owner only) ══
    if message.content_type in ('photo', 'document', 'video', 'audio'):
        if not is_admin(chat_id):
            bot.send_message(chat_id, "⛔ ফাইল আপলোড করার জন্য Admin অ্যাক্সেস প্রয়োজন।"); return

        mtype = message.content_type
        if mtype == 'photo':
            file_id = message.photo[-1].file_id
        else:
            file_id = getattr(message, mtype).file_id

        if step == "wait_batch":
            bid = user.get("batch_id")
            uid = str(uuid.uuid4().hex)[:12]
            with _db_lock:
                DB["files"][uid] = {"uid": uid, "file_id": file_id, "type": mtype, "uploader": chat_id, "batch_id": bid, "file_key": "", "uploaded_at": datetime.now().isoformat()}
                save_db()
            bot.send_message(chat_id, f"✅ ব্যাচে যোগ হয়েছে। (মোট: {_get_file_count_from_link(bid)})")
            return

        # ভিডিও একক আপলোড হলে — আগে থাম্বনেইল ও ম্যানুয়াল ডাউনলোড লিংক চাওয়া হবে
        if mtype == 'video' and step in ("wait_single", "none"):
            update_user(chat_id, {"temp_media_id": file_id, "temp_media_type": mtype, "step": "wait_thumbnail"})
            m = _mk()
            m.add(_btn("⏭️ Skip (থাম্বনেইল ছাড়া)", "skip_thumbnail"))
            bot.send_message(chat_id, "🖼️ এখন এই ভিডিওর জন্য একটি ছবি থাম্বনেইল হিসেবে পাঠান (অথবা নিচের বাটনে Skip করুন):", reply_markup=m)
            return

        if step in ("wait_single", "none"):
            file_key = str(uuid.uuid4().hex)[:10]
            uid = str(uuid.uuid4().hex)[:12]
            with _db_lock:
                DB["files"][uid] = {"uid": uid, "file_id": file_id, "type": mtype, "uploader": chat_id, "batch_id": "", "file_key": file_key, "uploaded_at": datetime.now().isoformat()}
                save_db()
            _ask_title(chat_id, None, file_key, 1)
            return

    # ══ কিছুই ম্যাচ না করলে ══
    if message.content_type == 'text' and text and not text.startswith('/'):
        if is_admin(chat_id):
            bot.send_message(chat_id, "ℹ️ ফাইল পাঠান আপলোড করতে, অথবা /start লিখে মেনুতে যান।")
        # সাধারণ user এর টেক্সট মেসেজে চুপ থাকা হলো

# ══════════════════════════════════════════════════
#  ব্রডকাস্ট ওয়ার্কার
# ══════════════════════════════════════════════════
def _broadcast_worker(admin_id, from_chat, msg_id, targets):
    total = len(targets); ok = fail = 0
    for i, uid in enumerate(targets):
        try:
            bot.copy_message(uid, from_chat, msg_id); ok += 1
        except Exception:
            fail += 1
        time.sleep(0.05)
        if (i + 1) % 100 == 0:
            try: bot.send_message(admin_id, f"📊 {i+1}/{total} | ✅{ok} ❌{fail}")
            except Exception: pass
    try:
        bot.send_message(admin_id, f"✅ <b>ব্রডকাস্ট সম্পন্ন!</b>\n📨 মোট: <b>{total}</b>\n✅ <b>{ok}</b> | ❌ <b>{fail}</b>")
    except Exception:
        pass

# ══════════════════════════════════════════════════
#  বট রান
# ══════════════════════════════════════════════════
def run_bot():
    if not BOT_TOKEN or BOT_TOKEN == "DUMMY_TOKEN":
        logger.error("❌ BOT_TOKEN সেট করা নেই! Polling শুরু করা যায়নি।")
        return
    if not all_ad_channel_ids() and not PREMIUM_CHANNEL_IDS:
        logger.warning("⚠️ AD_CHANNEL_IDS / PREMIUM_CHANNEL_IDS সেট করা নেই — পোস্ট কোথাও যাবে না।")

    # স্টার্টের আগে যেকোনো webhook/পুরনো getUpdates session ক্লিয়ার করে দেওয়া হলো —
    # এটা "409 Conflict: terminated by other getUpdates request" এরর অনেকটা কমাতে সাহায্য করে,
    # বিশেষ করে Render রিডিপ্লয়ের সময় সাময়িক ওভারল্যাপে।
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception as e:
        logger.warning(f"remove_webhook ব্যর্থ (ignore করা হলো): {e}")

    logger.info(f"🚀 Bot Polling started (v{BOT_VERSION})...")
    while True:
        try:
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60, skip_pending=True)
        except telebot.apihelper.ApiTelegramException as e:
            if getattr(e, "error_code", None) == 409:
                logger.error("⚠️ 409 Conflict: একই BOT_TOKEN দিয়ে অন্য কোথাও (আরেকটা ইন্সট্যান্স/পুরনো ডিপ্লয়) polling চলছে। "
                             "নিশ্চিত করুন Render-এ শুধু একটাই ইন্সট্যান্স/ডিপ্লয় চলছে এবং লোকালি বা অন্য কোথাও একই টোকেন চালু নেই।")
            else:
                logger.error(f"Polling error: {e}")
            time.sleep(8)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)

# ══════════════════════════════════════════════════
#  UptimeRobot ও Web Service এর জন্য Keep-Alive সার্ভার + Self Ping
# ══════════════════════════════════════════════════
class _PingHandler(BaseHTTPRequestHandler):
    def _send_ok(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(b"OK - Bot is running")))
        self.end_headers()

    def do_GET(self):
        self._send_ok()
        self.wfile.write(b"OK - Bot is running")

    def do_HEAD(self):
        self._send_ok()

    def log_message(self, *args):
        pass  # সার্ভার কনসোল লগ পরিষ্কার রাখতে চেপে রাখা হলো

def _get_port():
    raw = (os.environ.get("PORT") or "").strip()
    if raw and raw.isdigit():
        return int(raw)
    return 8080

def _run_keepalive_server():
    port = _get_port()
    try:
        server = HTTPServer(("0.0.0.0", port), _PingHandler)
        logger.info(f"🌐 Keep-alive server running on port {port}")
        server.serve_forever()
    except Exception as e:
        logger.warning(f"Keep-alive server error on port {port}: {e}")

def _self_ping_worker():
    """১০ মিনিট পর পর নিজেকে নিজে HTTP রিকোয়েস্ট করে স্লিপ মোডে যাওয়া রোধ করে।"""
    port = _get_port()
    time.sleep(20)  # সার্ভার বুট হওয়ার জন্য অপেক্ষা
    while True:
        try:
            # Render নিজে থেকেই RENDER_EXTERNAL_URL সেট করে, অথবা ইউজার APP_URL সেট করতে পারে
            target_url = (os.environ.get("APP_URL") or os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEB_URL") or "").strip()
            if not target_url:
                target_url = f"http://127.0.0.1:{port}"
            if not target_url.startswith("http://") and not target_url.startswith("https://"):
                target_url = f"https://{target_url}"

            res = requests.get(target_url, timeout=15)
            logger.info(f"🔄 Self-ping sent to {target_url} (Status: {res.status_code})")
        except Exception as e:
            logger.warning(f"Self-ping error: {e}")

        # ১০ মিনিট (৬০০ সেকেন্ড) পর পর রিকোয়েস্ট যাবে
        time.sleep(600)

if __name__ == "__main__":
    threading.Thread(target=_run_keepalive_server, daemon=True).start()
    threading.Thread(target=_self_ping_worker, daemon=True).start()
    threading.Thread(target=_cf_sync_worker, daemon=True).start()
    run_bot()
