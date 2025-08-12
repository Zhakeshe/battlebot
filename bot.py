import os
import time
import threading
import random
import aiosqlite
from flask import Flask, request, abort
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = "7575484165:AAE0hi2FrnahXlGTK_udxuzsqsKDH5xmFvI"
ADMIN_ID = 8073910583
CHANNEL_ID = -1002758370194
WEBHOOK_URL_BASE = os.getenv("WEBHOOK_URL_BASE", "")  # Render өзі береді, сондықтан орта айнымалылардан аламыз
WEBHOOK_URL_PATH = f"/{BOT_TOKEN}/"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ========== SQLite ИНИЦИАЛИЗАЦИЯ ==========
async def init_db():
    async with aiosqlite.connect("battle.db") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS battles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            max_players INTEGER,
            duration INTEGER,
            message_id INTEGER,
            status TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            battle_id INTEGER,
            user_id INTEGER,
            username TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            battle_id INTEGER,
            voter_id INTEGER,
            voted_user TEXT
        )""")
        await db.commit()

def run_async(coro):
    import asyncio
    return asyncio.run(coro)

# ========= КНОПКАЛАР ===========
def join_button():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📥 Қатысу!", callback_data="join"))
    return markup

def vote_buttons(user1, user2, battle_id):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton(f"1. {user1}", callback_data=f"vote:{battle_id}:{user1}"),
        InlineKeyboardButton(f"2. {user2}", callback_data=f"vote:{battle_id}:{user2}")
    )
    return markup

# ========= АДМИН КОМАНДАСЫ ==========
@bot.message_handler(commands=["newbattle"])
def cmd_newbattle(message):
    if message.from_user.id != ADMIN_ID:
        return
    bot.send_message(message.chat.id, "Батл атауын жазыңыз:")
    bot.register_next_step_handler(message, newbattle_max_players)

def newbattle_max_players(message):
    try:
        max_players = int(message.text)
        bot.send_message(message.chat.id, "Ұзақтығы минутпен:")
        bot.register_next_step_handler(message, newbattle_duration, max_players)
    except:
        bot.send_message(message.chat.id, "Қате! Санмен жауап беріңіз.")
        bot.register_next_step_handler(message, newbattle_max_players)

def newbattle_duration(message, max_players):
    try:
        duration = int(message.text)
    except:
        bot.send_message(message.chat.id, "Қате! Санмен жауап беріңіз.")
        bot.register_next_step_handler(message, newbattle_duration, max_players)
        return

    title = message.reply_to_message.text if message.reply_to_message else "Батл"

    async def save_battle():
        async with aiosqlite.connect("battle.db") as db:
            await db.execute("INSERT INTO battles (title, max_players, duration, status) VALUES (?, ?, ?, ?)",
                             (title, max_players, duration, "waiting"))
            await db.commit()
            cur = await db.execute("SELECT last_insert_rowid()")
            battle_id = (await cur.fetchone())[0]

            # Каналға хабарламаны жіберу
            text = f"🔥 Батл\n📌 Батл атауы: {title}\n👥 Қатысушы саны: 0/{max_players}\n"
            for i in range(1, max_players+1):
                text += f"{i}. —\n"

            msg = bot.send_message(CHANNEL_ID, text, reply_markup=join_button())

    run_async(save_battle())
    bot.send_message(message.chat.id, "✅ Батл жарияланды!")

# ========= ҚАТЫСУ ===========
@bot.callback_query_handler(func=lambda c: c.data == "join")
def join_battle(call):
    async def inner():
        async with aiosqlite.connect("battle.db") as db:
            cur = await db.execute("SELECT * FROM battles WHERE status='waiting' ORDER BY id DESC LIMIT 1")
            battle = await cur.fetchone()
            if not battle:
                bot.answer_callback_query(call.id, "Батл жоқ!", show_alert=True)
                return

            battle_id, title, max_players, duration, message_id, status = battle[:6]

            if not call.from_user.username:
                bot.answer_callback_query(call.id, "Алдымен username орнатыңыз!", show_alert=True)
                return

            cur = await db.execute("SELECT * FROM players WHERE battle_id=? AND user_id=?", (battle_id, call.from_user.id))
            if await cur.fetchone():
                bot.answer_callback_query(call.id, "Сіз бұрын тіркелдіңіз!", show_alert=True)
                return

            cur = await db.execute("SELECT COUNT(*) FROM players WHERE battle_id=?", (battle_id,))
            count = (await cur.fetchone())[0]
            if count >= max_players:
                bot.answer_callback_query(call.id, "Орын толды!", show_alert=True)
                return

            await db.execute("INSERT INTO players (battle_id, user_id, username) VALUES (?, ?, ?)",
                             (battle_id, call.from_user.id, f"@{call.from_user.username}"))
            await db.commit()

            cur = await db.execute("SELECT username FROM players WHERE battle_id=?", (battle_id,))
            players = [row[0] for row in await cur.fetchall()]

            text = f"🔥 Батл\n📌 Батл атауы: {title}\n👥 Қатысушы саны: {len(players)}/{max_players}\n"
            for i in range(max_players):
                text += f"{i+1}. {players[i] if i < len(players) else '—'}\n"

            try:
                bot.edit_message_text(text, CHANNEL_ID, message_id, reply_markup=join_button())
            except Exception as e:
                print("Edit message error:", e)

            if len(players) == max_players:
                await db.execute("UPDATE battles SET status='started' WHERE id=?", (battle_id,))
                await db.commit()
                threading.Thread(target=start_battle_sync, args=(battle_id, duration)).start()

            bot.answer_callback_query(call.id, "Сіз тіркелдіңіз!")

    threading.Thread(target=run_async, args=(inner(),)).start()

# ========= БАТЛ БАСТАУ ===========
def start_battle_sync(battle_id, duration):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_battle(battle_id, duration))

async def start_battle(battle_id, duration):
    async with aiosqlite.connect("battle.db") as db:
        cur = await db.execute("SELECT username FROM players WHERE battle_id=?", (battle_id,))
        players = [p[0] for p in await cur.fetchall()]

    random.shuffle(players)
    pairs = [players[i:i+2] for i in range(0, len(players), 2)]

    for pair in pairs:
        if len(pair) == 2:
            bot.send_message(CHANNEL_ID, f"🥊 Батл:\n1. {pair[0]}\n2. {pair[1]}\n⏳ Ұзақтығы: {duration} минут",
                             reply_markup=vote_buttons(pair[0], pair[1], battle_id))

    await asyncio.sleep(duration * 60)
    await finish_battle(battle_id)

# ========= ДАУЫС ============
@bot.callback_query_handler(func=lambda c: c.data.startswith("vote:"))
def handle_vote(call):
    parts = call.data.split(":")
    if len(parts) != 3:
        bot.answer_callback_query(call.id, "Қате!")
        return
    _, battle_id_str, voted_user = parts
    battle_id = int(battle_id_str)

    async def inner():
        async with aiosqlite.connect("battle.db") as db:
            cur = await db.execute("SELECT * FROM votes WHERE battle_id=? AND voter_id=?", (battle_id, call.from_user.id))
            if await cur.fetchone():
                bot.answer_callback_query(call.id, "Бұл батлда дауыс бердіңіз!", show_alert=True)
                return

            await db.execute("INSERT INTO votes (battle_id, voter_id, voted_user) VALUES (?, ?, ?)",
                             (battle_id, call.from_user.id, voted_user))
            await db.commit()

        bot.answer_callback_query(call.id, "Дауыс қабылданды!")

    threading.Thread(target=run_async, args=(inner(),)).start()

# ========= ЖЕҢІМПАЗ ============
async def finish_battle(battle_id):
    async with aiosqlite.connect("battle.db") as db:
        cur = await db.execute("SELECT voted_user, COUNT(*) as cnt FROM votes WHERE battle_id=? GROUP BY voted_user", (battle_id,))
        results = await cur.fetchall()

    if not results:
        bot.send_message(CHANNEL_ID, "Нәтиже жоқ!")
        return

    results.sort(key=lambda x: x[1], reverse=True)
    winner, score = results[0]
    bot.send_message(CHANNEL_ID, f"🏆 Жеңімпаз: {winner} ({score} дауыс)")

# ========= FLASK ВЕБХУК ===========
@app.route(WEBHOOK_URL_PATH, methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ""
    else:
        abort(403)

@app.route("/")
def index():
    return "Bot is running"

def set_webhook():
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL_BASE + WEBHOOK_URL_PATH)

if __name__ == "__main__":
    import asyncio
    asyncio.run(init_db())
    set_webhook()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
