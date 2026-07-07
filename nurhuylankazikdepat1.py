import os
import json
import random
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler

# Load environment variables
load_dotenv()

# Initialize logging first so database initialization logs correctly
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8606720265:AAGDZJhk_uW7RT3SdTBzRXuuz93eD7RU65Q")
DATABASE_URL = os.getenv("DATABASE_URL")

# Paths
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"

# Try connecting and initializing PostgreSQL database
def init_db():
    if not DATABASE_URL:
        logger.info("No DATABASE_URL environment variable found. Using local JSON database.")
        return False
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id VARCHAR(50) PRIMARY KEY,
                coins BIGINT DEFAULT 1000,
                last_daily VARCHAR(100),
                lang VARCHAR(10) DEFAULT 'ru',
                first_name VARCHAR(255),
                stats JSONB DEFAULT '{"games_played": 0, "games_won": 0, "total_won": 0, "total_lost": 0}'::jsonb,
                registered_at VARCHAR(100)
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        logger.info("PostgreSQL database initialized successfully.")
        return True
    except Exception as e:
        logger.error(f"PostgreSQL connection failed: {e}. Falling back to JSON database.")
        return False

IS_POSTGRES = init_db()

# Load or initialize user data (only for local JSON fallback)
users = {}
if not IS_POSTGRES and USERS_FILE.exists():
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
    except Exception as e:
        logger.error(f"Error loading users.json: {e}")

INITIAL_COINS = 1000  # starting balance for new users

# In-memory game states for active multi-turn games
ACTIVE_GAMES = {}

# Translations
LANGUAGES = {
    "ru": {
        "welcome": "🎰 *Добро пожаловать в TG Casino, {name}!* 🎰\n\n💵 Ваш баланс: *{coins} монет*\n🏆 Уровень: *{level}*\n\nВыберите действие в меню ниже:",
        "menu_games": "🎮 *Выберите игру для начала:*",
        "menu_profile": "👤 *Ваш профиль:*\n\nID: `{uid}`\n💵 Баланс: *{coins} монет*\n🏆 Уровень: *{level}* ({exp} опыта)\n\n📊 *Статистика:*\n🎮 Всего игр: {games_played}\n✅ Побед: {games_won} ({win_rate}%)\n📈 Всего выиграно: {total_won} монет\n📉 Всего проиграно: {total_lost} монет",
        "menu_leaderboard": "🏆 *Топ 10 богатейших игроков:*",
        "leaderboard_row": "{index}. {name} — *{coins} монет* (Ур. {level})",
        "menu_lang": "🌐 *Выберите язык / Select language:*",
        "lang_set": "Язык изменен на Русский! 🇷🇺",
        
        "choose_bet": "💰 Выберите размер ставки для игры *{game}*:\n💵 Ваш баланс: *{coins} монет*",
        "invalid_bet": "❌ Недостаточно монет или неверная ставка!",
        
        # Slots
        "slots_spinning": "🎰 *СЛОТЫ КРУТЯТСЯ...* 🎰\n\n[ 🔄 | 🔄 | 🔄 ]",
        "slots_result": "🎰 *Результат Слотов:* 🎰\n\n{reels}\n\n{status_msg}\n💵 Выигрыш: *{win} монет*\n💰 Ваш баланс: *{coins} монет*",
        "slots_jackpot": "🎉 *ДЖЕКПОТ! Поздравляем!* 🎉",
        "slots_win": "🎉 *Вы выиграли!*",
        "slots_lose": "😢 *Вы проиграли.*",
        
        # Blackjack
        "bj_title": "🃏 *Блэкджек (Ставка: {bet})* 🃏",
        "bj_player_hand": "👤 *Ваша рука:* {hand} *(Счет: {score})*",
        "bj_dealer_hand": "🤖 *Рука дилера:* {hand} *(Счет: {score})*",
        "bj_win": "🎉 *Вы выиграли!* +{win} монет.",
        "bj_bj": "🔥 *БЛЭКДЖЕК!* Вы выиграли {win} монет!",
        "bj_lose": "😢 *Вы проиграли.* Дилер выиграл.",
        "bj_bust": "💥 *Перебор!* Вы проиграли {bet} монет.",
        "bj_push": "🤝 *Ничья (Push).* Ставка возвращена.",
        "bj_action": "Выберите ход:",
        
        # Roulette
        "roulette_title": "🎡 *Рулетка (Ставка: {bet})* 🎡",
        "roulette_choice": "Выберите тип ставки:",
        "roulette_spinning": "🎡 *Шарик катится...* 🎡\n\n🟢 [0] ... 🔴 [14] ... ⚫ [32] ...",
        "roulette_result": "🎡 *Результат Рулетки:* 🎡\n\nВыпало: {color_emoji} *{number} {color_name}*\nВаша ставка: {bet_type}\n\n{status_msg}\n💵 Выигрыш: *{win} монет*\n💰 Ваш баланс: *{coins} монет*",
        "roulette_red": "Красное",
        "roulette_black": "Черное",
        "roulette_green": "Зеленое (0)",
        "roulette_even": "Четное",
        "roulette_odd": "Нечетное",
        
        # Coin Flip
        "cf_title": "🪙 *Монетка (Ставка: {bet})* 🪙",
        "cf_choice": "На что ставите?",
        "cf_heads": "🦅 Орел",
        "cf_tails": "🪙 Решка",
        "cf_flipping": "🪙 *Монетка подброшена...* 🪙\n\n🔄 крутится в воздухе...",
        "cf_result": "🪙 *Результат броска:* 🪙\n\nВыпало: *{result_text}*\nВаша ставка: *{bet_type}*\n\n{status_msg}\n💵 Выигрыш: *{win} монет*\n💰 Ваш баланс: *{coins} монет*",
        
        # Crash
        "crash_title": "📈 *Crash (Ставка: {bet})* 📈",
        "crash_preparing": "🚀 *Ракета готовится к старту...*",
        "crash_flying": "🚀 *Ракета летит!*\n\n📈 Текущий множитель: *{multiplier:.2f}x*\n💰 Возможный выигрыш: *{potential:.0f} монет*",
        "crash_crashed": "💥 *БАБАХ!* Ракета взорвалась на *{multiplier:.2f}x*!\n😢 Вы потеряли *{bet} монет*.",
        "crash_cashed": "🎉 *УСПЕШНЫЙ ВЫВОД!* 🎉\nВы забрали выигрыш на *{multiplier:.2f}x*!\n💵 Выиграно: *{win:.0f} монет*\n💰 Баланс: *{coins} монет*",
        
        # Wheel of Fortune
        "wheel_title": "🎁 *Колесо Фортуны* 🎁",
        "wheel_cooldown": "⏳ Вы уже крутили колесо сегодня!\nСледующая попытка через: *{hrs}ч {mins}м*",
        "wheel_spinning": "🎡 *Колесо Фортуны вращается...* 🎡\n\n💎 5000 ... 💵 100 ... 💎 1000 ...",
        "wheel_result": "🎡 *Колесо Фортуны* 🎡\n\n🎉 Вы выиграли: *{bonus} монет*!\n💰 Баланс: *{coins} монет*",
        
        # Buttons
        "btn_games": "🎮 Игры",
        "btn_profile": "👤 Профиль",
        "btn_wheel": "🎁 Колесо Фортуны",
        "btn_leaderboard": "🏆 Лидеры",
        "btn_lang": "🌐 Язык",
        "btn_back": "🔙 Назад",
        "btn_back_games": "🔙 К играм",
        
        "game_slots": "🎰 Слоты",
        "game_blackjack": "🃏 Блэкджек",
        "game_roulette": "🎡 Рулетка",
        "game_coinflip": "🪙 Монетка",
        "game_crash": "📈 Краш",
        
        "btn_all_in": "🔥 На все",
        "btn_hit": "🃏 Еще",
        "btn_stand": "🛑 Достаточно",
        "btn_cash_out": "🚀 Забрать {multiplier:.2f}x",
    },
    "en": {
        "welcome": "🎰 *Welcome to TG Casino, {name}!* 🎰\n\n💵 Your balance: *{coins} coins*\n🏆 Level: *{level}*\n\nSelect an action from the menu below:",
        "menu_games": "🎮 *Select a game to start playing:*",
        "menu_profile": "👤 *Your Profile:*\n\nID: `{uid}`\n💵 Balance: *{coins} coins*\n🏆 Level: *{level}* ({exp} xp)\n\n📊 *Statistics:*\n🎮 Total Games: {games_played}\n✅ Wins: {games_won} ({win_rate}%)\n📈 Total Won: {total_won} coins\n📉 Total Lost: {total_lost} coins",
        "menu_leaderboard": "🏆 *Top 10 Richest Players:*",
        "leaderboard_row": "{index}. {name} — *{coins} coins* (Lvl {level})",
        "menu_lang": "🌐 *Select language / Выберите язык:*",
        "lang_set": "Language set to English! 🇺🇸",
        
        "choose_bet": "💰 Select bet amount for *{game}*:\n💵 Your balance: *{coins} coins*",
        "invalid_bet": "❌ Not enough coins or invalid bet size!",
        
        # Slots
        "slots_spinning": "🎰 *SLOTS SPINNING...* 🎰\n\n[ 🔄 | 🔄 | 🔄 ]",
        "slots_result": "🎰 *Slots Result:* 🎰\n\n{reels}\n\n{status_msg}\n💵 Payout: *{win} coins*\n💰 Your balance: *{coins} coins*",
        "slots_jackpot": "🎉 *JACKPOT! Congratulations!* 🎉",
        "slots_win": "🎉 *You won!*",
        "slots_lose": "😢 *You lost.*",
        
        # Blackjack
        "bj_title": "🃏 *Blackjack (Bet: {bet})* 🃏",
        "bj_player_hand": "👤 *Your Hand:* {hand} *(Score: {score})*",
        "bj_dealer_hand": "🤖 *Dealer's Hand:* {hand} *(Score: {score})*",
        "bj_win": "🎉 *You won!* +{win} coins.",
        "bj_bj": "🔥 *BLACKJACK!* You won {win} coins!",
        "bj_lose": "😢 *You lost.* Dealer wins.",
        "bj_bust": "💥 *Bust!* You lost {bet} coins.",
        "bj_push": "🤝 *Push (Draw).* Bet returned.",
        "bj_action": "Choose your move:",
        
        # Roulette
        "roulette_title": "🎡 *Roulette (Bet: {bet})* 🎡",
        "roulette_choice": "Select bet type:",
        "roulette_spinning": "🎡 *Roulette is spinning...* 🎡\n\n🟢 [0] ... 🔴 [14] ... ⚫ [32] ...",
        "roulette_result": "🎡 *Roulette Result:* 🎡\n\nRolled: {color_emoji} *{number} {color_name}*\nYour bet: {bet_type}\n\n{status_msg}\n💵 Payout: *{win} coins*\n💰 Your balance: *{coins} coins*",
        "roulette_red": "Red",
        "roulette_black": "Black",
        "roulette_green": "Green (0)",
        "roulette_even": "Even",
        "roulette_odd": "Odd",
        
        # Coin Flip
        "cf_title": "🪙 *Coin Flip (Bet: {bet})* 🪙",
        "cf_choice": "What is your call?",
        "cf_heads": "🦅 Heads",
        "cf_tails": "🪙 Tails",
        "cf_flipping": "🪙 *Flipping coin...* 🪙\n\n🔄 spinning in midair...",
        "cf_result": "🪙 *Coin Flip Result:* 🪙\n\nResult: *{result_text}*\nYour bet: *{bet_type}*\n\n{status_msg}\n💵 Payout: *{win} coins*\n💰 Your balance: *{coins} coins*",
        
        # Crash
        "crash_title": "📈 *Crash (Bet: {bet})* 📈",
        "crash_preparing": "🚀 *Rocket is preparing for liftoff...*",
        "crash_flying": "🚀 *Rocket is flying!*\n\n📈 Current multiplier: *{multiplier:.2f}x*\n💰 Potential payout: *{potential:.0f} coins*",
        "crash_crashed": "💥 *BOOM!* Rocket crashed at *{multiplier:.2f}x*!\n😢 You lost *{bet} coins*.",
        "crash_cashed": "🎉 *CASHED OUT!* 🎉\nYou cashed out at *{multiplier:.2f}x*!\n💵 Won: *{win:.0f} coins*\n💰 Balance: *{coins} coins*",
        
        # Wheel of Fortune
        "wheel_title": "🎁 *Wheel of Fortune* 🎁",
        "wheel_cooldown": "⏳ You have already spun the wheel today!\nNext spin available in: *{hrs}h {mins}m*",
        "wheel_spinning": "🎡 *Wheel is spinning...* 🎡\n\n💎 5000 ... 💵 100 ... 💎 1000 ...",
        "wheel_result": "🎡 *Wheel of Fortune* 🎡\n\n🎉 You won: *{bonus} coins*!\n💰 Balance: *{coins} coins*",
        
        # Buttons
        "btn_games": "🎮 Games",
        "btn_profile": "👤 Profile",
        "btn_wheel": "🎁 Wheel of Fortune",
        "btn_leaderboard": "🏆 Leaderboard",
        "btn_lang": "🌐 Language",
        "btn_back": "🔙 Back",
        "btn_back_games": "🔙 To Games",
        
        "game_slots": "🎰 Slots",
        "game_blackjack": "🃏 Blackjack",
        "game_roulette": "🎡 Roulette",
        "game_coinflip": "🪙 Coin Flip",
        "game_crash": "📈 Crash",
        
        "btn_all_in": "🔥 All-in",
        "btn_hit": "🃏 Hit",
        "btn_stand": "🛑 Stand",
        "btn_cash_out": "🚀 Cash Out {multiplier:.2f}x",
    }
}

def save_users():
    if not IS_POSTGRES:
        try:
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump(users, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving users.json: {e}")
    else:
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()
            for user_id, data in list(users.items()):
                cur.execute(
                    """
                    INSERT INTO users (user_id, coins, last_daily, lang, first_name, stats, registered_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        coins = EXCLUDED.coins,
                        last_daily = EXCLUDED.last_daily,
                        lang = EXCLUDED.lang,
                        first_name = EXCLUDED.first_name,
                        stats = EXCLUDED.stats;
                    """,
                    (user_id, data["coins"], data["last_daily"], data["lang"], data["first_name"], json.dumps(data["stats"]), data["registered_at"])
                )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"Error syncing users to Postgres: {e}")

def get_user(user_id: str, first_name: str = None):
    if user_id not in users:
        if IS_POSTGRES:
            try:
                import psycopg2
                conn = psycopg2.connect(DATABASE_URL)
                cur = conn.cursor()
                cur.execute("SELECT coins, last_daily, lang, first_name, stats, registered_at FROM users WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
                cur.close()
                conn.close()
                if row:
                    coins, last_daily, lang, db_fname, stats, registered_at = row
                    if isinstance(stats, str):
                        stats = json.loads(stats)
                    users[user_id] = {
                        "coins": coins,
                        "last_daily": last_daily,
                        "lang": lang,
                        "first_name": db_fname,
                        "stats": stats,
                        "registered_at": registered_at
                    }
                else:
                    users[user_id] = {
                        "coins": INITIAL_COINS,
                        "last_daily": None,
                        "lang": "ru",
                        "first_name": first_name or f"Player {user_id[:6]}",
                        "stats": {
                            "games_played": 0,
                            "games_won": 0,
                            "total_won": 0,
                            "total_lost": 0
                        },
                        "registered_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                    }
                    save_users()
            except Exception as e:
                logger.error(f"Error getting user from Postgres: {e}")
                # Fallback structure
                users[user_id] = {
                    "coins": INITIAL_COINS,
                    "last_daily": None,
                    "lang": "ru",
                    "first_name": first_name or f"Player {user_id[:6]}",
                    "stats": {"games_played": 0, "games_won": 0, "total_won": 0, "total_lost": 0},
                    "registered_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                }
        else:
            users[user_id] = {
                "coins": INITIAL_COINS,
                "last_daily": None,
                "lang": "ru",
                "first_name": first_name or f"Player {user_id[:6]}",
                "stats": {
                    "games_played": 0,
                    "games_won": 0,
                    "total_won": 0,
                    "total_lost": 0
                },
                "registered_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            }
            save_users()
            
    user = users[user_id]
    updated = False
    if first_name and user.get("first_name") != first_name:
        user["first_name"] = first_name
        updated = True
    if "stats" not in user:
        user["stats"] = {
            "games_played": 0,
            "games_won": 0,
            "total_won": 0,
            "total_lost": 0
        }
        updated = True
    if "registered_at" not in user:
        user["registered_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        updated = True
    if "lang" not in user or user["lang"] not in ["ru", "en"]:
        user["lang"] = "ru"
        updated = True
        
    if updated:
        save_users()
        
    return user

def get_leaderboard_data(limit=10):
    if not IS_POSTGRES:
        top = sorted(users.items(), key=lambda item: item[1].get("coins", 0), reverse=True)[:limit]
        return [(uid, data) for uid, data in top]
    else:
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()
            cur.execute("SELECT user_id, coins, last_daily, lang, first_name, stats, registered_at FROM users ORDER BY coins DESC LIMIT %s", (limit,))
            rows = cur.fetchall()
            cur.close()
            conn.close()
            res = []
            for row in rows:
                uid, coins, last_daily, lang, db_fname, stats, registered_at = row
                if isinstance(stats, str):
                    stats = json.loads(stats)
                res.append((uid, {
                    "coins": coins,
                    "last_daily": last_daily,
                    "lang": lang,
                    "first_name": db_fname,
                    "stats": stats,
                    "registered_at": registered_at
                }))
            return res
        except Exception as e:
            logger.error(f"Error fetching leaderboard from Postgres: {e}")
            # Fallback to local dict top
            top = sorted(users.items(), key=lambda item: item[1].get("coins", 0), reverse=True)[:limit]
            return [(uid, data) for uid, data in top]

def get_text(user_id: str, key: str, **kwargs):
    lang = get_user(user_id).get("lang", "ru")
    return LANGUAGES[lang][key].format(**kwargs)

# Helper function to edit messages safely without crashing on RateLimits/MessageUnmodified errors
async def edit_msg(query, text, reply_markup=None):
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error editing message: {e}")

# --- KEYBOARD BUILDERS ---

def get_main_menu_keyboard(uid):
    keyboard = [
        [InlineKeyboardButton(get_text(uid, "btn_games"), callback_data="menu_games")],
        [InlineKeyboardButton(get_text(uid, "btn_profile"), callback_data="menu_profile"),
         InlineKeyboardButton(get_text(uid, "btn_wheel"), callback_data="menu_wheel")],
        [InlineKeyboardButton(get_text(uid, "btn_leaderboard"), callback_data="menu_leaderboard"),
         InlineKeyboardButton(get_text(uid, "btn_lang"), callback_data="menu_lang")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_games_menu_keyboard(uid):
    keyboard = [
        [InlineKeyboardButton(get_text(uid, "game_slots"), callback_data="bet_select_slots"),
         InlineKeyboardButton(get_text(uid, "game_blackjack"), callback_data="bet_select_blackjack")],
        [InlineKeyboardButton(get_text(uid, "game_roulette"), callback_data="bet_select_roulette"),
         InlineKeyboardButton(get_text(uid, "game_coinflip"), callback_data="bet_select_coinflip")],
        [InlineKeyboardButton(get_text(uid, "game_crash"), callback_data="bet_select_crash")],
        [InlineKeyboardButton(get_text(uid, "btn_back"), callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- VIEWS & NAVIGATION ---

async def send_main_menu(update_or_query, uid, edit=False):
    user = get_user(uid)
    xp = user["stats"].get("total_won", 0) + user["stats"].get("total_lost", 0)
    level = int(xp // 1000) + 1
    
    first_name = ""
    if hasattr(update_or_query, "message") and update_or_query.message:
        first_name = update_or_query.effective_user.first_name
    elif hasattr(update_or_query, "from_user"):
        first_name = update_or_query.from_user.first_name
        
    text = get_text(uid, "welcome", name=first_name, coins=user["coins"], level=level)
    markup = get_main_menu_keyboard(uid)
    
    if edit:
        await edit_msg(update_or_query, text, markup)
    else:
        if hasattr(update_or_query, "message") and update_or_query.message:
            await update_or_query.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')
        else:
            await update_or_query.reply_text(text, reply_markup=markup, parse_mode='Markdown')

async def send_games_menu(query, uid):
    text = get_text(uid, "menu_games")
    markup = get_games_menu_keyboard(uid)
    await edit_msg(query, text, markup)

async def send_profile(query, uid):
    user = get_user(uid)
    xp = user["stats"].get("total_won", 0) + user["stats"].get("total_lost", 0)
    level = int(xp // 1000) + 1
    
    games_played = user["stats"].get("games_played", 0)
    games_won = user["stats"].get("games_won", 0)
    win_rate = round((games_won / games_played) * 100, 1) if games_played > 0 else 0.0
    
    text = get_text(uid, "menu_profile",
                    uid=uid,
                    coins=user["coins"],
                    level=level,
                    exp=xp,
                    games_played=games_played,
                    games_won=games_won,
                    win_rate=win_rate,
                    total_won=user["stats"].get("total_won", 0),
                    total_lost=user["stats"].get("total_lost", 0))
                    
    keyboard = [[InlineKeyboardButton(get_text(uid, "btn_back"), callback_data="menu_main")]]
    await edit_msg(query, text, InlineKeyboardMarkup(keyboard))

async def send_leaderboard(query, uid):
    top = get_leaderboard_data()
    
    lines = [get_text(uid, "menu_leaderboard"), ""]
    for i, (userid, data) in enumerate(top, 1):
        xp = data.get("stats", {}).get("total_won", 0) + data.get("stats", {}).get("total_lost", 0)
        level = int(xp // 1000) + 1
        name = data.get("first_name", f"Player {userid[:6]}")
        lines.append(get_text(uid, "leaderboard_row", index=i, name=name, coins=data.get("coins", 0), level=level))
        
    keyboard = [[InlineKeyboardButton(get_text(uid, "btn_back"), callback_data="menu_main")]]
    await edit_msg(query, "\n".join(lines), InlineKeyboardMarkup(keyboard))

async def send_lang_menu(query, uid):
    text = get_text(uid, "menu_lang")
    keyboard = [[
        InlineKeyboardButton("🇷🇺 Русский", callback_data="setlang_ru"),
        InlineKeyboardButton("🇺🇸 English", callback_data="setlang_en")
    ], [
        InlineKeyboardButton(get_text(uid, "btn_back"), callback_data="menu_main")
    ]]
    await edit_msg(query, text, InlineKeyboardMarkup(keyboard))

async def send_bet_selection(query, uid, game):
    user = get_user(uid)
    text = get_text(uid, "choose_bet", game=get_text(uid, f"game_{game}"), coins=user["coins"])
    
    keyboard = [
        [
            InlineKeyboardButton("💵 10", callback_data=f"play_{game}_10"),
            InlineKeyboardButton("💵 50", callback_data=f"play_{game}_50"),
            InlineKeyboardButton("💵 100", callback_data=f"play_{game}_100"),
        ],
        [
            InlineKeyboardButton("💵 250", callback_data=f"play_{game}_250"),
            InlineKeyboardButton("💵 500", callback_data=f"play_{game}_500"),
            InlineKeyboardButton("💵 1000", callback_data=f"play_{game}_1000"),
        ],
        [
            InlineKeyboardButton(get_text(uid, "btn_all_in"), callback_data=f"play_{game}_allin"),
        ],
        [
            InlineKeyboardButton(get_text(uid, "btn_back_games"), callback_data="menu_games")
        ]
    ]
    await edit_msg(query, text, InlineKeyboardMarkup(keyboard))

# --- GAME ENGINE LOGIC ---

# 🎰 SLOTS
async def run_slots_game(query, uid, bet):
    user = get_user(uid)
    user["coins"] -= bet
    user["stats"]["games_played"] = user["stats"].get("games_played", 0) + 1
    user["stats"]["total_lost"] = user["stats"].get("total_lost", 0) + bet
    save_users()
    
    await edit_msg(query, get_text(uid, "slots_spinning"))
    await asyncio.sleep(1.2)
    
    reels_symbols = ["🍒", "🍋", "🍇", "🔔", "💎", "7️⃣"]
    weights = [0.25, 0.25, 0.20, 0.15, 0.10, 0.05]
    
    roll = random.choices(reels_symbols, weights=weights, k=3)
    reels_display = f"[ {roll[0]} | {roll[1]} | {roll[2]} ]"
    
    win = 0
    msg_key = "slots_lose"
    
    if roll[0] == roll[1] == roll[2]:
        if roll[0] == "7️⃣":
            win = bet * 25
            msg_key = "slots_jackpot"
        elif roll[0] == "💎":
            win = bet * 15
            msg_key = "slots_win"
        else:
            win = bet * 8
            msg_key = "slots_win"
    elif roll[0] == roll[1] or roll[0] == roll[2] or roll[1] == roll[2]:
        win = bet * 2
        msg_key = "slots_win"
        
    if win > 0:
        user["coins"] += win
        user["stats"]["games_won"] = user["stats"].get("games_won", 0) + 1
        user["stats"]["total_won"] = user["stats"].get("total_won", 0) + win
        user["stats"]["total_lost"] = max(0, user["stats"].get("total_lost", 0) - bet)
        
    save_users()
    
    status_msg = get_text(uid, msg_key)
    text = get_text(uid, "slots_result",
                    reels=reels_display,
                    status_msg=status_msg,
                    win=win,
                    coins=user["coins"])
                    
    keyboard = [
        [InlineKeyboardButton(get_text(uid, "game_slots"), callback_data=f"bet_select_slots"),
         InlineKeyboardButton(get_text(uid, "btn_back_games"), callback_data="menu_games")]
    ]
    await edit_msg(query, text, InlineKeyboardMarkup(keyboard))

# 🃏 BLACKJACK HELPERS
def make_deck():
    suits = ['♥️', '♦️', '♣️', '♠️']
    values = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
    deck = [(s, v) for s in suits for v in values]
    random.shuffle(deck)
    return deck

def calc_score(hand):
    score = 0
    aces = 0
    for s, v in hand:
        if v in ['J', 'Q', 'K']:
            score += 10
        elif v == 'A':
            score += 11
            aces += 1
        else:
            score += int(v)
            
    while score > 21 and aces > 0:
        score -= 10
        aces -= 1
    return score

def hand_to_str(hand):
    return " ".join([f"`[{s}{v}]`" for s, v in hand])

async def show_blackjack_turn(query, uid):
    game = ACTIVE_GAMES[uid]
    bet = game['bet']
    player_hand = game['player_hand']
    dealer_hand = game['dealer_hand']
    
    player_score = calc_score(player_hand)
    dealer_display_hand = [dealer_hand[0]]
    dealer_display_score = calc_score(dealer_display_hand)
    
    text = (get_text(uid, "bj_title", bet=bet) + "\n\n" +
            get_text(uid, "bj_player_hand", hand=hand_to_str(player_hand), score=player_score) + "\n" +
            get_text(uid, "bj_dealer_hand", hand=hand_to_str(dealer_display_hand) + " `[?]`", score=dealer_display_score) + "\n\n" +
            get_text(uid, "bj_action"))
            
    keyboard = [[
        InlineKeyboardButton(get_text(uid, "btn_hit"), callback_data="bj_hit"),
        InlineKeyboardButton(get_text(uid, "btn_stand"), callback_data="bj_stand")
    ]]
    await edit_msg(query, text, InlineKeyboardMarkup(keyboard))

async def start_blackjack_game(query, uid, bet):
    user = get_user(uid)
    user["coins"] -= bet
    user["stats"]["games_played"] = user["stats"].get("games_played", 0) + 1
    user["stats"]["total_lost"] = user["stats"].get("total_lost", 0) + bet
    save_users()
    
    deck = make_deck()
    player_hand = [deck.pop(), deck.pop()]
    dealer_hand = [deck.pop(), deck.pop()]
    
    ACTIVE_GAMES[uid] = {
        'game': 'blackjack',
        'bet': bet,
        'player_hand': player_hand,
        'dealer_hand': dealer_hand,
        'deck': deck
    }
    
    player_score = calc_score(player_hand)
    dealer_score = calc_score(dealer_hand)
    
    if player_score == 21:
        if dealer_score == 21:
            user["coins"] += bet
            user["stats"]["total_lost"] = max(0, user["stats"].get("total_lost", 0) - bet)
            save_users()
            text = (get_text(uid, "bj_title", bet=bet) + "\n\n" +
                    get_text(uid, "bj_player_hand", hand=hand_to_str(player_hand), score=21) + "\n" +
                    get_text(uid, "bj_dealer_hand", hand=hand_to_str(dealer_hand), score=21) + "\n\n" +
                    get_text(uid, "bj_push"))
            ACTIVE_GAMES.pop(uid, None)
            keyboard = [[InlineKeyboardButton(get_text(uid, "game_blackjack"), callback_data="bet_select_blackjack"),
                         InlineKeyboardButton(get_text(uid, "btn_back_games"), callback_data="menu_games")]]
            await edit_msg(query, text, InlineKeyboardMarkup(keyboard))
        else:
            win = int(bet * 2.5)
            user["coins"] += win
            user["stats"]["games_won"] = user["stats"].get("games_won", 0) + 1
            user["stats"]["total_won"] = user["stats"].get("total_won", 0) + win
            user["stats"]["total_lost"] = max(0, user["stats"].get("total_lost", 0) - bet)
            save_users()
            text = (get_text(uid, "bj_title", bet=bet) + "\n\n" +
                    get_text(uid, "bj_player_hand", hand=hand_to_str(player_hand), score=21) + "\n" +
                    get_text(uid, "bj_dealer_hand", hand=hand_to_str(dealer_hand), score=dealer_score) + "\n\n" +
                    get_text(uid, "bj_bj", win=win))
            ACTIVE_GAMES.pop(uid, None)
            keyboard = [[InlineKeyboardButton(get_text(uid, "game_blackjack"), callback_data="bet_select_blackjack"),
                         InlineKeyboardButton(get_text(uid, "btn_back_games"), callback_data="menu_games")]]
            await edit_msg(query, text, InlineKeyboardMarkup(keyboard))
        return
        
    await show_blackjack_turn(query, uid)

async def handle_blackjack_hit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    
    if uid not in ACTIVE_GAMES or ACTIVE_GAMES[uid]['game'] != 'blackjack':
        await query.answer("No active Blackjack game!")
        return
        
    game = ACTIVE_GAMES[uid]
    deck = game['deck']
    player_hand = game['player_hand']
    bet = game['bet']
    
    player_hand.append(deck.pop())
    score = calc_score(player_hand)
    
    if score > 21:
        dealer_hand = game['dealer_hand']
        text = (get_text(uid, "bj_title", bet=bet) + "\n\n" +
                get_text(uid, "bj_player_hand", hand=hand_to_str(player_hand), score=score) + "\n" +
                get_text(uid, "bj_dealer_hand", hand=hand_to_str(dealer_hand), score=calc_score(dealer_hand)) + "\n\n" +
                get_text(uid, "bj_bust", bet=bet))
        
        ACTIVE_GAMES.pop(uid, None)
        keyboard = [[InlineKeyboardButton(get_text(uid, "game_blackjack"), callback_data="bet_select_blackjack"),
                     InlineKeyboardButton(get_text(uid, "btn_back_games"), callback_data="menu_games")]]
        await query.answer("Bust!")
        await edit_msg(query, text, InlineKeyboardMarkup(keyboard))
    else:
        await query.answer("Hit!")
        await show_blackjack_turn(query, uid)

async def handle_blackjack_stand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    
    if uid not in ACTIVE_GAMES or ACTIVE_GAMES[uid]['game'] != 'blackjack':
        await query.answer("No active Blackjack game!")
        return
        
    game = ACTIVE_GAMES[uid]
    deck = game['deck']
    player_hand = game['player_hand']
    dealer_hand = game['dealer_hand']
    bet = game['bet']
    
    player_score = calc_score(player_hand)
    
    while calc_score(dealer_hand) < 17:
        dealer_hand.append(deck.pop())
        
    dealer_score = calc_score(dealer_hand)
    user = get_user(uid)
    
    if dealer_score > 21:
        win = bet * 2
        user["coins"] += win
        user["stats"]["games_won"] = user["stats"].get("games_won", 0) + 1
        user["stats"]["total_won"] = user["stats"].get("total_won", 0) + win
        user["stats"]["total_lost"] = max(0, user["stats"].get("total_lost", 0) - bet)
        msg_key = "bj_win"
    elif dealer_score > player_score:
        win = 0
        msg_key = "bj_lose"
    elif dealer_score < player_score:
        win = bet * 2
        user["coins"] += win
        user["stats"]["games_won"] = user["stats"].get("games_won", 0) + 1
        user["stats"]["total_won"] = user["stats"].get("total_won", 0) + win
        user["stats"]["total_lost"] = max(0, user["stats"].get("total_lost", 0) - bet)
        msg_key = "bj_win"
    else:
        win = bet
        user["coins"] += win
        user["stats"]["total_lost"] = max(0, user["stats"].get("total_lost", 0) - bet)
        msg_key = "bj_push"
        
    save_users()
    ACTIVE_GAMES.pop(uid, None)
    
    text = (get_text(uid, "bj_title", bet=bet) + "\n\n" +
            get_text(uid, "bj_player_hand", hand=hand_to_str(player_hand), score=player_score) + "\n" +
            get_text(uid, "bj_dealer_hand", hand=hand_to_str(dealer_hand), score=dealer_score) + "\n\n" +
            get_text(uid, msg_key, win=win, bet=bet))
            
    keyboard = [[InlineKeyboardButton(get_text(uid, "game_blackjack"), callback_data="bet_select_blackjack"),
                 InlineKeyboardButton(get_text(uid, "btn_back_games"), callback_data="menu_games")]]
    await query.answer()
    await edit_msg(query, text, InlineKeyboardMarkup(keyboard))

# 🎡 ROULETTE
async def run_roulette_game(query, uid, bet, choice):
    user = get_user(uid)
    user["coins"] -= bet
    user["stats"]["games_played"] = user["stats"].get("games_played", 0) + 1
    user["stats"]["total_lost"] = user["stats"].get("total_lost", 0) + bet
    save_users()
    
    await edit_msg(query, get_text(uid, "roulette_spinning"))
    await asyncio.sleep(1.5)
    
    num = random.randint(0, 36)
    red_nums = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
    black_nums = {2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35}
    
    is_red = num in red_nums
    is_black = num in black_nums
    is_green = num == 0
    
    color_emoji = "🔴" if is_red else "⚫" if is_black else "🟢"
    color_name_key = "roulette_red" if is_red else "roulette_black" if is_black else "roulette_green"
    color_name = get_text(uid, color_name_key)
    
    won = False
    payout = 0
    
    if choice == 'red' and is_red:
        won = True
        payout = bet * 2
    elif choice == 'black' and is_black:
        won = True
        payout = bet * 2
    elif choice == 'green' and is_green:
        won = True
        payout = bet * 35
    elif choice == 'even' and num != 0 and num % 2 == 0:
        won = True
        payout = bet * 2
    elif choice == 'odd' and num % 2 != 0:
        won = True
        payout = bet * 2
        
    if won:
        user["coins"] += payout
        user["stats"]["games_won"] = user["stats"].get("games_won", 0) + 1
        user["stats"]["total_won"] = user["stats"].get("total_won", 0) + payout
        user["stats"]["total_lost"] = max(0, user["stats"].get("total_lost", 0) - bet)
        status_msg = get_text(uid, "slots_win")
    else:
        status_msg = get_text(uid, "slots_lose")
        
    save_users()
    
    bet_type_name = get_text(uid, f"roulette_{choice}")
    text = get_text(uid, "roulette_result",
                    color_emoji=color_emoji,
                    number=num,
                    color_name=color_name,
                    bet_type=bet_type_name,
                    status_msg=status_msg,
                    win=payout,
                    coins=user["coins"])
                    
    keyboard = [
        [InlineKeyboardButton(get_text(uid, "game_roulette"), callback_data=f"bet_select_roulette"),
         InlineKeyboardButton(get_text(uid, "btn_back_games"), callback_data="menu_games")]
    ]
    await edit_msg(query, text, InlineKeyboardMarkup(keyboard))

# 🪙 COIN FLIP
async def run_coinflip_game(query, uid, bet, choice):
    user = get_user(uid)
    user["coins"] -= bet
    user["stats"]["games_played"] = user["stats"].get("games_played", 0) + 1
    user["stats"]["total_lost"] = user["stats"].get("total_lost", 0) + bet
    save_users()
    
    await edit_msg(query, get_text(uid, "cf_flipping"))
    await asyncio.sleep(1.2)
    
    outcome = 'heads' if random.random() < 0.5 else 'tails'
    result_text = get_text(uid, f"cf_{outcome}")
    bet_type = get_text(uid, f"cf_{choice}")
    
    won = (outcome == choice)
    payout = bet * 2 if won else 0
    
    if won:
        user["coins"] += payout
        user["stats"]["games_won"] = user["stats"].get("games_won", 0) + 1
        user["stats"]["total_won"] = user["stats"].get("total_won", 0) + payout
        user["stats"]["total_lost"] = max(0, user["stats"].get("total_lost", 0) - bet)
        status_msg = get_text(uid, "slots_win")
    else:
        status_msg = get_text(uid, "slots_lose")
        
    save_users()
    
    text = get_text(uid, "cf_result",
                    result_text=result_text,
                    bet_type=bet_type,
                    status_msg=status_msg,
                    win=payout,
                    coins=user["coins"])
                    
    keyboard = [
        [InlineKeyboardButton(get_text(uid, "game_coinflip"), callback_data=f"bet_select_coinflip"),
         InlineKeyboardButton(get_text(uid, "btn_back_games"), callback_data="menu_games")]
    ]
    await edit_msg(query, text, InlineKeyboardMarkup(keyboard))

# 📈 CRASH GAME
async def start_crash_game(query, uid, bet):
    user = get_user(uid)
    user["coins"] -= bet
    user["stats"]["games_played"] = user["stats"].get("games_played", 0) + 1
    user["stats"]["total_lost"] = user["stats"].get("total_lost", 0) + bet
    save_users()
    
    # Calculate crash multiplier
    r = random.random()
    if r < 0.08:
        crash_point = 1.00  # 8% chance of instant crash
    else:
        crash_point = round(1.0 + random.expovariate(0.35), 2)
        if crash_point > 100.0:
            crash_point = 100.0
            
    ACTIVE_GAMES[uid] = {
        'game': 'crash',
        'bet': bet,
        'multiplier': 1.00,
        'crash_point': crash_point,
        'cashed_out': False,
        'message_id': query.message.message_id
    }
    
    await edit_msg(query, get_text(uid, "crash_preparing"))
    await asyncio.sleep(1.5)
    
    current_mult = 1.00
    while current_mult < crash_point:
        if uid not in ACTIVE_GAMES or ACTIVE_GAMES[uid]['cashed_out']:
            return
            
        ACTIVE_GAMES[uid]['multiplier'] = current_mult
        
        potential = bet * current_mult
        text = get_text(uid, "crash_flying", multiplier=current_mult, potential=potential)
        
        btn_text = get_text(uid, "btn_cash_out", multiplier=current_mult)
        keyboard = [[InlineKeyboardButton(btn_text, callback_data="crash_cashout")]]
        
        await edit_msg(query, text, InlineKeyboardMarkup(keyboard))
        
        # Grow multiplier
        await asyncio.sleep(1.2)
        if current_mult < 2.0:
            current_mult = round(current_mult + 0.1, 2)
        elif current_mult < 5.0:
            current_mult = round(current_mult + 0.25, 2)
        elif current_mult < 15.0:
            current_mult = round(current_mult + 1.0, 2)
        else:
            current_mult = round(current_mult + 5.0, 2)
            
    # Rocket crashed
    if uid in ACTIVE_GAMES and not ACTIVE_GAMES[uid]['cashed_out']:
        text = get_text(uid, "crash_crashed", multiplier=crash_point, bet=bet)
        keyboard = [
            [InlineKeyboardButton(get_text(uid, "game_crash"), callback_data="bet_select_crash"),
             InlineKeyboardButton(get_text(uid, "btn_back_games"), callback_data="menu_games")]
        ]
        await edit_msg(query, text, InlineKeyboardMarkup(keyboard))
        ACTIVE_GAMES.pop(uid, None)

async def handle_crash_cashout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    
    if uid not in ACTIVE_GAMES or ACTIVE_GAMES[uid]['game'] != 'crash' or ACTIVE_GAMES[uid]['cashed_out']:
        await query.answer("No active crash game!")
        return
        
    game = ACTIVE_GAMES[uid]
    game['cashed_out'] = True
    
    current_mult = game['multiplier']
    bet = game['bet']
    win = int(bet * current_mult)
    
    user = get_user(uid)
    user["coins"] += win
    user["stats"]["games_won"] = user["stats"].get("games_won", 0) + 1
    user["stats"]["total_won"] = user["stats"].get("total_won", 0) + win
    user["stats"]["total_lost"] = max(0, user["stats"].get("total_lost", 0) - bet)
    save_users()
    
    ACTIVE_GAMES.pop(uid, None)
    
    text = get_text(uid, "crash_cashed", multiplier=current_mult, win=win, coins=user["coins"])
    keyboard = [
        [InlineKeyboardButton(get_text(uid, "game_crash"), callback_data="bet_select_crash"),
         InlineKeyboardButton(get_text(uid, "btn_back_games"), callback_data="menu_games")]
    ]
    await query.answer("Cashed out!")
    await edit_msg(query, text, InlineKeyboardMarkup(keyboard))

# 🎡 WHEEL OF FORTUNE (DAILY BONUS OVERHAUL)
async def run_wheel_fortune(query, uid):
    user = get_user(uid)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    last = user.get("last_daily")
    if last:
        last_dt = datetime.fromisoformat(last)
        if now - last_dt < timedelta(hours=24):
            remaining = timedelta(hours=24) - (now - last_dt)
            hrs = int(remaining.total_seconds() // 3600)
            mins = int((remaining.total_seconds() % 3600) // 60)
            text = get_text(uid, "wheel_cooldown", hrs=hrs, mins=mins)
            keyboard = [[InlineKeyboardButton(get_text(uid, "btn_back"), callback_data="menu_main")]]
            await query.answer("Wheel of fortune on cooldown!", show_alert=True)
            await edit_msg(query, text, InlineKeyboardMarkup(keyboard))
            return
            
    await edit_msg(query, get_text(uid, "wheel_spinning"))
    await asyncio.sleep(1.5)
    
    payouts = [50, 100, 200, 500, 1000, 5000]
    weights = [0.10, 0.25, 0.35, 0.20, 0.08, 0.02]
    
    bonus = random.choices(payouts, weights=weights, k=1)[0]
    
    user["coins"] += bonus
    user["last_daily"] = now.isoformat()
    save_users()
    
    text = get_text(uid, "wheel_result", bonus=bonus, coins=user["coins"])
    keyboard = [[InlineKeyboardButton(get_text(uid, "btn_back"), callback_data="menu_main")]]
    await edit_msg(query, text, InlineKeyboardMarkup(keyboard))

# --- DISPATCHERS ---

async def button_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    first_name = query.from_user.first_name
    
    get_user(uid, first_name)
    data = query.data
    
    # Active games check
    if uid in ACTIVE_GAMES:
        game_type = ACTIVE_GAMES[uid]['game']
        if game_type == 'crash' and data != 'crash_cashout':
            await query.answer("Please finish your Crash game first!", show_alert=True)
            return
        if game_type == 'blackjack' and data not in ['bj_hit', 'bj_stand']:
            await query.answer("Please finish your Blackjack game first!", show_alert=True)
            return

    # Routing callbacks
    if data == "menu_main":
        await query.answer()
        await send_main_menu(query, uid, edit=True)
    elif data == "menu_games":
        await query.answer()
        await send_games_menu(query, uid)
    elif data == "menu_profile":
        await query.answer()
        await send_profile(query, uid)
    elif data == "menu_leaderboard":
        await query.answer()
        await send_leaderboard(query, uid)
    elif data == "menu_lang":
        await query.answer()
        await send_lang_menu(query, uid)
    elif data.startswith("setlang_"):
        await handle_setlang(update, context)
    elif data == "menu_wheel":
        await query.answer()
        await run_wheel_fortune(query, uid)
    elif data.startswith("bet_select_"):
        await query.answer()
        game = data.split("_")[2]
        await send_bet_selection(query, uid, game)
    elif data.startswith("play_"):
        await handle_play_callback(update, context)
    elif data.startswith("choice_"):
        await handle_game_choice_callback(update, context)
    elif data == "bj_hit":
        await handle_blackjack_hit(update, context)
    elif data == "bj_stand":
        await handle_blackjack_stand(update, context)
    elif data == "crash_cashout":
        await handle_crash_cashout(update, context)

async def handle_setlang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    lang = query.data.split("_")[1]
    
    user = get_user(uid)
    user["lang"] = lang
    save_users()
    
    await query.answer(get_text(uid, "lang_set"))
    await send_main_menu(query, uid, edit=True)

async def handle_play_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    user = get_user(uid)
    
    parts = query.data.split("_")
    game = parts[1]
    amount_str = parts[2]
    
    if amount_str == "allin":
        bet = user["coins"]
    else:
        bet = int(amount_str)
        
    if bet <= 0 or bet > user["coins"]:
        await query.answer(get_text(uid, "invalid_bet"), show_alert=True)
        await send_bet_selection(query, uid, game)
        return
        
    await query.answer()
    
    if game == "slots":
        await run_slots_game(query, uid, bet)
    elif game == "blackjack":
        await start_blackjack_game(query, uid, bet)
    elif game == "crash":
        asyncio.create_task(start_crash_game(query, uid, bet))
    elif game == "roulette":
        text = get_text(uid, "roulette_title", bet=bet) + "\n\n" + get_text(uid, "roulette_choice")
        keyboard = [
            [
                InlineKeyboardButton(get_text(uid, "roulette_red") + " 🔴", callback_data=f"choice_roulette_{bet}_red"),
                InlineKeyboardButton(get_text(uid, "roulette_black") + " ⚫", callback_data=f"choice_roulette_{bet}_black"),
            ],
            [
                InlineKeyboardButton(get_text(uid, "roulette_green") + " 🟢", callback_data=f"choice_roulette_{bet}_green"),
            ],
            [
                InlineKeyboardButton(get_text(uid, "roulette_even"), callback_data=f"choice_roulette_{bet}_even"),
                InlineKeyboardButton(get_text(uid, "roulette_odd"), callback_data=f"choice_roulette_{bet}_odd"),
            ],
            [
                InlineKeyboardButton(get_text(uid, "btn_back"), callback_data=f"bet_select_roulette")
            ]
        ]
        await edit_msg(query, text, InlineKeyboardMarkup(keyboard))
    elif game == "coinflip":
        text = get_text(uid, "cf_title", bet=bet) + "\n\n" + get_text(uid, "cf_choice")
        keyboard = [
            [
                InlineKeyboardButton(get_text(uid, "cf_heads") + " 🦅", callback_data=f"choice_cf_{bet}_heads"),
                InlineKeyboardButton(get_text(uid, "cf_tails") + " 🪙", callback_data=f"choice_cf_{bet}_tails"),
            ],
            [
                InlineKeyboardButton(get_text(uid, "btn_back"), callback_data=f"bet_select_coinflip")
            ]
        ]
        await edit_msg(query, text, InlineKeyboardMarkup(keyboard))

async def handle_game_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    user = get_user(uid)
    
    parts = query.data.split("_")
    game = parts[1]
    bet_str = parts[2]
    choice_val = parts[3]
    
    if bet_str == "allin":
        bet = user["coins"]
    else:
        bet = int(bet_str)
        
    if bet <= 0 or bet > user["coins"]:
        await query.answer(get_text(uid, "invalid_bet"), show_alert=True)
        await send_bet_selection(query, uid, game)
        return
        
    await query.answer()
    
    if game == "roulette":
        await run_roulette_game(query, uid, bet, choice_val)
    elif game == "cf":
        await run_coinflip_game(query, uid, bet, choice_val)

# --- COMMAND HANDLERS (LEGACY SUPPORT & SHORTCUTS) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    first_name = update.effective_user.first_name
    get_user(uid, first_name)
    ACTIVE_GAMES.pop(uid, None)
    await send_main_menu(update, uid, edit=False)

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    first_name = update.effective_user.first_name
    get_user(uid, first_name)
    user = get_user(uid)
    text = get_text(uid, "balance", coins=user["coins"])
    keyboard = [[InlineKeyboardButton(get_text(uid, "btn_back"), callback_data="menu_main")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def daily_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    first_name = update.effective_user.first_name
    get_user(uid, first_name)
    text = get_text(uid, "wheel_title")
    keyboard = [[InlineKeyboardButton("🎡 Spin / Крутить", callback_data="menu_wheel")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def slots_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    first_name = update.effective_user.first_name
    get_user(uid, first_name)
    user = get_user(uid)
    text = get_text(uid, "choose_bet", game=get_text(uid, "game_slots"), coins=user["coins"])
    keyboard = [
        [
            InlineKeyboardButton("💵 10", callback_data="play_slots_10"),
            InlineKeyboardButton("💵 50", callback_data="play_slots_50"),
            InlineKeyboardButton("💵 100", callback_data="play_slots_100"),
        ],
        [
            InlineKeyboardButton("💵 250", callback_data="play_slots_250"),
            InlineKeyboardButton("💵 500", callback_data="play_slots_500"),
            InlineKeyboardButton("💵 1000", callback_data="play_slots_1000"),
        ],
        [
            InlineKeyboardButton(get_text(uid, "btn_all_in"), callback_data="play_slots_allin"),
        ],
        [
            InlineKeyboardButton(get_text(uid, "btn_back_games"), callback_data="menu_games")
        ]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    first_name = update.effective_user.first_name
    get_user(uid, first_name)
    top = get_leaderboard_data()
    
    lines = [get_text(uid, "menu_leaderboard"), ""]
    for i, (userid, data) in enumerate(top, 1):
        xp = data.get("stats", {}).get("total_won", 0) + data.get("stats", {}).get("total_lost", 0)
        level = int(xp // 1000) + 1
        name = data.get("first_name", f"Player {userid[:6]}")
        lines.append(get_text(uid, "leaderboard_row", index=i, name=name, coins=data.get("coins", 0), level=level))
        
    keyboard = [[InlineKeyboardButton(get_text(uid, "btn_back"), callback_data="menu_main")]]
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    first_name = update.effective_user.first_name
    get_user(uid, first_name)
    text = get_text(uid, "menu_lang")
    keyboard = [[
        InlineKeyboardButton("🇷🇺 Русский", callback_data="setlang_ru"),
        InlineKeyboardButton("🇺🇸 English", callback_data="setlang_en")
    ], [
        InlineKeyboardButton(get_text(uid, "btn_back"), callback_data="menu_main")
    ]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# --- MAIN ---

def main() -> None:
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("daily", daily_cmd))
    app.add_handler(CommandHandler("slot", slots_cmd))
    app.add_handler(CommandHandler("slots", slots_cmd))
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
    app.add_handler(CommandHandler("lang", lang_cmd))
    
    # Callback queries dispatcher
    app.add_handler(CallbackQueryHandler(button_dispatcher))
    
    logger.info("Starting bot polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
