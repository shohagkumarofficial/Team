# Multi-Admin File Share Bot v7.0

পুরনো v6.0 বটের সরলীকৃত (Firebase/MongoDB/Flask-panel ছাড়া) মাল্টি-এডমিন ভার্সন।

## যা বাদ দেওয়া হয়েছে
- ❌ MongoDB → এখন সব ডাটা `data/database.json` ফাইলে (thread-safe লক সহ) সেভ হয়
- ❌ Firebase Realtime DB sync
- ❌ Flask Blueprint / Web Admin Panel API (`/api/...` সব রুট বাদ)
- ❌ Download-1 বাটন — এখন শুধু একটাই "📥 ডাউনলোড" বাটন (আগের Download-2 এর জায়গায়, যেটা admin-এর নিজের শর্টেনার key দিয়ে মনিটাইজড হয়)
- ❌ Log চ্যানেল ON/OFF টগল সিস্টেম → এখন `LOG_CHANNEL_ID` env-এ ফিক্সড
- ❌ Category সিস্টেম — যেহেতু এখন Ad/Premium/Log চ্যানেল single fixed সেট (env থেকে), তাই একাধিক ক্যাটাগরি বেছে পোস্ট করার দরকার নেই
- ❌ imgbb থাম্বনেইল অটো-জেনারেশন ও Firebase-নির্ভর ওয়েব ভিডিও লিস্টিং/আনলক ফ্লো (এগুলো Firebase-এর উপর নির্ভরশীল ছিল)

## নতুন রোল সিস্টেম
- **Owner** (`MAIN_ADMIN_ID`) — সবার উপরে, Admin Request Accept/Reject করে, Admin remove করতে পারে, সব ইউজারকে বা নির্দিষ্ট Admin(দের)-এর ইউজারদের ব্রডকাস্ট করতে পারে, Protect Content টগল করতে পারে।
- **Admin** — User থেকে Owner-approved হয়ে ওঠে। নিজের হেডার/ফুটার/ফিল্টার/অটো-ডিলিট/লিংক-রিপিট সেট করতে পারে, ফাইল আপলোড/পোস্ট/সিডিউল করতে পারে, নিজের শর্টেনার (TeraBox-স্টাইল) API key যোগ করে Download বাটন থেকে আর্নিং করতে পারে। Force-Sub চ্যানেলও যোগ/রিমুভ করতে পারে (Owner-এর সাথে শেয়ার্ড লিস্টে)।
- **User** — ডিফল্ট রোল। ফাইল ডাউনলোড করতে পারে, "🔑 Admin Access Request" বাটনে ক্লিক করে Admin হওয়ার জন্য আবেদন করতে পারে।

### User ↔ Admin লিংকিং (ব্রডকাস্ট টার্গেটিং-এর ভিত্তি)
1. কেউ কোনো Admin-এর রেফারেল লিংক (`https://t.me/BOT?start=ref_<admin_chat_id>`) দিয়ে ঢুকলে সে সরাসরি সেই Admin-এর সাথে লিংক হয়।
2. রেফারেল না থাকলে, প্রথমবার যে Admin-এর আপলোড করা ফাইল সে ডাউনলোড করে, সেই Admin-এর সাথে লিংক হয়।
3. সব ইউজার, লিংক যাই হোক, Owner-এর broadcast "সবাইকে" অপশনে থাকবেই।

## Broadcast (Owner-only)
- 🌐 **সবাইকে** — সিস্টেমের সব ইউজারের কাছে যাবে
- 🎯 **নির্দিষ্ট Admin(দের) সিলেক্ট করে** — শুধু সেই Admin(দের)-এর সাথে লিংকড ইউজারদের কাছে যাবে

## চ্যানেল সিস্টেম
Ad/Premium/Log চ্যানেল **শুধু Owner .env থেকে সেট করবে** — Admin-রা এগুলো দেখতে/বদলাতে পারবে না।

## প্রয়োজনীয় Environment Variables

| Variable | বিবরণ |
|---|---|
| `BOT_TOKEN` | @BotFather থেকে পাওয়া টোকেন |
| `BOT_USERNAME` | বটের ইউজারনেম (@ ছাড়া), ডাউনলোড লিংক বানাতে লাগে |
| `MAIN_ADMIN_ID` | আপনার (Owner) Telegram Chat ID |
| `AD_CHANNEL_IDS` | কমা-সেপারেটেড Ad চ্যানেল ID (যেমন `-1001,-1002`) |
| `PREMIUM_CHANNEL_IDS` | কমা-সেপারেটেড Premium চ্যানেল ID (ঐচ্ছিক) |
| `LOG_CHANNEL_ID` | ব্যাকআপ লগ চ্যানেল ID (ঐচ্ছিক) |
| `SHORTENER_API_BASE` | শর্টেনার API base URL (ডিফল্ট: `https://teraboxlinks.com/api`) |
| `DATA_FILE` | ডাটাবেস JSON ফাইলের পাথ (ডিফল্ট: `data/database.json`) |
| `PORT` | Render Web Service পোর্ট (ডিফল্ট: `8080`, Render নিজে সেট করে দেয়) |

Chat ID বের করতে বটে `/myid` কমান্ড ব্যবহার করুন।

## Render-এ ডিপ্লয়
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python shortenerbot.py`
- একটা **Web Service** হিসেবেই ডিপ্লয় করা যাবে — বটের ভেতরে একটা হালকা keep-alive HTTP সার্ভার (Flask ছাড়া, built-in `http.server`) আছে যেটা health-check পাশ করাবে।
- ⚠️ **গুরুত্বপূর্ণ:** Render-এর ফ্রি ডিস্ক ইফেমেরাল (ephemeral) — রিডিপ্লয় হলে `data/database.json` মুছে যেতে পারে। ডাটা স্থায়ীভাবে রাখতে Render Disk (Persistent Disk) অ্যাড-অন যোগ করুন এবং `DATA_FILE` env দিয়ে সেই disk-mount পাথ দেখিয়ে দিন (যেমন `/var/data/database.json`)।

## প্রথমবার সেটআপ
1. env var-এ নিজের Chat ID `MAIN_ADMIN_ID`-তে বসান — তাহলে বট চালু হলে আপনি অটোমেটিক Owner হয়ে যাবেন।
2. `/start` দিন — Owner প্যানেল দেখবেন।
3. যাকে Admin বানাতে চান, তাকে বটে `/start` দিতে বলুন → সে "🔑 Admin Access Request" চাপবে → আপনার কাছে Accept/Reject নোটিফিকেশন আসবে।
4. Accept করলে সে Admin হয়ে যাবে এবং নিজের শর্টেনার API key সেট করে আর্নিং শুরু করতে পারবে।
