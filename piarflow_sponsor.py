# piarflow_sponsor.py
import os
import json
import time
import aiohttp
import aiosqlite
from datetime import datetime
from typing import Any

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

SERVICE_CONFIG = {
    "piarflow": {
        "base_url": "https://piarflow.com/api/v1",
        "key_env": "PIARFLOW_API_KEY",
    },
}

SPONSOR_CACHE_SECONDS = 300
DB_PATH = os.getenv("DB_PATH", "bot.db")

sponsor_router = Router()

# ============ HTTP КЛИЕНТ ============

async def http_json(method: str, url: str, params: dict = None, json_body: dict = None) -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            if method == "GET":
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    result = await resp.json()
                    print(f"PiarFlow GET response: {result}")
                    return result
            elif method == "POST":
                async with session.post(url, json=json_body, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    result = await resp.json()
                    print(f"PiarFlow POST response: {result}")
                    return result
    except Exception as e:
        print(f"HTTP error: {e}")
        return {}
    return {}

# ============ БАЗА ДАННЫХ ============

async def db_execute(query: str, params: tuple = ()) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(query, params)
        await db.commit()

async def db_fetchone(query: str, params: tuple = ()) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def db_fetchall(query: str, params: tuple = ()) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def init_sponsor_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sponsor_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sponsor_tasks (
                user_id INTEGER,
                service TEXT,
                offer_id TEXT,
                link TEXT,
                status TEXT DEFAULT 'unsubscribed',
                created_at TEXT,
                PRIMARY KEY (user_id, service, offer_id, link)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_sponsor_shown (
                user_id INTEGER PRIMARY KEY,
                last_shown REAL NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sponsor_cache_expires ON sponsor_cache(expires_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sponsor_tasks_user ON sponsor_tasks(user_id)")
        await db.commit()

# ============ УТИЛИТЫ ============

def status_name(status: Any, subscribed: Any = None) -> str:
    if subscribed is True or str(status).lower() in ("subscribed", "active", "success"):
        return "subscribed"
    return "unsubscribed"

def utc_now() -> str:
    return datetime.utcnow().isoformat()

# ============ РАБОТА СО СПОНСОРАМИ ============

def normalize_offers(service: str, payload: Any) -> tuple[list[dict], str | None]:
    tasks = []
    sponsors_data = payload.get("sponsors") or payload.get("channels") or []
    
    for i, item in enumerate(sponsors_data):
        if not isinstance(item, dict):
            continue
        
        link = item.get("link") or item.get("url") or item.get("invite_link") or item.get("channel_link")
        if not link:
            continue
        
        if link.startswith(("t.me/", "telegram.me/")):
            link = f"https://{link}"
        
        tasks.append({
            "service": service,
            "id": str(item.get("id") or item.get("channel_id") or i + 1),
            "link": link,
            "status": status_name(item.get("status"), item.get("subscribed"))
        })
    
    return tasks, str(payload.get("status", "")).lower()

async def fetch_sponsors_from_service(service: str, user: Any) -> list[dict]:
    api_key = os.getenv(SERVICE_CONFIG[service]["key_env"], "").strip()
    print(f"API Key present: {bool(api_key)}")
    
    if not api_key:
        return []
    
    if service == "piarflow":
        payload = await http_json(
            "GET",
            f"{SERVICE_CONFIG['piarflow']['base_url']}/sponsors",
            params={"api_key": api_key, "telegram_id": user.id}
        )
        return normalize_offers(service, payload)[0]
    
    return []

async def check_subscriptions_on_service(service: str, user: Any, tasks: list[dict]) -> bool:
    api_key = os.getenv(SERVICE_CONFIG[service]["key_env"], "").strip()
    if not api_key:
        return True
    
    if service == "piarflow":
        payload = await http_json(
            "POST",
            f"{SERVICE_CONFIG['piarflow']['base_url']}/check",
            json_body={"api_key": api_key, "telegram_id": user.id}
        )
        items = payload.get("sponsors") or payload.get("channels") or []
        return all(
            status_name(i.get("status"), i.get("subscribed")) == "subscribed"
            for i in items if isinstance(i, dict)
        )
    
    return True

async def cached_sponsors(user: Any, force_refresh: bool = False) -> list[dict]:
    tasks = []
    
    for service in ["piarflow"]:
        cache_key = f"{service}:{user.id}"
        cached = await db_fetchone(
            "SELECT payload, expires_at FROM sponsor_cache WHERE cache_key = ?",
            (cache_key,)
        )
        
        if not force_refresh and cached and float(cached["expires_at"]) > time.time():
            tasks.extend(json.loads(cached["payload"]))
        else:
            new_tasks = await fetch_sponsors_from_service(service, user)
            if new_tasks:
                await db_execute(
                    "INSERT OR REPLACE INTO sponsor_cache(cache_key, payload, expires_at) VALUES (?,?,?)",
                    (cache_key, json.dumps(new_tasks), time.time() + SPONSOR_CACHE_SECONDS)
                )
                tasks.extend(new_tasks)
    
    if tasks:
        await db_execute("DELETE FROM sponsor_tasks WHERE user_id = ?", (user.id,))
        for t in tasks:
            await db_execute(
                "INSERT OR REPLACE INTO sponsor_tasks(user_id, service, offer_id, link, status, created_at) VALUES (?,?,?,?,?,?)",
                (user.id, t["service"], t["id"], t["link"], t["status"], utc_now())
            )
    
    return tasks

async def should_show_sponsors(user_id: int) -> bool:
    """Проверяем нужно ли показать спонсоров (каждые 5 минут)"""
    last_shown = await db_fetchone(
        "SELECT last_shown FROM user_sponsor_shown WHERE user_id = ?",
        (user_id,)
    )
    
    if not last_shown:
        return True
    
    return time.time() - float(last_shown["last_shown"]) >= 300  # 5 минут

async def mark_sponsors_shown(user_id: int):
    """Отмечаем что показали спонсоров"""
    await db_execute(
        "INSERT OR REPLACE INTO user_sponsor_shown(user_id, last_shown) VALUES (?,?)",
        (user_id, time.time())
    )

async def show_sponsors_to_user(message: Message):
    """Показать спонсоров пользователю"""
    user = message.from_user
    
    sponsors = await cached_sponsors(user, force_refresh=True)
    
    if not sponsors:
        print(f"Нет спонсоров для пользователя {user.id}")
        return False
    
    # Проверяем подписки
    all_subscribed = await check_subscriptions_on_service("piarflow", user, sponsors)
    
    if all_subscribed:
        print(f"Пользователь {user.id} уже подписан на всех спонсоров")
        return False
    
    text = "📢 <b>Подпишитесь на спонсоров</b>\n\n"
    text += "Для продолжения работы с ботом подпишитесь на каналы:\n\n"
    
    keyboard = []
    for idx, sponsor in enumerate(sponsors, 1):
        status_emoji = "✅" if sponsor["status"] == "subscribed" else "❌"
        text += f"{idx}. {status_emoji} Канал #{idx}\n"
        
        keyboard.append([
            InlineKeyboardButton(
                text=f"📢 Канал #{idx}",
                url=sponsor["link"]
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton(
            text="✅ Проверить подписку",
            callback_data="check_piarflow_subscription"
        )
    ])
    
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await message.answer(text, reply_markup=markup, parse_mode="HTML")
    await mark_sponsors_shown(user.id)
    return True

# ============ ОБРАБОТЧИКИ ============

@sponsor_router.message(Command("sponsors"))
async def cmd_sponsors(message: Message):
    await show_sponsors_to_user(message)

@sponsor_router.callback_query(F.data == "check_piarflow_subscription")
async def callback_check_subscription(callback: CallbackQuery):
    await callback.answer("⏳ Проверяю подписки...", show_alert=False)
    
    user = callback.from_user
    
    sponsors = await cached_sponsors(user, force_refresh=True)
    
    if not sponsors:
        await callback.message.answer("❌ Не найдено спонсоров для проверки.")
        return
    
    all_subscribed = await check_subscriptions_on_service("piarflow", user, sponsors)
    
    if all_subscribed:
        await callback.message.answer(
            "✅ <b>Отлично!</b>\n\n"
            "Вы подписаны на все каналы спонсоров.",
            parse_mode="HTML"
        )
    else:
        text = "⚠️ <b>Не все подписки активны</b>\n\n"
        text += "Пожалуйста, подпишитесь на все каналы:\n\n"
        
        keyboard = []
        for idx, sponsor in enumerate(sponsors, 1):
            status_emoji = "✅" if sponsor["status"] == "subscribed" else "❌"
            text += f"{idx}. {status_emoji} Канал #{idx}\n"
            
            if sponsor["status"] != "subscribed":
                keyboard.append([
                    InlineKeyboardButton(
                        text=f"📢 Подписаться на #{idx}",
                        url=sponsor["link"]
                    )
                ])
        
        keyboard.append([
            InlineKeyboardButton(
                text="🔄 Проверить снова",
                callback_data="check_piarflow_subscription"
            )
        ])
        
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")

# ============ MIDDLEWARE ============

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from typing import Callable, Dict, Any, Awaitable

class SponsorMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        
        # Пропускаем callback от проверки спонсоров
        if hasattr(event, 'data') and event.data == "check_piarflow_subscription":
            return await handler(event, data)
        
        if user and hasattr(event, 'text'):
            # Проверяем нужно ли показать спонсоров
            if await should_show_sponsors(user.id):
                shown = await show_sponsors_to_user(event)
                if shown:
                    # Блокируем дальнейшее выполнение
                    return
        
        return await handler(event, data)
