import sqlite3
import random
import math
import os
import shutil
import asyncio
from datetime import datetime, timedelta
from typing import Tuple, Optional, List, Dict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes
)

# ===== КОНФИГУРАЦИЯ =====
TOKEN = "8366865358:AAHow4w4DylLhHxWDqHKHdDSkkPkHbwiThE"  # ЗАМЕНИ!
ADMIN_IDS = [1320819190]  # ТВОЙ ID!

DB_NAME = "tyryaga_bot.db"
BACKUP_DIR = "backups"

# Настройки
BASE_XP_PER_LEVEL = 100
LEVEL_MULTIPLIER = 1.12
DAILY_ATTACK_LIMIT = 25
PARTY_TIME_HOURS = 3

# БАЗОВЫЙ УРОН ЗАТОЧКИ (1 уровень)
BASE_SHANK_DAMAGE = 15

# БОССЫ
BOSSES = {
    1: {"name": "Шнырь", "desc": "Местный шестёрка", "base_hp": 80, "base_damage": 8, "reward_xp": 80, "reward_chifir": 40, "min_respect": 1},
    2: {"name": "Баклан", "desc": "Бывший мент", "base_hp": 200, "base_damage": 20, "reward_xp": 200, "reward_chifir": 100, "min_respect": 5},
    3: {"name": "Вор в Законе", "desc": "Главный авторитет", "base_hp": 500, "base_damage": 50, "reward_xp": 500, "reward_chifir": 250, "min_respect": 15}
}

ATTACKS = {
    "zatochka": {"name": "🔪 Заточка", "damage_mult": 1.0, "cost": 0},
    "butylka": {"name": "🍾 Бутылка", "damage_mult": 1.5, "cost": 20},
    "klyuch": {"name": "🔧 Гаечный ключ", "damage_mult": 2.0, "cost": 50}
}

EARN_METHODS = {
    "work": {"name": "🏭 Работа", "min": 5, "max": 20, "cooldown": 300},
    "card": {"name": "🎲 Карты", "min": 10, "max": 50, "cooldown": 600},
    "fight": {"name": "👊 Драка", "min": 15, "max": 30, "cooldown": 900}
}

# ===== ФУНКЦИИ =====
def get_zatochka_damage(level: int) -> int:
    """Урон заточки: базовый 15 + 5 за каждый уровень"""
    return BASE_SHANK_DAMAGE + (level - 1) * 5

def get_zatochka_cost(level: int) -> int:
    return 50 + (level - 1) * 25

def get_boss_stats(boss_id: int, respect: int) -> dict:
    boss = BOSSES[boss_id]
    # Множитель сложности от авторитета игрока
    mult = 1 + (respect - boss["min_respect"]) * 0.05 if respect >= boss["min_respect"] else 0.5
    mult = max(0.5, min(mult, 2.0))
    return {
        "name": boss["name"],
        "desc": boss["desc"],
        "hp": int(boss["base_hp"] * mult),
        "damage": int(boss["base_damage"] * mult),
        "reward_xp": int(boss["reward_xp"] * mult),
        "reward_chifir": int(boss["reward_chifir"] * mult),
        "min_respect": boss["min_respect"]
    }

def get_attack_damage(attack_type: str, zatochka_level: int, boss_id: int = None) -> int:
    """Расчет урона с учетом типа атаки и уровня заточки"""
    base_damage = get_zatochka_damage(zatochka_level)
    mult = ATTACKS[attack_type]["damage_mult"]
    damage = int(base_damage * mult)
    
    # Бонусы против определенных боссов
    if boss_id == 2 and attack_type == "butylka":
        damage = int(damage * 1.3)  # Бутылка эффективна против Баклана
    elif boss_id == 3 and attack_type == "klyuch":
        damage = int(damage * 1.2)  # Ключ эффективен против Вора
    
    return max(1, damage)

# ===== ОСТАЛЬНЫЕ ФУНКЦИИ (сокращены для экономии места, но основные остаются) =====
def get_xp_for_respect(respect: int) -> int:
    return int(BASE_XP_PER_LEVEL * (LEVEL_MULTIPLIER ** (respect - 1)))

def get_respect_from_xp(total_xp: int) -> Tuple[int, int]:
    respect = 1
    while total_xp >= get_xp_for_respect(respect):
        total_xp -= get_xp_for_respect(respect)
        respect += 1
    return respect, total_xp

def get_referrals_count(user_id: int) -> int:
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM referrals WHERE referred_by = ?", (user_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except:
        return 0

def get_user_cooldown_multiplier(user_id: int) -> float:
    return max(0.75, 1 - (get_referrals_count(user_id) * 0.005))

def add_xp(user_id: int, xp_amount: int, name: str = None) -> Tuple[int, int, bool]:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT respect, xp, total_xp FROM zeks WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if not result:
        cursor.execute('INSERT INTO zeks (user_id, name, last_active, created_at) VALUES (?, ?, ?, ?)',
                      (user_id, name, datetime.now().isoformat(), datetime.now().isoformat()))
        conn.commit()
        respect, current_xp, total_xp = 1, 0, 0
    else:
        respect, current_xp, total_xp = result
    total_xp += xp_amount
    new_respect, new_current_xp = get_respect_from_xp(total_xp)
    levels_gained = new_respect - respect
    leveled_up = levels_gained > 0
    cursor.execute('UPDATE zeks SET respect = ?, xp = ?, total_xp = ?, last_active = ? WHERE user_id = ?',
                  (new_respect, new_current_xp, total_xp, datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()
    return new_respect, levels_gained, leveled_up

def add_chifir(user_id: int, amount: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE zeks SET chifir = chifir + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def remove_chifir(user_id: int, amount: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT chifir FROM zeks WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if not result or result[0] < amount:
        conn.close()
        return False
    cursor.execute("UPDATE zeks SET chifir = chifir - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()
    return True

def get_zek_info(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT name, respect, xp, total_xp, zatochka, chifir, boss_kills, krysa_count, is_banned, ban_until FROM zeks WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    if not result:
        return None
    xp_needed = get_xp_for_respect(result[1]) - result[2]
    return {
        "name": result[0] or f"zek_{user_id}",
        "respect": result[1],
        "current_xp": result[2],
        "total_xp": result[3],
        "zatochka": result[4],
        "zatochka_damage": get_zatochka_damage(result[4]),
        "upgrade_cost": get_zatochka_cost(result[4]),
        "chifir": result[5],
        "boss_kills": result[6],
        "krysa_count": result[7],
        "xp_to_next": xp_needed,
        "is_banned": result[8],
        "ban_until": result[9]
    }

def get_player_rank(user_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) + 1 FROM zeks WHERE is_banned = 0 AND total_xp > (SELECT total_xp FROM zeks WHERE user_id = ?)', (user_id,))
    rank = cursor.fetchone()[0]
    conn.close()
    return rank

def get_all_players(page: int = 0, per_page: int = 15):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM zeks WHERE is_banned = 0')
    total = cursor.fetchone()[0]
    offset = page * per_page
    cursor.execute('SELECT user_id, name, respect, total_xp, zatochka, chifir, boss_kills FROM zeks WHERE is_banned = 0 ORDER BY respect DESC, total_xp DESC LIMIT ? OFFSET ?', (per_page, offset))
    results = cursor.fetchall()
    conn.close()
    return [{"user_id": r[0], "name": r[1], "respect": r[2], "total_xp": r[3], "zatochka": r[4], "chifir": r[5], "boss_kills": r[6]} for r in results], total

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def get_daily_attacks(user_id: int) -> int:
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT daily_attacks FROM zeks WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else 0
    except:
        return 0

def increment_daily_attacks(user_id: int):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE zeks SET daily_attacks = daily_attacks + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    except:
        pass

def reset_daily_attacks():
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE zeks SET daily_attacks = 0")
        conn.commit()
        conn.close()
    except:
        pass

def get_boss_progress(user_id: int, boss_id: int) -> dict:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT current_hp FROM boss_fights WHERE user_id = ? AND boss_id = ?", (user_id, boss_id))
    result = cursor.fetchone()
    conn.close()
    info = get_zek_info(user_id)
    boss_stats = get_boss_stats(boss_id, info["respect"] if info else 1)
    hp = result[0] if result and result[0] and result[0] > 0 else boss_stats["hp"]
    return {"hp": hp, "is_active": True}

def update_boss_hp(user_id: int, boss_id: int, new_hp: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO boss_fights (user_id, boss_id, current_hp, last_fight) VALUES (?, ?, ?, ?)',
                  (user_id, boss_id, new_hp, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def can_work(user_id: int, work_type: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT last_used FROM work_cooldown WHERE user_id = ? AND work_type = ?", (user_id, work_type))
    result = cursor.fetchone()
    conn.close()
    if not result or not result[0]:
        return True, 0
    last = datetime.fromisoformat(result[0])
    left = EARN_METHODS[work_type]["cooldown"] - (datetime.now() - last).total_seconds()
    return (True, 0) if left <= 0 else (False, int(left))

def set_work_cooldown(user_id: int, work_type: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO work_cooldown (user_id, work_type, last_used) VALUES (?, ?, ?)',
                  (user_id, work_type, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_havka_cooldown(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT last_claim FROM daily_havka WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if not result or not result[0]:
        return True, 0
    last = datetime.fromisoformat(result[0])
    left = 10800 - (datetime.now() - last).total_seconds()
    return (True, 0) if left <= 0 else (False, int(left))

def set_havka_cooldown(user_id: int, streak: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO daily_havka VALUES (?, ?, ?)", (user_id, datetime.now().isoformat(), streak))
    conn.commit()
    conn.close()

def can_krysa(user_id: int):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT last_krysa FROM zeks WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        if not result or not result[0]:
            return True, 0
        last = datetime.fromisoformat(result[0])
        left = 3600 - (datetime.now() - last).total_seconds()
        return (True, 0) if left <= 0 else (False, int(left))
    except:
        return True, 0

def set_krysa_cooldown(user_id: int):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE zeks SET last_krysa = ? WHERE user_id = ?", (datetime.now().isoformat(), user_id))
        conn.commit()
        conn.close()
    except:
        pass

def add_referral(user_id: int, referrer_id: int):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM referrals WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            cursor.execute('INSERT INTO referrals (user_id, referred_by, bonus_claimed, referred_at) VALUES (?, ?, 0, ?)',
                          (user_id, referrer_id, datetime.now().isoformat()))
            conn.commit()
        conn.close()
    except:
        pass

def add_feedback(user_id: int, username: str, message: str) -> bool:
    if len(message) > 1000:
        return False
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO feedback (user_id, username, message, created_at) VALUES (?, ?, ?, ?)',
                      (user_id, username, message, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def send_gift(from_user: int, to_user: int, amount: int) -> bool:
    if amount > 50:
        return False
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT chifir FROM zeks WHERE user_id = ?", (from_user,))
    result = cursor.fetchone()
    if not result or result[0] < amount:
        conn.close()
        return False
    cursor.execute("UPDATE zeks SET chifir = chifir - ? WHERE user_id = ?", (amount, from_user))
    cursor.execute("UPDATE zeks SET chifir = chifir + ? WHERE user_id = ?", (amount, to_user))
    try:
        cursor.execute("INSERT INTO gifts (from_user, to_user, amount, created_at) VALUES (?, ?, ?, ?)",
                      (from_user, to_user, amount, datetime.now().isoformat()))
    except:
        pass
    conn.commit()
    conn.close()
    return True

# ===== ФУНКЦИИ ПАТИ =====
def create_party(leader_id: int, boss_id: int, boss_hp: int, boss_max_hp: int) -> int:
    expires = (datetime.now() + timedelta(hours=PARTY_TIME_HOURS)).isoformat()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO parties (boss_id, boss_current_hp, boss_max_hp, members, leader_id, created_at, expires_at, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
    ''', (boss_id, boss_hp, boss_max_hp, str(leader_id), leader_id, datetime.now().isoformat(), expires))
    party_id = cursor.lastrowid
    cursor.execute('INSERT OR REPLACE INTO boss_fights (user_id, boss_id, current_hp, last_fight, party_id) VALUES (?, ?, ?, ?, ?)',
                  (leader_id, boss_id, boss_hp, datetime.now().isoformat(), party_id))
    conn.commit()
    conn.close()
    return party_id

def join_party(user_id: int, party_id: int, boss_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT members, boss_current_hp FROM parties WHERE id = ? AND is_active = 1", (party_id,))
    result = cursor.fetchone()
    if result:
        members = result[0].split(',') if result[0] else []
        if str(user_id) not in members:
            members.append(str(user_id))
            cursor.execute("UPDATE parties SET members = ? WHERE id = ?", (','.join(members), party_id))
            cursor.execute('INSERT OR REPLACE INTO boss_fights (user_id, boss_id, current_hp, last_fight, party_id) VALUES (?, ?, ?, ?, ?)',
                          (user_id, boss_id, result[1], datetime.now().isoformat(), party_id))
    conn.commit()
    conn.close()

def get_party(party_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, boss_id, boss_current_hp, boss_max_hp, members, leader_id, created_at, expires_at, is_active FROM parties WHERE id = ? AND is_active = 1", (party_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return {
            "id": result[0],
            "boss_id": result[1],
            "boss_current_hp": result[2],
            "boss_max_hp": result[3],
            "members": result[4].split(',') if result[4] else [],
            "leader_id": result[5],
            "created_at": result[6],
            "expires_at": result[7],
            "is_active": result[8]
        }
    return None

def get_party_by_boss(boss_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, boss_id, boss_current_hp, boss_max_hp, members, leader_id, created_at, expires_at, is_active FROM parties WHERE boss_id = ? AND expires_at > ? AND is_active = 1", 
                   (boss_id, datetime.now().isoformat()))
    result = cursor.fetchone()
    conn.close()
    if result:
        return {
            "id": result[0],
            "boss_id": result[1],
            "boss_current_hp": result[2],
            "boss_max_hp": result[3],
            "members": result[4].split(',') if result[4] else [],
            "leader_id": result[5],
            "created_at": result[6],
            "expires_at": result[7],
            "is_active": result[8]
        }
    return None

def update_party_hp(party_id: int, new_hp: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE parties SET boss_current_hp = ? WHERE id = ?", (new_hp, party_id))
    cursor.execute("UPDATE boss_fights SET current_hp = ? WHERE party_id = ?", (new_hp, party_id))
    conn.commit()
    conn.close()

def get_party_members_hp(party_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT members, boss_current_hp FROM parties WHERE id = ?", (party_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return result[0].split(',') if result[0] else [], result[1]
    return [], 0

async def end_party_battle(party_id: int, is_victory: bool, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT members, boss_id FROM parties WHERE id = ?", (party_id,))
    result = cursor.fetchone()
    if not result:
        conn.close()
        return
    
    members = result[0].split(',') if result[0] else []
    boss_id = result[1]
    boss_stats = get_boss_stats(boss_id, 1)
    boss_name = BOSSES[boss_id]["name"]
    
    if is_victory:
        reward_xp = boss_stats["reward_xp"]
        reward_chifir = boss_stats["reward_chifir"]
        
        for member_id in members:
            member_id = int(member_id)
            add_xp(member_id, reward_xp, "party")
            add_chifir(member_id, reward_chifir)
            conn2 = sqlite3.connect(DB_NAME)
            cursor2 = conn2.cursor()
            cursor2.execute("UPDATE zeks SET boss_kills = boss_kills + 1 WHERE user_id = ?", (member_id,))
            conn2.commit()
            conn2.close()
            try:
                await context.bot.send_message(
                    member_id,
                    f"🎉 *ПОБЕДА В ПАТИ!* 🎉\n\n"
                    f"Ваша команда победила босса *{boss_name}*!\n\n"
                    f"📦 *Твоя награда:*\n"
                    f"• +{reward_xp} опыта\n"
                    f"• +{reward_chifir} чифира\n\n"
                    f"👑 Ты получил +1 к победам над авторитетами!",
                    parse_mode="Markdown"
                )
            except:
                pass
    else:
        for member_id in members:
            try:
                await context.bot.send_message(
                    int(member_id),
                    f"💀 *ПОРАЖЕНИЕ В ПАТИ* 💀\n\n"
                    f"Ваша команда не смогла победить босса *{boss_name}* вовремя.\n\n"
                    f"Битва завершена. Ты можешь начать новую битву!",
                    parse_mode="Markdown"
                )
            except:
                pass
    
    for member_id in members:
        cursor.execute("DELETE FROM boss_fights WHERE user_id = ? AND party_id = ?", (int(member_id), party_id))
    cursor.execute("DELETE FROM parties WHERE id = ?", (party_id,))
    conn.commit()
    conn.close()

def cleanup_expired_parties(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("SELECT id FROM parties WHERE expires_at < ? AND is_active = 1", (now,))
    expired = cursor.fetchall()
    for party in expired:
        asyncio.create_task(end_party_battle(party[0], False, context))
    conn.close()

# ===== КЛАВИАТУРЫ =====
def get_main_keyboard(user_id: int):
    kb = [
        [InlineKeyboardButton("👤 Досье", callback_data="profile"), InlineKeyboardButton("⚔️ Заточка", callback_data="weapon")],
        [InlineKeyboardButton("👊 Боссы", callback_data="bosses_menu"), InlineKeyboardButton("💰 Заработок", callback_data="earn_menu")],
        [InlineKeyboardButton("🏆 Рейтинг", callback_data="leaderboard"), InlineKeyboardButton("🍜 Хавка", callback_data="daily")],
        [InlineKeyboardButton("🤝 Пригласить", callback_data="referral"), InlineKeyboardButton("📈 Прогресс", callback_data="progress")],
        [InlineKeyboardButton("🐀 Крыса", callback_data="krysa"), InlineKeyboardButton("🎁 Подарок", callback_data="gift_start")],
        [InlineKeyboardButton("💬 Отзыв", callback_data="feedback_start")]
    ]
    if is_admin(user_id):
        kb.append([InlineKeyboardButton("👑 Админка", callback_data="admin_panel")])
    return InlineKeyboardMarkup(kb)

def get_back(target: str):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=target)]])

def get_admin_keyboard():
    kb = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("📈 Статистика за сутки", callback_data="admin_daily")],
        [InlineKeyboardButton("🔍 Найти игрока", callback_data="admin_find")],
        [InlineKeyboardButton("📋 Список игроков", callback_data="admin_list")],
        [InlineKeyboardButton("💬 Отзывы", callback_data="admin_feedback")],
        [InlineKeyboardButton("🎁 Подарок всем", callback_data="admin_gift_all")],
        [InlineKeyboardButton("⭐ Прокачать админа", callback_data="admin_max_out")],
        [InlineKeyboardButton("💾 Бэкап", callback_data="admin_backup")],
        [InlineKeyboardButton("🗑️ Очистить бои", callback_data="admin_clear_fights")],
        [InlineKeyboardButton("📤 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔄 Сброс атак", callback_data="admin_reset_attacks")],
        [InlineKeyboardButton("🔙 В меню", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(kb)

def get_player_list_keyboard(page: int, total_pages: int):
    kb = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_page_{page-1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_page_{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(kb)

def get_player_detail_keyboard(target_id: int):
    kb = [
        [InlineKeyboardButton("➕ Авторитет", callback_data=f"admin_raise_{target_id}")],
        [InlineKeyboardButton("🎁 XP", callback_data=f"admin_gift_{target_id}")],
        [InlineKeyboardButton("💰 Чифир", callback_data=f"admin_gold_{target_id}")],
        [InlineKeyboardButton("⚔️ Заточка", callback_data=f"admin_weapon_{target_id}")],
        [InlineKeyboardButton("🚫 Бан", callback_data=f"admin_ban_{target_id}")],
        [InlineKeyboardButton("✅ Разбан", callback_data=f"admin_unban_{target_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_list")]
    ]
    return InlineKeyboardMarkup(kb)

def get_bosses_keyboard(user_id: int):
    kb = []
    info = get_zek_info(user_id)
    for bid, boss in BOSSES.items():
        if info and info["respect"] >= boss["min_respect"]:
            party = get_party_by_boss(bid)
            if party:
                hp_percent = int(party["boss_current_hp"] / party["boss_max_hp"] * 100) if party["boss_max_hp"] > 0 else 0
                kb.append([InlineKeyboardButton(f"👥 {boss['name']} (ПАТИ, HP: {hp_percent}%)", callback_data=f"party_view_{party['id']}")])
            else:
                kb.append([InlineKeyboardButton(f"👑 {boss['name']}", callback_data=f"boss_{bid}")])
        else:
            kb.append([InlineKeyboardButton(f"🔒 {boss['name']} (нужен {boss['min_respect']} ур.)", callback_data="noop")])
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(kb)

def get_party_action_keyboard(party_id: int, boss_id: int):
    kb = [
        [InlineKeyboardButton("🔪 Заточка (0🍺)", callback_data=f"party_attack_{party_id}_{boss_id}_zatochka")],
        [InlineKeyboardButton("🍾 Бутылка (20🍺)", callback_data=f"party_attack_{party_id}_{boss_id}_butylka")],
        [InlineKeyboardButton("🔧 Гаечный ключ (50🍺)", callback_data=f"party_attack_{party_id}_{boss_id}_klyuch")],
        [InlineKeyboardButton("📎 Пригласить друзей", callback_data=f"party_invite_{party_id}_{boss_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="bosses_menu")]
    ]
    return InlineKeyboardMarkup(kb)

def get_attack_keyboard(boss_id: int):
    kb = [
        [InlineKeyboardButton("🔪 Заточка (0🍺)", callback_data=f"attack_{boss_id}_zatochka")],
        [InlineKeyboardButton("🍾 Бутылка (20🍺)", callback_data=f"attack_{boss_id}_butylka")],
        [InlineKeyboardButton("🔧 Гаечный ключ (50🍺)", callback_data=f"attack_{boss_id}_klyuch")],
        [InlineKeyboardButton("👥 Создать пати", callback_data=f"create_party_{boss_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="bosses_menu")]
    ]
    return InlineKeyboardMarkup(kb)

def get_earn_keyboard():
    kb = [
        [InlineKeyboardButton("🏭 Работа", callback_data="earn_work")],
        [InlineKeyboardButton("🎲 Карты", callback_data="earn_card")],
        [InlineKeyboardButton("👊 Драка", callback_data="earn_fight")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(kb)

def get_gift_amount_keyboard():
    kb = [
        [InlineKeyboardButton("10 🍺", callback_data="gift_10"), InlineKeyboardButton("20 🍺", callback_data="gift_20")],
        [InlineKeyboardButton("30 🍺", callback_data="gift_30"), InlineKeyboardButton("50 🍺", callback_data="gift_50")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(kb)

# ===== ОБРАБОТЧИКИ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name or f"zek_{user_id}"
    
    if context.args and len(context.args) > 0:
        arg = context.args[0]
        if arg.startswith("battle_"):
            try:
                parts = arg.split("_")
                if len(parts) >= 3:
                    party_id = int(parts[1])
                    boss_id = int(parts[2])
                    party = get_party(party_id)
                    if party:
                        if str(user_id) not in party["members"]:
                            join_party(user_id, party_id, boss_id)
                            await update.message.reply_text(f"👥 Ты присоединился к битве с боссом {BOSSES[boss_id]['name']}!")
                        else:
                            await update.message.reply_text(f"👥 Ты уже в битве с боссом {BOSSES[boss_id]['name']}!")
                    else:
                        await update.message.reply_text("❌ Пати уже не существует!")
            except:
                pass
        else:
            try:
                ref = int(arg)
                if ref != user_id:
                    add_referral(user_id, ref)
                    await update.message.reply_text("🤝 Ты пришёл по приглашению!")
            except:
                pass
    
    add_xp(user_id, 1, name)
    info = get_zek_info(user_id)
    if not info:
        await update.message.reply_text("❌ Ошибка")
        return
    
    await update.message.reply_text(
        f"⛓️ *ТЮРЯГА* ⛓️\n\n"
        f"👤 *{info['name']}*\n"
        f"📊 Авторитет: *{info['respect']}* (#{get_player_rank(user_id)})\n"
        f"⚔️ Заточка: +{info['zatochka_damage']} урона (ур.{info['zatochka']})\n"
        f"🍺 Чифира: {info['chifir']}\n"
        f"👑 Побед: {info['boss_kills']}\n"
        f"🐀 Крысят: {info['krysa_count']}\n"
        f"📈 Опыт: {info['current_xp']}/{info['current_xp'] + info['xp_to_next']}",
        reply_markup=get_main_keyboard(user_id), parse_mode="Markdown")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    name = query.from_user.first_name or f"zek_{user_id}"
    data = query.data
    
    if data == "noop":
        return
    
    # Профиль
    if data == "profile":
        info = get_zek_info(user_id)
        text = f"👤 *Досье*\n\n{info['name']}\n📊 Авторитет: {info['respect']} (#{get_player_rank(user_id)})\n🍺 Чифира: {info['chifir']}\n⚔️ Заточка: ур.{info['zatochka']} (+{info['zatochka_damage']})\n👑 Побед: {info['boss_kills']}\n🐀 Крысят: {info['krysa_count']}\n🤝 Друзей: {get_referrals_count(user_id)}"
        await query.edit_message_text(text, parse_mode="Markdown")
        await query.message.reply_text("⬅️ Меню", reply_markup=get_main_keyboard(user_id), parse_mode="Markdown")
    
    # Заточка
    elif data == "weapon":
        info = get_zek_info(user_id)
        kb = [[InlineKeyboardButton(f"🔪 Улучшить ({info['upgrade_cost']}🍺)", callback_data="upgrade_weapon")], [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]
        await query.edit_message_text(f"⚔️ *Заточка*\nУровень: {info['zatochka']}\nУрон: +{info['zatochka_damage']}\nДо след. уровня: {info['upgrade_cost']}🍺", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    
    elif data == "upgrade_weapon":
        info = get_zek_info(user_id)
        if info['chifir'] >= info['upgrade_cost']:
            remove_chifir(user_id, info['upgrade_cost'])
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("UPDATE zeks SET zatochka = zatochka + 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            await query.edit_message_text(f"✅ Заточка повышена до {info['zatochka'] + 1} уровня!\nНовый урон: +{get_zatochka_damage(info['zatochka'] + 1)}", parse_mode="Markdown")
        else:
            await query.edit_message_text(f"❌ Нужно {info['upgrade_cost']}🍺, у тебя {info['chifir']}🍺")
        await query.message.reply_text("⬅️ Меню", reply_markup=get_main_keyboard(user_id), parse_mode="Markdown")
    
    # Боссы
    elif data == "bosses_menu":
        await query.edit_message_text("👊 *Выбери босса:*", reply_markup=get_bosses_keyboard(user_id), parse_mode="Markdown")
    
    elif data.startswith("boss_"):
        try:
            boss_id = int(data.split("_")[1])
            info = get_zek_info(user_id)
            stats = get_boss_stats(boss_id, info["respect"])
            attacks = get_daily_attacks(user_id)
            text = f"👑 *{stats['name']}*\n{stats['desc']}\n\n❤️ HP: {stats['hp']}\n🎁 Награда: {stats['reward_xp']}XP, {stats['reward_chifir']}🍺\n⚔️ Твой урон заточкой: +{info['zatochka_damage']}\n📊 Атак сегодня: {attacks}/{DAILY_ATTACK_LIMIT}"
            await query.edit_message_text(text, reply_markup=get_attack_keyboard(boss_id), parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=get_back("bosses_menu"))
    
    # Создание пати
    elif data.startswith("create_party_"):
        try:
            boss_id = int(data.split("_")[2])
            info = get_zek_info(user_id)
            stats = get_boss_stats(boss_id, info["respect"])
            
            existing = get_party_by_boss(boss_id)
            if existing:
                await query.edit_message_text(f"❌ Пати для босса {BOSSES[boss_id]['name']} уже существует!", reply_markup=get_back("bosses_menu"))
                return
            
            party_id = create_party(user_id, boss_id, stats["hp"], stats["hp"])
            bot_username = context.bot.username
            invite_link = f"https://t.me/{bot_username}?start=battle_{party_id}_{boss_id}"
            
            await query.edit_message_text(
                f"✅ *Создана пати для битвы с {stats['name']}!*\n\n"
                f"📎 *Ссылка для приглашения:*\n`{invite_link}`\n\n"
                f"👥 *Как это работает:*\n"
                f"• Отправь ссылку друзьям\n"
                f"• Все участники наносят урон одному боссу\n"
                f"• Урон суммируется\n"
                f"• Время на битву: {PARTY_TIME_HOURS} часа\n"
                f"• При победе все получают награду!",
                reply_markup=get_back("bosses_menu"), parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=get_back("bosses_menu"))
    
    # Просмотр пати
    elif data.startswith("party_view_"):
        try:
            party_id = int(data.split("_")[2])
            party = get_party(party_id)
            if not party:
                await query.edit_message_text("❌ Пати не найдена!", reply_markup=get_back("bosses_menu"))
                return
            
            info = get_zek_info(user_id)
            hp_percent = int(party["boss_current_hp"] / party["boss_max_hp"] * 100) if party["boss_max_hp"] > 0 else 0
            members_count = len(party["members"])
            time_left = max(0, (datetime.fromisoformat(party["expires_at"]) - datetime.now()).seconds // 60)
            
            text = f"👥 *Пати против {BOSSES[party['boss_id']]['name']}*\n\n"
            text += f"❤️ Общий HP босса: {party['boss_current_hp']}/{party['boss_max_hp']} ({hp_percent}%)\n"
            text += f"👥 Участников: {members_count}\n"
            text += f"⚔️ Твой урон заточкой: +{info['zatochka_damage']}\n"
            text += f"👑 Лидер: {party['leader_id']}\n"
            text += f"⏳ До конца битвы: {time_left} мин\n\n"
            text += f"*Нанеси удар!*"
            
            await query.edit_message_text(text, reply_markup=get_party_action_keyboard(party_id, party["boss_id"]), parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=get_back("bosses_menu"))
    
    # Атака в пати
    elif data.startswith("party_attack_"):
        try:
            parts = data.split("_")
            if len(parts) >= 5:
                party_id = int(parts[2])
                boss_id = int(parts[3])
                atk_type = parts[4]
                
                party = get_party(party_id)
                if not party:
                    await query.edit_message_text("❌ Пати не найдена!", reply_markup=get_back("bosses_menu"))
                    return
                
                info = get_zek_info(user_id)
                stats = get_boss_stats(boss_id, info["respect"])
                atk = ATTACKS[atk_type]
                
                attacks = get_daily_attacks(user_id)
                if not is_admin(user_id) and attacks >= DAILY_ATTACK_LIMIT:
                    await query.edit_message_text(f"❌ Лимит атак ({DAILY_ATTACK_LIMIT}) исчерпан!")
                    await query.message.reply_text("⬅️ Меню", reply_markup=get_main_keyboard(user_id), parse_mode="Markdown")
                    return
                
                if atk["cost"] > 0 and info["chifir"] < atk["cost"]:
                    await query.edit_message_text(f"❌ Нужно {atk['cost']}🍺, у тебя {info['chifir']}🍺")
                    return
                
                if atk["cost"] > 0:
                    remove_chifir(user_id, atk["cost"])
                
                # РАСЧЕТ УРОНА - ИСПРАВЛЕНО!
                damage = get_attack_damage(atk_type, info["zatochka"], boss_id)
                new_hp = max(0, party["boss_current_hp"] - damage)
                update_party_hp(party_id, new_hp)
                increment_daily_attacks(user_id)
                
                if new_hp <= 0:
                    await end_party_battle(party_id, True, context)
                    await query.edit_message_text(
                        f"🎉 *ПОБЕДА ПАТИ!* 🎉\n\n"
                        f"Твой урон: {damage}\n"
                        f"Босс {stats['name']} повержен!\n\n"
                        f"Все участники получили награду!",
                        parse_mode="Markdown")
                else:
                    counter = max(1, stats["damage"] // 15)
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    cursor.execute("SELECT total_xp FROM zeks WHERE user_id = ?", (user_id,))
                    total = cursor.fetchone()[0]
                    new_total = max(0, total - counter)
                    new_respect, new_xp = get_respect_from_xp(new_total)
                    cursor.execute("UPDATE zeks SET total_xp = ?, respect = ?, xp = ? WHERE user_id = ?", (new_total, new_respect, new_xp, user_id))
                    conn.commit()
                    conn.close()
                    
                    # Оповещаем всех участников пати об атаке
                    members, current_hp = get_party_members_hp(party_id)
                    for member_id in members:
                        if int(member_id) != user_id:
                            try:
                                await context.bot.send_message(
                                    int(member_id),
                                    f"⚔️ *Атака в пати!*\n\n"
                                    f"Игрок *{name}* нанёс {damage} урона боссу {stats['name']}!\n"
                                    f"❤️ Осталось HP: {new_hp}/{party['boss_max_hp']}",
                                    parse_mode="Markdown"
                                )
                            except:
                                pass
                    
                    await query.edit_message_text(
                        f"⚔️ *Атака в пати!*\n\n"
                        f"Твой урон: -{damage}❤️\n"
                        f"Босс контратакует: -{counter}XP\n"
                        f"❤️ Общий HP босса: {new_hp}/{party['boss_max_hp']}",
                        parse_mode="Markdown")
                
                await query.message.reply_text("⬅️ К боссам", reply_markup=get_bosses_keyboard(user_id), parse_mode="Markdown")
            else:
                await query.edit_message_text("❌ Ошибка формата атаки!", reply_markup=get_back("bosses_menu"))
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=get_back("bosses_menu"))
    
    # Приглашение в пати
    elif data.startswith("party_invite_"):
        try:
            parts = data.split("_")
            if len(parts) >= 4:
                party_id = int(parts[2])
                boss_id = int(parts[3])
                bot_username = context.bot.username
                invite_link = f"https://t.me/{bot_username}?start=battle_{party_id}_{boss_id}"
                
                await query.edit_message_text(
                    f"📎 *Пригласи друзей в битву!*\n\n"
                    f"📋 *Ссылка для приглашения:*\n`{invite_link}`\n\n"
                    f"👥 *Как это работает:*\n"
                    f"• Отправь ссылку друзьям\n"
                    f"• Когда друг перейдёт по ссылке, он автоматически присоединится к пати\n"
                    f"• Все участники наносят урон одному боссу\n"
                    f"• Урон суммируется",
                    reply_markup=get_back(f"party_view_{party_id}"), parse_mode="Markdown")
            else:
                await query.edit_message_text("❌ Ошибка!", reply_markup=get_back("bosses_menu"))
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=get_back("bosses_menu"))
    
    # Сольная атака
    elif data.startswith("attack_"):
        try:
            parts = data.split("_")
            if len(parts) >= 3:
                boss_id = int(parts[1])
                atk_type = parts[2]
                info = get_zek_info(user_id)
                stats = get_boss_stats(boss_id, info["respect"])
                atk = ATTACKS[atk_type]
                progress = get_boss_progress(user_id, boss_id)
                
                attacks = get_daily_attacks(user_id)
                if not is_admin(user_id) and attacks >= DAILY_ATTACK_LIMIT:
                    await query.edit_message_text(f"❌ Лимит атак ({DAILY_ATTACK_LIMIT}) исчерпан!")
                    await query.message.reply_text("⬅️ Меню", reply_markup=get_main_keyboard(user_id), parse_mode="Markdown")
                    return
                
                if atk["cost"] > 0 and info["chifir"] < atk["cost"]:
                    await query.edit_message_text(f"❌ Нужно {atk['cost']}🍺, у тебя {info['chifir']}🍺")
                    return
                
                if atk["cost"] > 0:
                    remove_chifir(user_id, atk["cost"])
                
                # РАСЧЕТ УРОНА - ИСПРАВЛЕНО!
                damage = get_attack_damage(atk_type, info["zatochka"], boss_id)
                new_hp = max(0, progress["hp"] - damage)
                update_boss_hp(user_id, boss_id, new_hp)
                increment_daily_attacks(user_id)
                
                if new_hp <= 0:
                    add_xp(user_id, stats["reward_xp"], name)
                    add_chifir(user_id, stats["reward_chifir"])
                    
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    cursor.execute("UPDATE zeks SET boss_kills = boss_kills + 1 WHERE user_id = ?", (user_id,))
                    conn.commit()
                    conn.close()
                    
                    await query.edit_message_text(
                        f"🎉 *ПОБЕДА!*\n\n"
                        f"Твой урон: {damage}\n"
                        f"+{stats['reward_xp']}XP, +{stats['reward_chifir']}🍺",
                        parse_mode="Markdown")
                else:
                    counter = max(1, stats["damage"] // 15)
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    cursor.execute("SELECT total_xp FROM zeks WHERE user_id = ?", (user_id,))
                    total = cursor.fetchone()[0]
                    new_total = max(0, total - counter)
                    new_respect, new_xp = get_respect_from_xp(new_total)
                    cursor.execute("UPDATE zeks SET total_xp = ?, respect = ?, xp = ? WHERE user_id = ?", (new_total, new_respect, new_xp, user_id))
                    conn.commit()
                    conn.close()
                    
                    await query.edit_message_text(
                        f"⚔️ *Битва!*\n\n"
                        f"Твой урон: -{damage}❤️\n"
                        f"Босс контратакует: -{counter}XP\n"
                        f"❤️ HP босса: {new_hp}/{stats['hp']}",
                        parse_mode="Markdown")
                
                await query.message.reply_text("⬅️ К боссам", reply_markup=get_bosses_keyboard(user_id), parse_mode="Markdown")
            else:
                await query.edit_message_text("❌ Ошибка атаки!", reply_markup=get_back("bosses_menu"))
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=get_back("bosses_menu"))
    
    # Заработок
    elif data == "earn_menu":
        await query.edit_message_text("💰 *Заработок:*", reply_markup=get_earn_keyboard(), parse_mode="Markdown")
    
    elif data.startswith("earn_"):
        try:
            work = data.split("_")[1]
            w = EARN_METHODS[work]
            can, cd = can_work(user_id, work)
            if not can:
                await query.edit_message_text(f"⏳ Через {cd//60} мин", reply_markup=get_back("earn_menu"))
                return
            reward = random.randint(w["min"], w["max"])
            add_chifir(user_id, reward)
            set_work_cooldown(user_id, work)
            await query.edit_message_text(f"✅ +{reward}🍺", reply_markup=get_back("earn_menu"))
        except:
            await query.edit_message_text("❌ Ошибка!", reply_markup=get_back("earn_menu"))
    
    # Рейтинг
    elif data == "leaderboard":
        players, _ = get_all_players(0, 15)
        text = "🏆 *Топ игроков* 🏆\n\n"
        for i, p in enumerate(players[:15], 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            text += f"{medal} *{p['name']}* — {p['respect']} ур.\n"
        await query.edit_message_text(text, parse_mode="Markdown")
        await query.message.reply_text("⬅️ Меню", reply_markup=get_main_keyboard(user_id), parse_mode="Markdown")
    
    # Хавка
    elif data == "daily":
        can, cd = get_havka_cooldown(user_id)
        if not can:
            await query.edit_message_text(f"🍜 Через {cd//3600}ч {(cd%3600)//60}м", reply_markup=get_back("back_to_menu"))
            return
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT streak FROM daily_havka WHERE user_id = ?", (user_id,))
        r = cursor.fetchone()
        streak = (r[0] + 1) if r else 1
        bonus_xp = min(15 + (streak - 1) * 5, 100)
        bonus_chifir = 10 + (streak - 1) * 5
        set_havka_cooldown(user_id, streak)
        add_xp(user_id, bonus_xp, name)
        add_chifir(user_id, bonus_chifir)
        await query.edit_message_text(f"🍜 *Хавка!*\nДень {streak}: +{bonus_xp}XP, +{bonus_chifir}🍺", parse_mode="Markdown")
        await query.message.reply_text("⬅️ Меню", reply_markup=get_main_keyboard(user_id), parse_mode="Markdown")
    
    # Прогресс
    elif data == "progress":
        info = get_zek_info(user_id)
        text = f"📈 *Прогресс*\n🎯 Авторитет: {info['respect']}\n⚔️ Заточка: ур.{info['zatochka']} (+{info['zatochka_damage']})\n👑 Побед: {info['boss_kills']}\n🍺 Чифира: {info['chifir']}\n🤝 Друзей: {get_referrals_count(user_id)}\n⚡ Бонус: {int((1-get_user_cooldown_multiplier(user_id))*100)}%\n📊 До уровня: {info['xp_to_next']}XP"
        await query.edit_message_text(text, parse_mode="Markdown")
        await query.message.reply_text("⬅️ Меню", reply_markup=get_main_keyboard(user_id), parse_mode="Markdown")
    
    # Крыса
    elif data == "krysa":
        can, cd = can_krysa(user_id)
        if not can:
            await query.edit_message_text(f"🐀 Через {cd//60} мин", reply_markup=get_back("back_to_menu"))
            return
        success = random.random() < 0.7
        if success:
            reward = random.randint(20, 50)
            add_chifir(user_id, reward)
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("UPDATE zeks SET krysa_count = krysa_count + 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            set_krysa_cooldown(user_id)
            await query.edit_message_text(f"🐀 *Успех!* +{reward}🍺", parse_mode="Markdown")
        else:
            penalty = random.randint(10, 30)
            remove_chifir(user_id, penalty)
            set_krysa_cooldown(user_id)
            await query.edit_message_text(f"😰 *Провал!* -{penalty}🍺", parse_mode="Markdown")
        await query.message.reply_text("⬅️ Меню", reply_markup=get_main_keyboard(user_id), parse_mode="Markdown")
    
    # Подарок
    elif data == "gift_start":
        context.user_data['gift_step'] = 'amount'
        await query.edit_message_text("🎁 *Сколько подарить? (макс 50)*", reply_markup=get_gift_amount_keyboard(), parse_mode="Markdown")
    
    elif data.startswith("gift_") and (data.startswith("gift_10") or data.startswith("gift_20") or data.startswith("gift_30") or data.startswith("gift_50")):
        amount = int(data.split("_")[1])
        context.user_data['gift_amount'] = amount
        context.user_data['gift_step'] = 'target'
        await query.edit_message_text(f"🎁 Введи ID или имя игрока для подарка {amount}🍺:", reply_markup=get_back("gift_start"), parse_mode="Markdown")
    
    # Обратная связь
    elif data == "feedback_start":
        context.user_data['feedback_step'] = 'text'
        await query.edit_message_text("💬 *Напиши своё сообщение* (до 1000 символов):", reply_markup=get_back("back_to_menu"), parse_mode="Markdown")
    
    # Реферал
    elif data == "referral":
        bot = context.bot.username
        link = f"https://t.me/{bot}?start={user_id}"
        count = get_referrals_count(user_id)
        bonus = int((1 - get_user_cooldown_multiplier(user_id)) * 100)
        await query.edit_message_text(
            f"🤝 *Пригласи друга в Тюрягу!*\n\n"
            f"📎 *Твоя реферальная ссылка:*\n`{link}`\n\n"
            f"👥 Приглашено друзей: {count}\n"
            f"⚡ Бонус скорости: {bonus}%",
            reply_markup=get_back("back_to_menu"), parse_mode="Markdown")
    
    # ===== АДМИН-ПАНЕЛЬ =====
    elif data == "admin_panel" and is_admin(user_id):
        await query.edit_message_text("👑 *Админ-панель*", reply_markup=get_admin_keyboard(), parse_mode="Markdown")
    
    elif data == "admin_stats" and is_admin(user_id):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM zeks")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT AVG(respect) FROM zeks")
        avg = cursor.fetchone()[0] or 0
        cursor.execute("SELECT SUM(boss_kills) FROM zeks")
        kills = cursor.fetchone()[0] or 0
        conn.close()
        await query.edit_message_text(f"📊 *Статистика*\n\n👥 Игроков: {total}\n📈 Средний авторитет: {avg:.1f}\n👑 Побед: {kills}", reply_markup=get_back("admin_panel"), parse_mode="Markdown")
    
    elif data == "admin_daily" and is_admin(user_id):
        await query.edit_message_text(f"📈 *Статистика за {datetime.now().strftime('%d.%m.%Y')}*", reply_markup=get_back("admin_panel"), parse_mode="Markdown")
    
    elif data == "admin_list" and is_admin(user_id):
        page = context.user_data.get('admin_page', 0)
        players, total = get_all_players(page, 15)
        pages = (total + 14) // 15
        if not players:
            await query.edit_message_text("📋 Нет игроков", reply_markup=get_back("admin_panel"))
            return
        text = f"📋 *Игроки (стр.{page+1}/{pages})*\n\n"
        for i, p in enumerate(players, page*15+1):
            text += f"{i}. *{p['name']}* — {p['respect']} ур. (🔪{p['zatochka']}, 🍺{p['chifir']})\n"
        await query.edit_message_text(text, reply_markup=get_player_list_keyboard(page, pages), parse_mode="Markdown")
    
    elif data.startswith("admin_page_"):
        page = int(data.split("_")[2])
        context.user_data['admin_page'] = page
        players, total = get_all_players(page, 15)
        pages = (total + 14) // 15
        text = f"📋 *Игроки (стр.{page+1}/{pages})*\n\n"
        for i, p in enumerate(players, page*15+1):
            text += f"{i}. *{p['name']}* — {p['respect']} ур.\n"
        await query.edit_message_text(text, reply_markup=get_player_list_keyboard(page, pages), parse_mode="Markdown")
    
    elif data.startswith("admin_view_") and is_admin(user_id):
        target_id = int(data.split("_")[2])
        info = get_zek_info(target_id)
        if info:
            rank = get_player_rank(target_id)
            text = f"👤 *Досье*\n\n🆔 ID: {target_id}\n📛 {info['name']}\n📊 Авторитет: {info['respect']} (#{rank})\n⚔️ Заточка: ур.{info['zatochka']} (+{info['zatochka_damage']})\n🍺 Чифира: {info['chifir']}\n👑 Побед: {info['boss_kills']}\n🐀 Крысят: {info['krysa_count']}\n📈 Опыта: {info['total_xp']}"
            await query.edit_message_text(text, reply_markup=get_player_detail_keyboard(target_id), parse_mode="Markdown")
    
    elif data == "admin_find" and is_admin(user_id):
        context.user_data['admin_action'] = 'find'
        await query.edit_message_text("🔍 Введи ID или имя игрока:", reply_markup=get_back("admin_panel"), parse_mode="Markdown")
    
    elif data == "admin_gift_all" and is_admin(user_id):
        context.user_data['admin_action'] = 'gift_all'
        await query.edit_message_text("🎁 Введи количество XP для всех игроков:", reply_markup=get_back("admin_panel"), parse_mode="Markdown")
    
    elif data == "admin_broadcast" and is_admin(user_id):
        context.user_data['admin_action'] = 'broadcast'
        await query.edit_message_text("📤 Введи текст рассылки:", reply_markup=get_back("admin_panel"), parse_mode="Markdown")
    
    elif data == "admin_reset_attacks" and is_admin(user_id):
        reset_daily_attacks()
        await query.edit_message_text("✅ Счётчик атак сброшен!", reply_markup=get_back("admin_panel"))
    
    elif data == "admin_max_out" and is_admin(user_id):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE zeks SET respect = 100, zatochka = 100, chifir = 999999, is_admin_hidden = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await query.edit_message_text("✅ Админ прокачан!", reply_markup=get_back("admin_panel"))
    
    elif data == "admin_backup" and is_admin(user_id):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        shutil.copy(DB_NAME, f"{BACKUP_DIR}/backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        await query.edit_message_text("✅ Бэкап создан!", reply_markup=get_back("admin_panel"))
    
    elif data == "admin_clear_fights" and is_admin(user_id):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM boss_fights")
        cursor.execute("DELETE FROM parties")
        conn.commit()
        conn.close()
        await query.edit_message_text("✅ Битвы очищены!", reply_markup=get_back("admin_panel"))
    
    elif data.startswith("admin_raise_") and is_admin(user_id):
        target_id = int(data.split("_")[2])
        info = get_zek_info(target_id)
        if info:
            add_xp(target_id, get_xp_for_respect(info['respect']), "admin")
            await query.edit_message_text(f"✅ Авторитет {target_id} повышен до {info['respect'] + 1}", reply_markup=get_back("admin_list"))
    
    elif data.startswith("admin_gift_") and is_admin(user_id):
        target_id = int(data.split("_")[2])
        context.user_data['admin_action'] = f'gift_{target_id}'
        await query.edit_message_text(f"🎁 Введи XP для {target_id}:", reply_markup=get_back("admin_list"))
    
    elif data.startswith("admin_gold_") and is_admin(user_id):
        target_id = int(data.split("_")[2])
        context.user_data['admin_action'] = f'gold_{target_id}'
        await query.edit_message_text(f"💰 Введи чифир для {target_id}:", reply_markup=get_back("admin_list"))
    
    elif data.startswith("admin_weapon_") and is_admin(user_id):
        target_id = int(data.split("_")[2])
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE zeks SET zatochka = zatochka + 1 WHERE user_id = ?", (target_id,))
        conn.commit()
        conn.close()
        await query.edit_message_text(f"✅ Заточка {target_id} повышена!", reply_markup=get_back("admin_list"))
    
    elif data.startswith("admin_ban_") and is_admin(user_id):
        target_id = int(data.split("_")[2])
        context.user_data['admin_action'] = f'ban_{target_id}'
        await query.edit_message_text(f"🚫 Введи часы бана:", reply_markup=get_back("admin_list"))
    
    elif data.startswith("admin_unban_") and is_admin(user_id):
        target_id = int(data.split("_")[2])
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE zeks SET is_banned = 0, ban_until = NULL WHERE user_id = ?", (target_id,))
        conn.commit()
        conn.close()
        await query.edit_message_text(f"✅ Игрок {target_id} разбанен!", reply_markup=get_back("admin_list"))
    
    elif data == "back_to_menu":
        await query.edit_message_text("⛓️ *Главное меню*", reply_markup=get_main_keyboard(user_id), parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name or f"zek_{user_id}"
    text = update.message.text
    
    # Подарок - ввод получателя
    if context.user_data.get('gift_step') == 'target':
        amount = context.user_data.get('gift_amount')
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        if text.isdigit():
            cursor.execute("SELECT user_id, name FROM zeks WHERE user_id = ?", (int(text),))
        else:
            cursor.execute("SELECT user_id, name FROM zeks WHERE name LIKE ?", (f"%{text}%",))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            await update.message.reply_text("❌ Игрок не найден!", reply_markup=get_back("gift_start"))
            context.user_data['gift_step'] = None
            return
        
        target_id, target_name = result
        if target_id == user_id:
            await update.message.reply_text("❌ Нельзя дарить себе!", reply_markup=get_back("gift_start"))
            context.user_data['gift_step'] = None
            return
        
        if send_gift(user_id, target_id, amount):
            await update.message.reply_text(f"✅ Ты подарил {amount}🍺 игроку {target_name}!", reply_markup=get_main_keyboard(user_id))
            try:
                await context.bot.send_message(target_id, f"🎁 *Подарок!*\n\nИгрок *{name}* подарил тебе {amount}🍺 чифира!", parse_mode="Markdown")
            except:
                pass
        else:
            info = get_zek_info(user_id)
            await update.message.reply_text(f"❌ Не хватает чифира! У тебя {info['chifir']}🍺", reply_markup=get_back("gift_start"))
        
        context.user_data['gift_step'] = None
        return
    
    # Обратная связь
    if context.user_data.get('feedback_step') == 'text':
        if len(text) > 1000:
            await update.message.reply_text("❌ Слишком длинно! Максимум 1000 символов.", reply_markup=get_back("back_to_menu"))
            return
        add_feedback(user_id, name, text)
        context.user_data['feedback_step'] = None
        await update.message.reply_text(
            "✅ *Спасибо за обратную связь!*\n\nТвоё сообщение отправлено администратору.",
            reply_markup=get_main_keyboard(user_id), parse_mode="Markdown")
        return
    
    # Админ-действия
    action = context.user_data.get('admin_action')
    if action and is_admin(user_id):
        if action == 'find':
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            if text.isdigit():
                cursor.execute("SELECT user_id, name, respect, chifir, zatochka, boss_kills FROM zeks WHERE user_id = ?", (int(text),))
            else:
                cursor.execute("SELECT user_id, name, respect, chifir, zatochka, boss_kills FROM zeks WHERE name LIKE ?", (f"%{text}%",))
            player = cursor.fetchone()
            conn.close()
            if player:
                target_id, name, respect, chifir, zatochka, kills = player
                await update.message.reply_text(f"👤 *Найден*\n\nID: {target_id}\nИмя: {name}\nАвторитет: {respect}\nЗаточка: {zatochka}\nЧифир: {chifir}\nПобед: {kills}", reply_markup=get_player_detail_keyboard(target_id), parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ Игрок не найден", reply_markup=get_back("admin_panel"))
            context.user_data['admin_action'] = None
        
        elif action == 'gift_all':
            try:
                xp = int(text)
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM zeks")
                players = cursor.fetchall()
                conn.close()
                count = 0
                for pid in players:
                    add_xp(pid[0], xp, "admin")
                    count += 1
                    try:
                        await context.bot.send_message(pid[0], f"🎁 *Подарок от администрации!* +{xp}XP", parse_mode="Markdown")
                    except:
                        pass
                await update.message.reply_text(f"✅ {count} игроков получили +{xp}XP!", reply_markup=get_back("admin_panel"))
            except:
                await update.message.reply_text("❌ Введи число!", reply_markup=get_back("admin_panel"))
            context.user_data['admin_action'] = None
        
        elif action == 'broadcast':
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM zeks")
            players = cursor.fetchall()
            conn.close()
            count = 0
            for pid in players:
                try:
                    await context.bot.send_message(pid[0], f"📢 *Рассылка*\n\n{text}", parse_mode="Markdown")
                    count += 1
                except:
                    pass
            await update.message.reply_text(f"✅ Отправлено {count} игрокам!", reply_markup=get_back("admin_panel"))
            context.user_data['admin_action'] = None
        
        elif action.startswith('gift_'):
            target_id = int(action.split('_')[1])
            try:
                xp = int(text)
                add_xp(target_id, xp, "admin")
                await update.message.reply_text(f"✅ +{xp}XP игроку {target_id}!", reply_markup=get_back("admin_list"))
                try:
                    await context.bot.send_message(target_id, f"🎁 *Награда!* +{xp}XP от администрации!", parse_mode="Markdown")
                except:
                    pass
            except:
                await update.message.reply_text("❌ Введи число!", reply_markup=get_back("admin_list"))
            context.user_data['admin_action'] = None
        
        elif action.startswith('gold_'):
            target_id = int(action.split('_')[1])
            try:
                gold = int(text)
                add_chifir(target_id, gold)
                await update.message.reply_text(f"✅ +{gold}🍺 игроку {target_id}!", reply_markup=get_back("admin_list"))
                try:
                    await context.bot.send_message(target_id, f"💰 *Награда!* +{gold}🍺 от администрации!", parse_mode="Markdown")
                except:
                    pass
            except:
                await update.message.reply_text("❌ Введи число!", reply_markup=get_back("admin_list"))
            context.user_data['admin_action'] = None
        
        elif action.startswith('ban_'):
            target_id = int(action.split('_')[1])
            try:
                hours = int(text)
                if hours == 0:
                    ban_until = datetime.now() + timedelta(days=3650)
                    ban_text = "навсегда"
                else:
                    ban_until = datetime.now() + timedelta(hours=hours)
                    ban_text = f"{hours} ч"
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("UPDATE zeks SET is_banned = 1, ban_until = ? WHERE user_id = ?", (ban_until.isoformat(), target_id))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ Игрок {target_id} забанен на {ban_text}!", reply_markup=get_back("admin_list"))
                try:
                    await context.bot.send_message(target_id, f"🚫 *Вы забанены!*\n\nСрок: {ban_text}", parse_mode="Markdown")
                except:
                    pass
            except:
                await update.message.reply_text("❌ Введи число!", reply_markup=get_back("admin_list"))
            context.user_data['admin_action'] = None
    
    # Обычная активность
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM zeks WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result and result[0] == 1:
        return
    add_xp(user_id, 1, name)

async def check_expired_parties(context: ContextTypes.DEFAULT_TYPE):
    cleanup_expired_parties(context)

def main():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    # Инициализация БД
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Создание таблиц если их нет
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS zeks (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            respect INTEGER DEFAULT 1,
            xp INTEGER DEFAULT 0,
            total_xp INTEGER DEFAULT 0,
            zatochka INTEGER DEFAULT 1,
            chifir INTEGER DEFAULT 50,
            boss_kills INTEGER DEFAULT 0,
            krysa_count INTEGER DEFAULT 0,
            last_krysa TEXT,
            last_active TEXT,
            is_banned BOOLEAN DEFAULT 0,
            ban_until TEXT,
            is_admin_hidden BOOLEAN DEFAULT 0,
            created_at TEXT,
            daily_attacks INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS boss_fights (
            user_id INTEGER,
            boss_id INTEGER,
            current_hp INTEGER,
            last_fight TEXT,
            party_id INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, boss_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS parties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            boss_id INTEGER,
            boss_current_hp INTEGER,
            boss_max_hp INTEGER,
            members TEXT,
            leader_id INTEGER,
            created_at TEXT,
            expires_at TEXT,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_havka (
            user_id INTEGER PRIMARY KEY,
            last_claim TEXT,
            streak INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS work_cooldown (
            user_id INTEGER,
            work_type TEXT,
            last_used TEXT,
            PRIMARY KEY (user_id, work_type)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS referrals (
            user_id INTEGER PRIMARY KEY,
            referred_by INTEGER,
            bonus_claimed BOOLEAN DEFAULT 0,
            referred_at TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attack_cooldown (
            user_id INTEGER,
            boss_id INTEGER,
            last_attack TEXT,
            PRIMARY KEY (user_id, boss_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            message TEXT,
            created_at TEXT,
            is_read BOOLEAN DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            new_players INTEGER DEFAULT 0,
            active_players INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER,
            to_user INTEGER,
            amount INTEGER,
            created_at TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    
    app = Application.builder().token(TOKEN).build()
    
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(check_expired_parties, interval=300, first=10)
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("⛓️ Тюряга бот запущен!")
    print(f"👑 Админы: {ADMIN_IDS}")
    app.run_polling()

if __name__ == "__main__":
    main()
