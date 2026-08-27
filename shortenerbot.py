"""
╔══════════════════════════════════════════════════════════════╗
║        🚀 MULTI-ADMIN FILE SHARE BOT v7.0                    ║
║   Owner → Admin (request/approve) → User                     ║
║   JSON Storage · No Firebase/Mongo/Web-Panel                 ║
║   Fixed Channels (ENV) · Per-Admin Shortener Earning          ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, re, time, json, uuid, threading, requests, telebot, logging
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from urllib.parse import quote
from http.server import BaseHTTPRequestHandler, HTTPServer

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
#  JSON ডাটাবেস (MongoDB এর বদলে)
# ══════════════════════════════════════════════════
_db_lock = threading.RLock()

def _empty_db():
    return {
        "users": {},            # chat_id -> user dict
        "files": {},             # uid -> file dict
        "queue": [],             # auto-delete queue
        "scheduled": {},         # sched_id -> scheduled post
        "admin_requests": {},    # chat_id -> request dict
        "force_sub": {},         # fs_id -> channel dict
        "ad_channels": {},       # ad_id -> {id,name,channel_id,added_by} — Admin-দের যোগ করা Ads চ্যানেল
        "settings": {},          # key -> value
        "stats": {},             # date -> counters
    }

def _load_db():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in _empty_db().items():
                data.setdefault(k, v)
            return data
        except Exception as e:
            logger.error(f"DB load error, starting fresh: {e}")
    return _empty_db()

DB = _load_db()

def save_db():
    with _db_lock:
        os.makedirs(os.path.dirname(DATA_FILE) or ".", exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(DB, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)

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
                                          item.get('d_link', ''), item.get('title', ''))
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
    return mk

def _send_media(ch_id, mtype, mid, caption, markup, protect=False, thumb_id=""):
    kw = {"caption": caption, "reply_markup": markup, "protect_content": protect}
    if mtype == 'photo':    bot.send_photo(ch_id, mid, **kw)
    elif mtype == 'video':  _send_video_with_thumb(ch_id, mid, thumb_id, **kw)
    elif mtype == 'document': bot.send_document(ch_id, mid, **kw)
    elif mtype == 'audio':  bot.send_audio(ch_id, mid, **kw)

def _send_video_with_thumb(ch_id, file_id, thumb_id, **kw):
    """ভিডিও পাঠানোর সময় থাম্বনেইল যোগ করে — telebot ভার্সনভেদে param নাম আলাদা হতে পারে বলে fallback রাখা হলো।"""
    if thumb_id:
        try:
            return bot.send_video(ch_id, file_id, thumbnail=thumb_id, **kw)
        except TypeError:
            try:
                return bot.send_video(ch_id, file_id, thumb=thumb_id, **kw)
            except Exception:
                pass
    return bot.send_video(ch_id, file_id, **kw)

def _publish_to_channels(admin_id, user, mtype, mid, d_link, title):
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
            _send_media(ch_id, mtype, mid, ad_caption, ad_markup, protect)
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
                _send_media(ch_id, mtype, mid, pr_caption, pr_markup, protect)
                posted += 1
            except Exception as e:
                logger.warning(f"Premium channel post error [{ch_id}]: {e}")

    # Log চ্যানেল — শুধু ব্যাকআপ
    if LOG_CHANNEL_ID:
        try:
            log_cap = f"💾 <b>Backup</b> | 📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n👤 Uploader: <code>{admin_id}</code>"
            _send_media(LOG_CHANNEL_ID, mtype, mid, log_cap, None, False)
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
    m.row(_btn("📢 Ads চ্যানেল", "menu_adchannels"))
    m.add(_btn("🔗 আমার রেফারেল লিংক", "my_referral"))
    return m

def _owner_menu():
    m = _admin_menu()
    m.row(_btn("👑 Admin Requests", "menu_requests"), _btn("👥 Admin লিস্ট", "menu_admins"))
    m.row(_btn("📢 ব্রডকাস্ট", "menu_broadcast"), _btn("🔐 Protect Content", "toggle_protect"))
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
        posted = _publish_to_channels(cid, user, user.get("temp_media_type"), user.get("temp_media_id"), d_link, user.get("post_title", ""))
        bot.edit_message_text(f"✅ <b>পোস্ট সম্পন্ন!</b>\n📤 <b>{posted}</b>টি চ্যানেলে পোস্ট হয়েছে।", cid, mid)
        update_user(cid, {"pending_link": "", "post_title": "", "temp_media_id": "", "temp_media_type": ""})

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
        "pending_link": file_key, "temp_thumb_id": thumb_id, "step": "wait_dl_link",
    })

    m = _mk()
    try:
        m.add(InlineKeyboardButton("📋 এক ক্লিকে কপি করুন", copy_text=telebot.types.CopyTextButton(text=d_link)))
    except Exception:
        pass  # পুরনো টেলিগ্রাম ক্লায়েন্ট/লাইব্রেরিতে সাপোর্ট না থাকলে বাটন ছাড়াই এগোবে
    try:
        bot.send_message(chat_id, f"✅ থাম্বনেইল সেট হয়েছে!\n\n🔗 <b>বট শেয়ার লিংক:</b>\n<code>{d_link}</code>\n(কোড টেক্সটে ট্যাপ করলেও কপি হয়ে যাবে)", reply_markup=m)
    except Exception:
        bot.send_message(chat_id, f"✅ থাম্বনেইল সেট হয়েছে!\n\n🔗 <b>বট শেয়ার লিংক:</b>\n<code>{d_link}</code>")

    bot.send_message(
        chat_id,
        "📥 এখন আপনার শর্টেনার ওয়েবসাইট থেকে বানানো ডাউনলোড লিংকটি পাঠান।\n"
        "(উপরের বট লিংকটি শর্টেনার সাইটে দিয়ে যে লিংক পাবেন, সেটাই এখানে পাঠান — এটাই Ads চ্যানেলে পোস্ট হবে)"
    )

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

    posted = 0
    for ch_id in ad_ids:
        try:
            _send_video_with_thumb(ch_id, f['file_id'], f.get('thumb_id', ''), caption=caption, reply_markup=markup, protect_content=protect)
            posted += 1
        except Exception as e:
            logger.warning(f"Ad channel video post error [{ch_id}]: {e}")

    _inc_stat("uploads")
    update_user(chat_id, {
        "total_uploads": user.get("total_uploads", 0) + 1,
        "step": "none", "pending_link": "", "temp_media_id": "", "temp_media_type": "", "temp_thumb_id": "",
    })
    bot.send_message(chat_id, f"✅ <b>পোস্ট সম্পন্ন!</b>\n📤 <b>{posted}</b>টি Ads চ্যানেলে পোস্ট হয়েছে।\n🔗 ডাউনলোড লিংক: {dl_link}")

def _ask_title(chat_id, mid, batch_id, count):
    update_user(chat_id, {"pending_link": batch_id})
    m = _mk()
    m.row(_btn("⏭️ Skip (কোনো শিরোনাম না)", "skip_title"), _btn("📝 Header ব্যবহার করুন", "use_header_title"))
    try:
        bot.edit_message_text(f"✅ <b>{count}টি ফাইল সেভ হয়েছে!</b>\n\nএই পোস্টের জন্য শিরোনাম লিখে পাঠান, অথবা নিচের বাটন ব্যবহার করুন:", chat_id, mid, reply_markup=m)
    except Exception:
        bot.send_message(chat_id, f"✅ <b>{count}টি ফাইল সেভ হয়েছে!</b>\n\nএই পোস্টের জন্য শিরোনাম লিখে পাঠান, অথবা নিচের বাটন ব্যবহার করুন:", reply_markup=m)
    update_step(chat_id, "wait_post_title")

def _finalize_post(chat_id, mid, title):
    user = get_user(chat_id)
    bid = user.get("pending_link", "")
    files = [f for f in DB["files"].values() if f.get("batch_id") == bid or f.get("file_key") == bid]
    if not files:
        bot.send_message(chat_id, "❌ ফাইল পাওয়া যায়নি, আবার আপলোড করুন।")
        update_user(chat_id, {"step": "none"})
        return

    mtype, mid_ = files[0]['type'], files[0]['file_id']
    d_link = f"https://t.me/{BOT_USERNAME}?start={bid}"

    m = _mk()
    m.row(_btn("🚀 এখনই পোস্ট করুন", "confirm_post_now"), _btn("⏰ সিডিউল করুন", "ask_schedule"))
    update_user(chat_id, {"post_title": title, "temp_media_id": mid_, "temp_media_type": mtype, "step": "none"})
    try:
        bot.edit_message_text(f"💎 লিংক তৈরি:\n<code>{d_link}</code>\n\nএখন পোস্ট করবেন নাকি সিডিউল করবেন?", chat_id, mid, reply_markup=m)
    except Exception:
        bot.send_message(chat_id, f"💎 লিংক তৈরি:\n<code>{d_link}</code>\n\nএখন পোস্ট করবেন নাকি সিডিউল করবেন?", reply_markup=m)

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
                "scheduled_at": dt.isoformat(), "status": "pending", "created_at": datetime.now().isoformat(),
            }
            save_db()
        update_user(chat_id, {"step": "none", "pending_link": "", "post_title": "", "temp_media_id": "", "temp_media_type": ""})
        bot.send_message(chat_id, f"⏰ <b>সিডিউল সেভ হয়েছে!</b>\n📅 সময়: <b>{text}</b>\n🆔 ID: <code>{sched_id}</code>"); return

    # ══ পোস্ট টাইটেল ══
    if step == "wait_post_title" and text:
        _finalize_post(chat_id, None, text); return

    # ══ ভিডিও-স্পেশাল ফ্লো ধাপ ২: থাম্বনেইল ছবি এসেছে ══
    if message.content_type == 'photo' and step == "wait_thumbnail" and is_admin(chat_id):
        _handle_thumbnail_received(chat_id, message.photo[-1].file_id, user)
        return

    # ══ ভিডিও-স্পেশাল ফ্লো ধাপ ৩: ডাউনলোড লিংক (শর্টেনার সাইট থেকে) এসেছে ══
    if step == "wait_dl_link" and text and is_admin(chat_id):
        _handle_download_link_received(chat_id, text, user)
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
            bot.send_message(chat_id, "🖼️ এখন এই ভিডিওর জন্য একটি ছবি থাম্বনেইল হিসেবে পাঠান:")
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
    logger.info(f"🚀 Bot Polling started (v{BOT_VERSION})...")
    while True:
        try:
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)

# ══════════════════════════════════════════════════
#  Render Web Service এর জন্য হালকা Keep-Alive সার্ভার
#  (Flask নেই — শুধু built-in http.server, health-check এর জন্য)
# ══════════════════════════════════════════════════
class _PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Bot is running")
    def log_message(self, *args):
        pass  # সার্ভার লগ চেপে রাখা হলো

def _run_keepalive_server():
    port = int(os.environ.get("PORT", 8080))
    try:
        HTTPServer(("0.0.0.0", port), _PingHandler).serve_forever()
    except Exception as e:
        logger.warning(f"Keep-alive server error: {e}")

if __name__ == "__main__":
    threading.Thread(target=_run_keepalive_server, daemon=True).start()
    run_bot()
