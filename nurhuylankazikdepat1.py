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
from telegram.constants import ParseMode

# Load environment variables
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8606720265:AAGDZJhk_uW7RT3SdTBzRXuuz93eD7RU65Q")
DATABASE_URL = os.getenv("DATABASE_URL")
# Convert Railway postgres URL to psycopg2-compatible format
if DATABASE_URL and (DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")):
    DB_URL = DATABASE_URL.replace("postgres://", "postgresql://")
    # Ensure SSL mode
    if "sslmode" not in DB_URL:
        sep = "?" if "?" not in DB_URL else "&"
        DB_URL = f"{DB_URL}{sep}sslmode=require"
else:
    DB_URL = DATABASE_URL

# Paths
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"

def init_db():
    if not DATABASE_URL:
        logger.info("No DATABASE_URL found. Using local JSON database.")
        return False
    try:
        import psycopg2
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id VARCHAR(50) PRIMARY KEY,
                coins BIGINT DEFAULT 1000,
                last_daily VARCHAR(100),
                lang VARCHAR(10) DEFAULT 'ru',
                first_name VARCHAR(255),
                stats JSONB DEFAULT '{"games_played":0,"games_won":0,"total_won":0,"total_lost":0}'::jsonb,
                registered_at VARCHAR(100)
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        logger.info("PostgreSQL database initialized successfully.")
        return True
    except Exception as e:
        logger.error(f"PostgreSQL connection failed: {e}. Falling back to JSON fallback.")
        return False

IS_POSTGRES = init_db()

users = {}
if not IS_POSTGRES and USERS_FILE.exists():
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
    except Exception as e:
        logger.error(f"Error loading users.json: {e}")

INITIAL_COINS = 1000
ACTIVE_GAMES = {}

# NOTE: All text uses HTML tags for safe formatting (no Markdown special chars issues)
LANGUAGES = {
    "ru": {
        "welcome": "🎰 <b>Добро пожаловать в TG Casino, {name}!</b> 🎰\n\n💵 Ваш баланс: <b>{coins} монет</b>\n🏆 Уровень: <b>{level}</b>\n\nВыберите действие в меню ниже:",
        "menu_games": "🎮 <b>Выберите игру для начала:</b>",
        "menu_profile": "👤 <b>Ваш профиль:</b>\n\nID: <code>{uid}</code>\n💵 Баланс: <b>{coins} монет</b>\n🏆 Уровень: <b>{level}</b> ({exp} опыта)\n\n📊 <b>Статистика:</b>\n🎮 Всего игр: {games_played}\n✅ Побед: {games_won} ({win_rate}%)\n📈 Всего выиграно: {total_won} монет\n📉 Всего проиграно: {total_lost} монет",
        "menu_leaderboard": "🏆 <b>Топ 10 богатейших игроков:</b>",
        "leaderboard_row": "{index}. {name} — <b>{coins} монет</b> (Ур. {level})",
        "menu_lang": "🌐 <b>Выберите язык / Select language:</b>",
        "lang_set": "Язык изменен на Русский! 🇷🇺",
        "choose_bet": "💰 Выберите размер ставки для игры <b>{game}</b>:\n💵 Ваш баланс: <b>{coins} монет</b>",
        "invalid_bet": "❌ Недостаточно монет или неверная ставка!",
        "slots_spinning": "🎰 <b>СЛОТЫ КРУТЯТСЯ...</b> 🎰\n\n[ 🔄 | 🔄 | 🔄 ]",
        "slots_result": "🎰 <b>Результат Слотов:</b> 🎰\n\n{reels}\n\n{status_msg}\n💵 Выигрыш: <b>{win} монет</b>\n💰 Ваш баланс: <b>{coins} монет</b>",
        "slots_jackpot": "🎉 <b>ДЖЕКПОТ! Поздравляем!</b> 🎉",
        "slots_win": "🎉 <b>Вы выиграли!</b>",
        "slots_lose": "😢 <b>Вы проиграли.</b>",
        "bj_title": "🃏 <b>Блэкджек (Ставка: {bet})</b> 🃏",
        "bj_player_hand": "👤 <b>Ваша рука:</b> {hand} <b>(Счет: {score})</b>",
        "bj_dealer_hand": "🤖 <b>Рука дилера:</b> {hand} <b>(Счет: {score})</b>",
        "bj_win": "🎉 <b>Вы выиграли!</b> +{win} монет.",
        "bj_bj": "🔥 <b>БЛЭКДЖЕК!</b> Вы выиграли {win} монет!",
        "bj_lose": "😢 <b>Вы проиграли.</b> Дилер выиграл.",
        "bj_bust": "💥 <b>Перебор!</b> Вы проиграли {bet} монет.",
        "bj_push": "🤝 <b>Ничья (Push).</b> Ставка возвращена.",
        "bj_action": "Выберите ход:",
        "roulette_title": "🎡 <b>Рулетка (Ставка: {bet})</b> 🎡",
        "roulette_choice": "Выберите тип ставки:",
        "roulette_spinning": "🎡 <b>Шарик катится...</b> 🎡\n\n🟢 [0] ... 🔴 [14] ... ⚫ [32] ...",
        "roulette_result": "🎡 <b>Результат Рулетки:</b> 🎡\n\nВыпало: {color_emoji} <b>{number} {color_name}</b>\nВаша ставка: {bet_type}\n\n{status_msg}\n💵 Выигрыш: <b>{win} монет</b>\n💰 Ваш баланс: <b>{coins} монет</b>",
        "roulette_red": "Красное",
        "roulette_black": "Черное",
        "roulette_green": "Зеленое (0)",
        "roulette_even": "Четное",
        "roulette_odd": "Нечетное",
        "cf_title": "🪙 <b>Монетка (Ставка: {bet})</b> 🪙",
        "cf_choice": "На что ставите?",
        "cf_heads": "🦅 Орел",
        "cf_tails": "🪙 Решка",
        "cf_flipping": "🪙 <b>Монетка подброшена...</b> 🪙\n\n🔄 крутится в воздухе...",
        "cf_result": "🪙 <b>Результат броска:</b> 🪙\n\nВыпало: <b>{result_text}</b>\nВаша ставка: <b>{bet_type}</b>\n\n{status_msg}\n💵 Выигрыш: <b>{win} монет</b>\n💰 Ваш баланс: <b>{coins} монет</b>",
        "crash_preparing": "🚀 <b>Ракета готовится к старту...</b>",
        "crash_flying": "🚀 <b>Ракета летит!</b>\n\n📈 Текущий множитель: <b>{multiplier:.2f}x</b>\n💰 Возможный выигрыш: <b>{potential:.0f} монет</b>",
        "crash_crashed": "💥 <b>БАБАХ!</b> Ракета взорвалась на <b>{multiplier:.2f}x</b>!\n😢 Вы потеряли <b>{bet} монет</b>.",
        "crash_cashed": "🎉 <b>УСПЕШНЫЙ ВЫВОД!</b> 🎉\nВы забрали выигрыш на <b>{multiplier:.2f}x</b>!\n💵 Выиграно: <b>{win:.0f} монет</b>\n💰 Баланс: <b>{coins} монет</b>",
        "wheel_title": "🎁 <b>Колесо Фортуны</b> 🎁",
        "wheel_cooldown": "⏳ Вы уже крутили колесо сегодня!\nСледующая попытка через: <b>{hrs}ч {mins}м</b>",
        "wheel_spinning": "🎡 <b>Колесо Фортуны вращается...</b> 🎡\n\n💎 5000 ... 💵 100 ... 💎 1000 ...",
        "wheel_result": "🎡 <b>Колесо Фортуны</b> 🎡\n\n🎉 Вы выиграли: <b>{bonus} монет</b>!\n💰 Баланс: <b>{coins} монет</b>",
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
        "btn_hit": "🃏 Еще карту",
        "btn_stand": "🛑 Хватит",
        "btn_cash_out": "🚀 Забрать {multiplier:.2f}x",
    },
    "en": {
        "welcome": "🎰 <b>Welcome to TG Casino, {name}!</b> 🎰\n\n💵 Your balance: <b>{coins} coins</b>\n🏆 Level: <b>{level}</b>\n\nSelect an action from the menu below:",
        "menu_games": "🎮 <b>Select a game to start playing:</b>",
        "menu_profile": "👤 <b>Your Profile:</b>\n\nID: <code>{uid}</code>\n💵 Balance: <b>{coins} coins</b>\n🏆 Level: <b>{level}</b> ({exp} xp)\n\n📊 <b>Statistics:</b>\n🎮 Total Games: {games_played}\n✅ Wins: {games_won} ({win_rate}%)\n📈 Total Won: {total_won} coins\n📉 Total Lost: {total_lost} coins",
        "menu_leaderboard": "🏆 <b>Top 10 Richest Players:</b>",
        "leaderboard_row": "{index}. {name} — <b>{coins} coins</b> (Lvl {level})",
        "menu_lang": "🌐 <b>Select language / Выберите язык:</b>",
        "lang_set": "Language set to English! 🇺🇸",
        "choose_bet": "💰 Select bet amount for <b>{game}</b>:\n💵 Your balance: <b>{coins} coins</b>",
        "invalid_bet": "❌ Not enough coins or invalid bet size!",
        "slots_spinning": "🎰 <b>SLOTS SPINNING...</b> 🎰\n\n[ 🔄 | 🔄 | 🔄 ]",
        "slots_result": "🎰 <b>Slots Result:</b> 🎰\n\n{reels}\n\n{status_msg}\n💵 Payout: <b>{win} coins</b>\n💰 Your balance: <b>{coins} coins</b>",
        "slots_jackpot": "🎉 <b>JACKPOT! Congratulations!</b> 🎉",
        "slots_win": "🎉 <b>You won!</b>",
        "slots_lose": "😢 <b>You lost.</b>",
        "bj_title": "🃏 <b>Blackjack (Bet: {bet})</b> 🃏",
        "bj_player_hand": "👤 <b>Your Hand:</b> {hand} <b>(Score: {score})</b>",
        "bj_dealer_hand": "🤖 <b>Dealer's Hand:</b> {hand} <b>(Score: {score})</b>",
        "bj_win": "🎉 <b>You won!</b> +{win} coins.",
        "bj_bj": "🔥 <b>BLACKJACK!</b> You won {win} coins!",
        "bj_lose": "😢 <b>You lost.</b> Dealer wins.",
        "bj_bust": "💥 <b>Bust!</b> You lost {bet} coins.",
        "bj_push": "🤝 <b>Push (Draw).</b> Bet returned.",
        "bj_action": "Choose your move:",
        "roulette_title": "🎡 <b>Roulette (Bet: {bet})</b> 🎡",
        "roulette_choice": "Select bet type:",
        "roulette_spinning": "🎡 <b>Roulette is spinning...</b> 🎡\n\n🟢 [0] ... 🔴 [14] ... ⚫ [32] ...",
        "roulette_result": "🎡 <b>Roulette Result:</b> 🎡\n\nRolled: {color_emoji} <b>{number} {color_name}</b>\nYour bet: {bet_type}\n\n{status_msg}\n💵 Payout: <b>{win} coins</b>\n💰 Your balance: <b>{coins} coins</b>",
        "roulette_red": "Red",
        "roulette_black": "Black",
        "roulette_green": "Green (0)",
        "roulette_even": "Even",
        "roulette_odd": "Odd",
        "cf_title": "🪙 <b>Coin Flip (Bet: {bet})</b> 🪙",
        "cf_choice": "What is your call?",
        "cf_heads": "🦅 Heads",
        "cf_tails": "🪙 Tails",
        "cf_flipping": "🪙 <b>Flipping coin...</b> 🪙\n\n🔄 spinning in midair...",
        "cf_result": "🪙 <b>Coin Flip Result:</b> 🪙\n\nResult: <b>{result_text}</b>\nYour bet: <b>{bet_type}</b>\n\n{status_msg}\n💵 Payout: <b>{win} coins</b>\n💰 Your balance: <b>{coins} coins</b>",
        "crash_preparing": "🚀 <b>Rocket is preparing for liftoff...</b>",
        "crash_flying": "🚀 <b>Rocket is flying!</b>\n\n📈 Current multiplier: <b>{multiplier:.2f}x</b>\n💰 Potential payout: <b>{potential:.0f} coins</b>",
        "crash_crashed": "💥 <b>BOOM!</b> Rocket crashed at <b>{multiplier:.2f}x</b>!\n😢 You lost <b>{bet} coins</b>.",
        "crash_cashed": "🎉 <b>CASHED OUT!</b> 🎉\nYou cashed out at <b>{multiplier:.2f}x</b>!\n💵 Won: <b>{win:.0f} coins</b>\n💰 Balance: <b>{coins} coins</b>",
        "wheel_title": "🎁 <b>Wheel of Fortune</b> 🎁",
        "wheel_cooldown": "⏳ You already spun the wheel today!\nNext spin in: <b>{hrs}h {mins}m</b>",
        "wheel_spinning": "🎡 <b>Wheel is spinning...</b> 🎡\n\n💎 5000 ... 💵 100 ... 💎 1000 ...",
        "wheel_result": "🎡 <b>Wheel of Fortune</b> 🎡\n\n🎉 You won: <b>{bonus} coins</b>!\n💰 Balance: <b>{coins} coins</b>",
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

# ────────────────────────────────────────────────
#  DATABASE HELPERS
# ────────────────────────────────────────────────

def save_users(user_id: str = None):
    if not IS_POSTGRES:
        try:
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump(users, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving users.json: {e}")
    else:
        try:
            import psycopg2
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            # Determine which users to sync: only provided user_id or all users
            if user_id and user_id in users:
                user_items = [(user_id, users[user_id])]
            else:
                user_items = list(users.items())
            for uid, data in user_items:
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
                    (uid, data["coins"], data["last_daily"], data["lang"],
                     data["first_name"], json.dumps(data["stats"]), data.get("registered_at"))
                )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"Error syncing to Postgres: {e}")


def _default_user(user_id, first_name):
    return {
        "coins": INITIAL_COINS,
        "last_daily": None,
        "lang": "ru",
        "first_name": first_name or f"Player {user_id[:6]}",
        "stats": {"games_played": 0, "games_won": 0, "total_won": 0, "total_lost": 0},
        "registered_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    }


def get_user(user_id: str, first_name: str = None):
    if user_id not in users:
        if IS_POSTGRES:
            try:
                import psycopg2
                conn = psycopg2.connect(DB_URL)
                cur = conn.cursor()
                cur.execute(
                    "SELECT coins, last_daily, lang, first_name, stats, registered_at FROM users WHERE user_id = %s",
                    (user_id,)
                )
                row = cur.fetchone()
                cur.close()
                conn.close()
                if row:
                    coins, last_daily, lang, db_fname, stats, registered_at = row
                    if isinstance(stats, str):
                        stats = json.loads(stats)
                    users[user_id] = {
                        "coins": coins, "last_daily": last_daily, "lang": lang,
                        "first_name": db_fname, "stats": stats, "registered_at": registered_at
                    }
                else:
                    users[user_id] = _default_user(user_id, first_name)
                    save_users()
            except Exception as e:
                logger.error(f"Error getting user from Postgres: {e}")
                users[user_id] = _default_user(user_id, first_name)
        else:
            users[user_id] = _default_user(user_id, first_name)
            save_users()

    user = users[user_id]
    changed = False
    if first_name and user.get("first_name") != first_name:
        user["first_name"] = first_name
        changed = True
    if "stats" not in user:
        user["stats"] = {"games_played": 0, "games_won": 0, "total_won": 0, "total_lost": 0}
        changed = True
    if "lang" not in user or user["lang"] not in ("ru", "en"):
        user["lang"] = "ru"
        changed = True
    if changed:
        save_users()
    return user


def get_leaderboard_data(limit=10):
    if not IS_POSTGRES:
        top = sorted(users.items(), key=lambda kv: kv[1].get("coins", 0), reverse=True)[:limit]
        return list(top)
    try:
        import psycopg2
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, coins, last_daily, lang, first_name, stats, registered_at "
            "FROM users ORDER BY coins DESC LIMIT %s", (limit,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        result = []
        for uid, coins, last_daily, lang, fname, stats, registered_at in rows:
            if isinstance(stats, str):
                stats = json.loads(stats)
            result.append((uid, {"coins": coins, "last_daily": last_daily, "lang": lang,
                                  "first_name": fname, "stats": stats, "registered_at": registered_at}))
        return result
    except Exception as e:
        logger.error(f"Error fetching leaderboard: {e}")
        top = sorted(users.items(), key=lambda kv: kv[1].get("coins", 0), reverse=True)[:limit]
        return list(top)


def get_text(user_id: str, key: str, **kwargs):
    lang = get_user(user_id).get("lang", "ru")
    return LANGUAGES[lang][key].format(**kwargs)


# ────────────────────────────────────────────────
#  MESSAGE HELPERS  (HTML mode — no Markdown issues)
# ────────────────────────────────────────────────

async def edit_msg(query, text: str, reply_markup=None):
    """Edit message with HTML parse mode. Logs real errors instead of silently swallowing."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"edit_msg error (non-fatal): {e}")


async def reply_html(msg, text: str, reply_markup=None):
    await msg.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


# ────────────────────────────────────────────────
#  KEYBOARD BUILDERS
# ────────────────────────────────────────────────

def main_menu_kbd(uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text(uid, "btn_games"), callback_data="menu_games")],
        [InlineKeyboardButton(get_text(uid, "btn_profile"), callback_data="menu_profile"),
         InlineKeyboardButton(get_text(uid, "btn_wheel"), callback_data="menu_wheel")],
        [InlineKeyboardButton(get_text(uid, "btn_leaderboard"), callback_data="menu_leaderboard"),
         InlineKeyboardButton(get_text(uid, "btn_lang"), callback_data="menu_lang")],
    ])


def games_menu_kbd(uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text(uid, "game_slots"), callback_data="bet_select_slots"),
         InlineKeyboardButton(get_text(uid, "game_blackjack"), callback_data="bet_select_blackjack")],
        [InlineKeyboardButton(get_text(uid, "game_roulette"), callback_data="bet_select_roulette"),
         InlineKeyboardButton(get_text(uid, "game_coinflip"), callback_data="bet_select_coinflip")],
        [InlineKeyboardButton(get_text(uid, "game_crash"), callback_data="bet_select_crash")],
        [InlineKeyboardButton(get_text(uid, "btn_back"), callback_data="menu_main")],
    ])


def bet_kbd(uid, game):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 10", callback_data=f"play_{game}_10"),
         InlineKeyboardButton("💵 50", callback_data=f"play_{game}_50"),
         InlineKeyboardButton("💵 100", callback_data=f"play_{game}_100")],
        [InlineKeyboardButton("💵 250", callback_data=f"play_{game}_250"),
         InlineKeyboardButton("💵 500", callback_data=f"play_{game}_500"),
         InlineKeyboardButton("💵 1000", callback_data=f"play_{game}_1000")],
        [InlineKeyboardButton(get_text(uid, "btn_all_in"), callback_data=f"play_{game}_allin")],
        [InlineKeyboardButton(get_text(uid, "btn_back_games"), callback_data="menu_games")],
    ])


def back_kbd(uid, back_cb="menu_main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(get_text(uid, "btn_back"), callback_data=back_cb)]])


def play_again_kbd(uid, game, back_cb="menu_games"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text(uid, f"game_{game}"), callback_data=f"bet_select_{game}"),
         InlineKeyboardButton(get_text(uid, "btn_back_games"), callback_data=back_cb)],
    ])


# ────────────────────────────────────────────────
#  VIEWS
# ────────────────────────────────────────────────

async def view_main_menu(target, uid, edit=False):
    user = get_user(uid)
    xp = user["stats"].get("total_won", 0) + user["stats"].get("total_lost", 0)
    level = max(1, int(xp // 1000) + 1)
    first_name = user.get("first_name", "")
    text = get_text(uid, "welcome", name=first_name, coins=user["coins"], level=level)
    kbd = main_menu_kbd(uid)
    if edit:
        await edit_msg(target, text, kbd)
    else:
        await reply_html(target, text, kbd)


async def view_games_menu(query, uid):
    await edit_msg(query, get_text(uid, "menu_games"), games_menu_kbd(uid))


async def view_profile(query, uid):
    user = get_user(uid)
    xp = user["stats"].get("total_won", 0) + user["stats"].get("total_lost", 0)
    level = max(1, int(xp // 1000) + 1)
    gp = user["stats"].get("games_played", 0)
    gw = user["stats"].get("games_won", 0)
    wr = round(gw / gp * 100, 1) if gp > 0 else 0.0
    text = get_text(uid, "menu_profile",
                    uid=uid, coins=user["coins"], level=level, exp=xp,
                    games_played=gp, games_won=gw, win_rate=wr,
                    total_won=user["stats"].get("total_won", 0),
                    total_lost=user["stats"].get("total_lost", 0))
    await edit_msg(query, text, back_kbd(uid))


async def view_leaderboard(query, uid):
    top = get_leaderboard_data()
    lines = [get_text(uid, "menu_leaderboard"), ""]
    for i, (userid, data) in enumerate(top, 1):
        xp = data.get("stats", {}).get("total_won", 0) + data.get("stats", {}).get("total_lost", 0)
        level = max(1, int(xp // 1000) + 1)
        name = data.get("first_name") or f"Player {userid[:6]}"
        lines.append(get_text(uid, "leaderboard_row",
                               index=i, name=name, coins=data.get("coins", 0), level=level))
    await edit_msg(query, "\n".join(lines), back_kbd(uid))


async def view_lang_menu(query, uid):
    text = get_text(uid, "menu_lang")
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="setlang_ru"),
         InlineKeyboardButton("🇺🇸 English", callback_data="setlang_en")],
        [InlineKeyboardButton(get_text(uid, "btn_back"), callback_data="menu_main")],
    ])
    await edit_msg(query, text, kbd)


async def view_bet_select(query, uid, game):
    user = get_user(uid)
    text = get_text(uid, "choose_bet", game=get_text(uid, f"game_{game}"), coins=user["coins"])
    await edit_msg(query, text, bet_kbd(uid, game))


# ────────────────────────────────────────────────
#  GAME: SLOTS
# ────────────────────────────────────────────────

async def play_slots(query, uid, bet):
    user = get_user(uid)
    user["coins"] -= bet
    user["stats"]["games_played"] = user["stats"].get("games_played", 0) + 1
    save_users()

    await edit_msg(query, get_text(uid, "slots_spinning"))
    await asyncio.sleep(1.2)

    symbols = ["🍒", "🍋", "🍇", "🔔", "💎", "7️⃣"]
    weights = [0.28, 0.25, 0.20, 0.13, 0.09, 0.05]
    roll = random.choices(symbols, weights=weights, k=3)
    reels_display = f"[ {roll[0]} | {roll[1]} | {roll[2]} ]"

    if roll[0] == roll[1] == roll[2]:
        if roll[0] == "7️⃣":
            win, key = bet * 25, "slots_jackpot"
        elif roll[0] == "💎":
            win, key = bet * 15, "slots_win"
        else:
            win, key = bet * 8, "slots_win"
    elif roll[0] == roll[1] or roll[0] == roll[2] or roll[1] == roll[2]:
        win, key = bet * 2, "slots_win"
    else:
        win, key = 0, "slots_lose"

    if win > 0:
        user["coins"] += win
        user["stats"]["games_won"] = user["stats"].get("games_won", 0) + 1
        user["stats"]["total_won"] = user["stats"].get("total_won", 0) + win
    else:
        user["stats"]["total_lost"] = user["stats"].get("total_lost", 0) + bet
    save_users()

    text = get_text(uid, "slots_result",
                    reels=reels_display, status_msg=get_text(uid, key),
                    win=win, coins=user["coins"])
    await edit_msg(query, text, play_again_kbd(uid, "slots"))


# ────────────────────────────────────────────────
#  GAME: BLACKJACK
# ────────────────────────────────────────────────

def _new_deck():
    suits = ['♥', '♦', '♣', '♠']
    values = ['2','3','4','5','6','7','8','9','10','J','Q','K','A']
    deck = [(s, v) for s in suits for v in values]
    random.shuffle(deck)
    return deck


def _score(hand):
    total, aces = 0, 0
    for _, v in hand:
        if v in ('J', 'Q', 'K'):
            total += 10
        elif v == 'A':
            total += 11; aces += 1
        else:
            total += int(v)
    while total > 21 and aces:
        total -= 10; aces -= 1
    return total


def _hand_str(hand):
    return " ".join(f"[{s}{v}]" for s, v in hand)


async def _show_bj(query, uid, show_dealer_hidden=False):
    game = ACTIVE_GAMES[uid]
    ph = game["player_hand"]
    dh = game["dealer_hand"]
    ps = _score(ph)
    if show_dealer_hidden:
        ds = _score(dh)
        dh_str = _hand_str(dh)
    else:
        ds = _score([dh[0]])
        dh_str = _hand_str([dh[0]]) + " [?]"

    text = (get_text(uid, "bj_title", bet=game["bet"]) + "\n\n" +
            get_text(uid, "bj_player_hand", hand=_hand_str(ph), score=ps) + "\n" +
            get_text(uid, "bj_dealer_hand", hand=dh_str, score=ds) + "\n\n" +
            get_text(uid, "bj_action"))
    kbd = InlineKeyboardMarkup([[
        InlineKeyboardButton(get_text(uid, "btn_hit"), callback_data="bj_hit"),
        InlineKeyboardButton(get_text(uid, "btn_stand"), callback_data="bj_stand"),
    ]])
    await edit_msg(query, text, kbd)


async def play_blackjack(query, uid, bet):
    user = get_user(uid)
    user["coins"] -= bet
    user["stats"]["games_played"] = user["stats"].get("games_played", 0) + 1
    save_users()

    deck = _new_deck()
    ph = [deck.pop(), deck.pop()]
    dh = [deck.pop(), deck.pop()]
    ACTIVE_GAMES[uid] = {"game": "blackjack", "bet": bet, "player_hand": ph, "dealer_hand": dh, "deck": deck}

    ps = _score(ph)
    ds = _score(dh)

    # Instant Blackjack
    if ps == 21:
        if ds == 21:
            user["coins"] += bet
            result_text = get_text(uid, "bj_push")
        else:
            win = int(bet * 2.5)
            user["coins"] += win
            user["stats"]["games_won"] = user["stats"].get("games_won", 0) + 1
            user["stats"]["total_won"] = user["stats"].get("total_won", 0) + win
            result_text = get_text(uid, "bj_bj", win=win)
        save_users()
        ACTIVE_GAMES.pop(uid, None)
        text = (get_text(uid, "bj_title", bet=bet) + "\n\n" +
                get_text(uid, "bj_player_hand", hand=_hand_str(ph), score=ps) + "\n" +
                get_text(uid, "bj_dealer_hand", hand=_hand_str(dh), score=ds) + "\n\n" +
                result_text)
        await edit_msg(query, text, play_again_kbd(uid, "blackjack"))
        return

    await _show_bj(query, uid)


async def bj_hit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    await query.answer()

    if uid not in ACTIVE_GAMES or ACTIVE_GAMES[uid].get("game") != "blackjack":
        return

    game = ACTIVE_GAMES[uid]
    game["player_hand"].append(game["deck"].pop())
    ps = _score(game["player_hand"])

    if ps > 21:
        user = get_user(uid)
        user["stats"]["total_lost"] = user["stats"].get("total_lost", 0) + game["bet"]
        save_users()
        text = (get_text(uid, "bj_title", bet=game["bet"]) + "\n\n" +
                get_text(uid, "bj_player_hand", hand=_hand_str(game["player_hand"]), score=ps) + "\n\n" +
                get_text(uid, "bj_bust", bet=game["bet"]))
        ACTIVE_GAMES.pop(uid, None)
        await edit_msg(query, text, play_again_kbd(uid, "blackjack"))
    else:
        await _show_bj(query, uid)


async def bj_stand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    await query.answer()

    if uid not in ACTIVE_GAMES or ACTIVE_GAMES[uid].get("game") != "blackjack":
        return

    game = ACTIVE_GAMES[uid]
    deck, dh, ph = game["deck"], game["dealer_hand"], game["player_hand"]
    bet = game["bet"]

    while _score(dh) < 17:
        dh.append(deck.pop())

    ps, ds = _score(ph), _score(dh)
    user = get_user(uid)

    if ds > 21 or ps > ds:
        win = bet * 2
        user["coins"] += win
        user["stats"]["games_won"] = user["stats"].get("games_won", 0) + 1
        user["stats"]["total_won"] = user["stats"].get("total_won", 0) + win
        msg_key, extra = "bj_win", {"win": win}
    elif ps < ds:
        user["stats"]["total_lost"] = user["stats"].get("total_lost", 0) + bet
        msg_key, extra = "bj_lose", {}
    else:
        user["coins"] += bet
        msg_key, extra = "bj_push", {}

    save_users()
    ACTIVE_GAMES.pop(uid, None)

    text = (get_text(uid, "bj_title", bet=bet) + "\n\n" +
            get_text(uid, "bj_player_hand", hand=_hand_str(ph), score=ps) + "\n" +
            get_text(uid, "bj_dealer_hand", hand=_hand_str(dh), score=ds) + "\n\n" +
            get_text(uid, msg_key, bet=bet, win=extra.get("win", 0)))
    await edit_msg(query, text, play_again_kbd(uid, "blackjack"))


# ────────────────────────────────────────────────
#  GAME: ROULETTE
# ────────────────────────────────────────────────

async def play_roulette(query, uid, bet, choice):
    user = get_user(uid)
    user["coins"] -= bet
    user["stats"]["games_played"] = user["stats"].get("games_played", 0) + 1
    save_users()

    await edit_msg(query, get_text(uid, "roulette_spinning"))
    await asyncio.sleep(1.5)

    num = random.randint(0, 36)
    RED = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
    BLACK = {2,4,6,8,10,11,13,15,17,20,22,24,26,28,29,31,33,35}
    is_red, is_black, is_green = num in RED, num in BLACK, num == 0

    color_emoji = "🔴" if is_red else "⚫" if is_black else "🟢"
    color_name_key = "roulette_red" if is_red else "roulette_black" if is_black else "roulette_green"

    won = ((choice == "red" and is_red) or
           (choice == "black" and is_black) or
           (choice == "green" and is_green) or
           (choice == "even" and num != 0 and num % 2 == 0) or
           (choice == "odd" and num % 2 != 0))
    payout = (bet * 35 if choice == "green" else bet * 2) if won else 0

    if won:
        user["coins"] += payout
        user["stats"]["games_won"] = user["stats"].get("games_won", 0) + 1
        user["stats"]["total_won"] = user["stats"].get("total_won", 0) + payout
        status = get_text(uid, "slots_win")
    else:
        user["stats"]["total_lost"] = user["stats"].get("total_lost", 0) + bet
        status = get_text(uid, "slots_lose")
    save_users()

    text = get_text(uid, "roulette_result",
                    color_emoji=color_emoji, number=num,
                    color_name=get_text(uid, color_name_key),
                    bet_type=get_text(uid, f"roulette_{choice}"),
                    status_msg=status, win=payout, coins=user["coins"])
    await edit_msg(query, text, play_again_kbd(uid, "roulette"))


# ────────────────────────────────────────────────
#  GAME: COIN FLIP
# ────────────────────────────────────────────────

async def play_coinflip(query, uid, bet, choice):
    user = get_user(uid)
    user["coins"] -= bet
    user["stats"]["games_played"] = user["stats"].get("games_played", 0) + 1
    save_users()

    await edit_msg(query, get_text(uid, "cf_flipping"))
    await asyncio.sleep(1.2)

    outcome = "heads" if random.random() < 0.5 else "tails"
    won = (outcome == choice)
    payout = bet * 2 if won else 0

    if won:
        user["coins"] += payout
        user["stats"]["games_won"] = user["stats"].get("games_won", 0) + 1
        user["stats"]["total_won"] = user["stats"].get("total_won", 0) + payout
        status = get_text(uid, "slots_win")
    else:
        user["stats"]["total_lost"] = user["stats"].get("total_lost", 0) + bet
        status = get_text(uid, "slots_lose")
    save_users()

    text = get_text(uid, "cf_result",
                    result_text=get_text(uid, f"cf_{outcome}"),
                    bet_type=get_text(uid, f"cf_{choice}"),
                    status_msg=status, win=payout, coins=user["coins"])
    await edit_msg(query, text, play_again_kbd(uid, "coinflip"))


# ────────────────────────────────────────────────
#  GAME: CRASH
# ────────────────────────────────────────────────

async def play_crash(query, uid, bet):
    user = get_user(uid)
    user["coins"] -= bet
    user["stats"]["games_played"] = user["stats"].get("games_played", 0) + 1
    save_users()

    r = random.random()
    crash_at = 1.00 if r < 0.08 else min(100.0, round(1.0 + random.expovariate(0.35), 2))

    ACTIVE_GAMES[uid] = {
        "game": "crash", "bet": bet,
        "multiplier": 1.00, "crash_at": crash_at, "cashed_out": False
    }

    await edit_msg(query, get_text(uid, "crash_preparing"))
    await asyncio.sleep(1.5)

    mult = 1.00
    while mult < crash_at:
        if uid not in ACTIVE_GAMES or ACTIVE_GAMES[uid].get("cashed_out"):
            return
        ACTIVE_GAMES[uid]["multiplier"] = mult
        potential = bet * mult
        text = get_text(uid, "crash_flying", multiplier=mult, potential=potential)
        btn_label = get_text(uid, "btn_cash_out", multiplier=mult)
        await edit_msg(query, text,
                       InlineKeyboardMarkup([[InlineKeyboardButton(btn_label, callback_data="crash_cashout")]]))
        await asyncio.sleep(1.2)
        if mult < 2.0:
            mult = round(mult + 0.1, 2)
        elif mult < 5.0:
            mult = round(mult + 0.25, 2)
        elif mult < 15.0:
            mult = round(mult + 1.0, 2)
        else:
            mult = round(mult + 5.0, 2)

    # Rocket crashed
    if uid in ACTIVE_GAMES and not ACTIVE_GAMES[uid].get("cashed_out"):
        user = get_user(uid)
        user["stats"]["total_lost"] = user["stats"].get("total_lost", 0) + bet
        save_users()
        text = get_text(uid, "crash_crashed", multiplier=crash_at, bet=bet)
        ACTIVE_GAMES.pop(uid, None)
        await edit_msg(query, text, play_again_kbd(uid, "crash"))


async def crash_cashout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    await query.answer("Cashed out! 🚀")

    if uid not in ACTIVE_GAMES or ACTIVE_GAMES[uid].get("game") != "crash":
        return

    game = ACTIVE_GAMES[uid]
    if game.get("cashed_out"):
        return
    game["cashed_out"] = True

    mult = game["multiplier"]
    bet = game["bet"]
    win = int(bet * mult)

    user = get_user(uid)
    user["coins"] += win
    user["stats"]["games_won"] = user["stats"].get("games_won", 0) + 1
    user["stats"]["total_won"] = user["stats"].get("total_won", 0) + win
    save_users()
    ACTIVE_GAMES.pop(uid, None)

    text = get_text(uid, "crash_cashed", multiplier=mult, win=win, coins=user["coins"])
    await edit_msg(query, text, play_again_kbd(uid, "crash"))


# ────────────────────────────────────────────────
#  WHEEL OF FORTUNE
# ────────────────────────────────────────────────

async def view_wheel(query, uid):
    user = get_user(uid)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    last = user.get("last_daily")
    if last:
        last_dt = datetime.fromisoformat(last)
        diff = now - last_dt
        if diff < timedelta(hours=24):
            remaining = timedelta(hours=24) - diff
            hrs = int(remaining.total_seconds() // 3600)
            mins = int((remaining.total_seconds() % 3600) // 60)
            await query.answer("Cooldown!", show_alert=True)
            await edit_msg(query, get_text(uid, "wheel_cooldown", hrs=hrs, mins=mins), back_kbd(uid))
            return

    await edit_msg(query, get_text(uid, "wheel_spinning"))
    await asyncio.sleep(1.5)

    payouts = [50, 100, 200, 500, 1000, 5000]
    weights = [0.30, 0.30, 0.20, 0.12, 0.06, 0.02]
    bonus = random.choices(payouts, weights=weights, k=1)[0]

    user["coins"] += bonus
    user["last_daily"] = now.isoformat()
    save_users()

    await edit_msg(query, get_text(uid, "wheel_result", bonus=bonus, coins=user["coins"]), back_kbd(uid))


# ────────────────────────────────────────────────
#  MAIN CALLBACK DISPATCHER
# ────────────────────────────────────────────────

async def button_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(query.from_user.id)
    fname = query.from_user.first_name
    get_user(uid, fname)
    data = query.data

    # Guard against starting new games while one is active
    active = ACTIVE_GAMES.get(uid)
    if active:
        gtype = active.get("game")
        if gtype == "crash" and data != "crash_cashout":
            try:
                await query.answer("Finish your Crash game first! 🚀", show_alert=True)
            except Exception:
                pass
            return
        if gtype == "blackjack" and data not in ("bj_hit", "bj_stand"):
            try:
                await query.answer("Finish your Blackjack game first! 🃏", show_alert=True)
            except Exception:
                pass
            return

    # Determine if we should defer answering because we will show custom text or show_alert
    defer_answer = False
    if data == "crash_cashout":
        defer_answer = True
    elif data == "menu_wheel":
        user = get_user(uid)
        last = user.get("last_daily")
        if last:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            try:
                last_dt = datetime.fromisoformat(last)
                if now - last_dt < timedelta(hours=24):
                    defer_answer = True
            except Exception:
                pass
    elif data.startswith("play_"):
        parts = data.split("_")
        if len(parts) >= 3:
            amt = parts[2]
            user = get_user(uid)
            try:
                bet = user["coins"] if amt == "allin" else int(amt)
                if bet <= 0 or bet > user["coins"]:
                    defer_answer = True
            except Exception:
                pass
    elif data.startswith("choice_"):
        parts = data.split("_")
        if len(parts) >= 4:
            try:
                bet = int(parts[2])
                user = get_user(uid)
                if bet <= 0 or bet > user["coins"]:
                    defer_answer = True
            except Exception:
                pass

    if not defer_answer:
        try:
            await query.answer()
        except Exception:
            pass

    # ── Navigation ──
    if data == "menu_main":
        await view_main_menu(query, uid, edit=True)

    elif data == "menu_games":
        await view_games_menu(query, uid)

    elif data == "menu_profile":
        await view_profile(query, uid)

    elif data == "menu_leaderboard":
        await view_leaderboard(query, uid)

    elif data == "menu_lang":
        await view_lang_menu(query, uid)

    elif data == "menu_wheel":
        await view_wheel(query, uid)

    elif data.startswith("setlang_"):
        lang = data.split("_")[1]
        user = get_user(uid)
        user["lang"] = lang
        save_users()
        await view_main_menu(query, uid, edit=True)

    # ── Bet selection ──
    elif data.startswith("bet_select_"):
        game = data[len("bet_select_"):]
        await view_bet_select(query, uid, game)

    # ── Game launch ──
    elif data.startswith("play_"):
        parts = data.split("_")          # play / <game> / <amount>
        game = parts[1]
        amt  = parts[2]
        user = get_user(uid)
        bet = user["coins"] if amt == "allin" else int(amt)
        if bet <= 0 or bet > user["coins"]:
            await query.answer(get_text(uid, "invalid_bet"), show_alert=True)
            return

        if game == "slots":
            await play_slots(query, uid, bet)
        elif game == "blackjack":
            await play_blackjack(query, uid, bet)
        elif game == "crash":
            asyncio.create_task(play_crash(query, uid, bet))
        elif game == "roulette":
            text = get_text(uid, "roulette_title", bet=bet) + "\n\n" + get_text(uid, "roulette_choice")
            kbd = InlineKeyboardMarkup([
                [InlineKeyboardButton(get_text(uid, "roulette_red") + " 🔴", callback_data=f"choice_roulette_{bet}_red"),
                 InlineKeyboardButton(get_text(uid, "roulette_black") + " ⚫", callback_data=f"choice_roulette_{bet}_black")],
                [InlineKeyboardButton(get_text(uid, "roulette_green") + " 🟢", callback_data=f"choice_roulette_{bet}_green")],
                [InlineKeyboardButton(get_text(uid, "roulette_even"), callback_data=f"choice_roulette_{bet}_even"),
                 InlineKeyboardButton(get_text(uid, "roulette_odd"), callback_data=f"choice_roulette_{bet}_odd")],
                [InlineKeyboardButton(get_text(uid, "btn_back"), callback_data=f"bet_select_roulette")],
            ])
            await edit_msg(query, text, kbd)
        elif game == "coinflip":
            text = get_text(uid, "cf_title", bet=bet) + "\n\n" + get_text(uid, "cf_choice")
            kbd = InlineKeyboardMarkup([
                [InlineKeyboardButton(get_text(uid, "cf_heads"), callback_data=f"choice_cf_{bet}_heads"),
                 InlineKeyboardButton(get_text(uid, "cf_tails"), callback_data=f"choice_cf_{bet}_tails")],
                [InlineKeyboardButton(get_text(uid, "btn_back"), callback_data=f"bet_select_coinflip")],
            ])
            await edit_msg(query, text, kbd)

    # ── In-game choices ──
    elif data.startswith("choice_"):
        parts = data.split("_")          # choice / <game> / <bet> / <pick>
        game  = parts[1]
        bet   = int(parts[2])
        pick  = parts[3]
        user  = get_user(uid)
        if bet <= 0 or bet > user["coins"]:
            await query.answer(get_text(uid, "invalid_bet"), show_alert=True)
            return
        if game == "roulette":
            await play_roulette(query, uid, bet, pick)
        elif game == "cf":
            await play_coinflip(query, uid, bet, pick)

    # ── Blackjack actions ──
    elif data == "bj_hit":
        await bj_hit(update, context)

    elif data == "bj_stand":
        await bj_stand(update, context)

    # ── Crash cash-out ──
    elif data == "crash_cashout":
        await crash_cashout(update, context)


# ────────────────────────────────────────────────
#  COMMAND HANDLERS
# ────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    get_user(uid, update.effective_user.first_name)
    ACTIVE_GAMES.pop(uid, None)
    await view_main_menu(update.message, uid)


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    user = get_user(uid, update.effective_user.first_name)
    await reply_html(update.message,
                     f"💵 Ваш баланс: <b>{user['coins']} монет</b>",
                     InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="menu_main")]]))


async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    get_user(uid, update.effective_user.first_name)
    kbd = InlineKeyboardMarkup([[InlineKeyboardButton("🎡 Крутить / Spin", callback_data="menu_wheel")]])
    await reply_html(update.message, get_text(uid, "wheel_title"), kbd)


async def cmd_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    user = get_user(uid, update.effective_user.first_name)
    text = get_text(uid, "choose_bet", game=get_text(uid, "game_slots"), coins=user["coins"])
    await reply_html(update.message, text, bet_kbd(uid, "slots"))


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    get_user(uid, update.effective_user.first_name)
    top = get_leaderboard_data()
    lines = [get_text(uid, "menu_leaderboard"), ""]
    for i, (userid, data) in enumerate(top, 1):
        xp = data.get("stats", {}).get("total_won", 0) + data.get("stats", {}).get("total_lost", 0)
        level = max(1, int(xp // 1000) + 1)
        name = data.get("first_name") or f"Player {userid[:6]}"
        lines.append(get_text(uid, "leaderboard_row",
                               index=i, name=name, coins=data.get("coins", 0), level=level))
    await reply_html(update.message, "\n".join(lines),
                     InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="menu_main")]]))


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    get_user(uid, update.effective_user.first_name)
    text = get_text(uid, "menu_lang")
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="setlang_ru"),
         InlineKeyboardButton("🇺🇸 English", callback_data="setlang_en")],
        [InlineKeyboardButton("🏠 Меню", callback_data="menu_main")],
    ])
    await reply_html(update.message, text, kbd)


# ────────────────────────────────────────────────
#  MAIN
# ────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("menu",        cmd_start))
    app.add_handler(CommandHandler("balance",     cmd_balance))
    app.add_handler(CommandHandler("daily",       cmd_daily))
    app.add_handler(CommandHandler("slot",        cmd_slots))
    app.add_handler(CommandHandler("slots",       cmd_slots))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("lang",        cmd_lang))
    app.add_handler(CallbackQueryHandler(button_dispatcher))

    logger.info("Bot started polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
