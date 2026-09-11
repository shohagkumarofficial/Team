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
| `CF_ACCOUNT_ID` | Cloudflare Account ID (Cloudflare D1-এর জন্য) |
| `CF_D1_DATABASE_ID` | Cloudflare D1 Database ID |
| `CF_API_TOKEN` | Cloudflare API Token (D1 Edit পারমিশন সহ) |

Chat ID বের করতে বটে `/myid` কমান্ড ব্যবহার করুন।

## Render-এ ডিপ্লয় ও ডাটাবেস স্থায়ী করা
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python shortenerbot.py`
- একটা **Web Service** হিসেবে ডিপ্লয় করা যাবে — বটের ভেতরে হালকা keep-alive HTTP সার্ভার (UptimeRobot ও Self-Ping সাপোর্ট সহ) আছে।
- ☁️ **ডাটাবেস স্থায়ী রাখতে Cloudflare D1 (১০০% ফ্রি):**
  Render-এর ফ্রি সার্ভার রিস্টার্ট হলে লোকাল ফাইল মুছে যায়। কিন্তু **Cloudflare D1** ব্যবহার করলে বটের সমস্ত ফাইল, ইউজার ও সেটিংস আজীবন ক্লাউডে অক্ষত থাকবে।

## Cloudflare D1 সেটাপ করার সহজ নিয়ম (প্রথমবার যারা করছেন)
1. **Cloudflare একাউন্ট:** [dash.cloudflare.com](https://dash.cloudflare.com)-এ ফ্রি একাউন্ট খুলুন বা লগইন করুন।
2. **D1 ডাটাবেস তৈরি:**
   - বাম পাশের মেনু থেকে **Storage & Databases** → **D1 SQL Database**-এ যান।
   - **Create database** বাটনে ক্লিক করুন।
   - ডাটাবেসের একটি নাম দিন (যেমন: `shortenerbot-db`) এবং **Create** চাপুন।
   - তৈরি হওয়ার পর স্ক্রিনে **Database ID** এবং **Account ID** দেখতে পাবেন। এগুলো কপি করে রাখুন।
3. **API Token তৈরি:**
   - ডানদিকের উপরে আপনার প্রোফাইল আইকন → **My Profile** → **API Tokens**-এ যান (অথবা [এখানে যান](https://dash.cloudflare.com/profile/api-tokens))।
   - **Create Token** বাটনে ক্লিক করুন।
   - নিচে স্ক্রল করে **Create Custom Token**-এর পাশে **Get started** দিন।
   - **Token name:** যেকোনো নাম দিন (যেমন: `Bot-D1-Token`)।
   - **Permissions:**
     - প্রথম ড্রপডাউনে **Account**, দ্বিতীয়টিতে **D1**, তৃতীয়টিতে **Edit** সিলেক্ট করুন।
   - নিচে **Continue to summary** → **Create Token** দিন।
   - যে টোকেনটি দেখতে পাবেন সেটি কপি করে রাখুন (এটি আর পরে দেখা যাবে না)।
4. **Render-এ ভেরিয়েবল যোগ:**
   - Render ড্যাশবোর্ডে গিয়ে আপনার বটের **Environment** সেকশনে এই ৩টি ভেরিয়েবল যোগ করুন:
     - `CF_ACCOUNT_ID` = আপনার Account ID
     - `CF_D1_DATABASE_ID` = আপনার Database ID
     - `CF_API_TOKEN` = আপনার API Token
   - Save Changes দিলে Render বট রিস্টার্ট করবে এবং স্বয়ংক্রিয়ভাবে ডাটাবেস কানেক্ট হয়ে যাবে!
5. **যাচাই করা:** বটে `/start` দিয়ে Owner প্যানেলে **☁️ Cloudflare DB স্ট্যাটাস** বাটনে ক্লিক করে কানেকশন সবুজ (Connected) দেখতে পাবেন।
