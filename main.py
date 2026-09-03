# main.py
import os
import asyncio
from aiogram import Bot, Dispatcher
from aiohttp import web

from piarflow_sponsor import sponsor_router, init_sponsor_db, cached_sponsors, check_subscriptions_on_service
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

async def health_check(request):
    return web.Response(text="Bot is running")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.getenv('PORT', 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server started on port {port}")

# Фоновая задача для периодической проверки спонсоров
async def sponsor_checker_task(bot: Bot):
    from piarflow_sponsor import db_fetchall
    
    while True:
        try:
            await asyncio.sleep(300)  # 5 минут = 300 секунд
            
            print("Запуск проверки спонсоров...")
            
            # Получаем всех уникальных пользователей
            users = await db_fetchall("SELECT DISTINCT user_id FROM sponsor_tasks")
            
            for user_row in users:
                user_id = user_row["user_id"]
                
                # Создаём фейковый объект пользователя
                class FakeUser:
                    def __init__(self, uid):
                        self.id = uid
                
                user = FakeUser(user_id)
                
                # Получаем спонсоров
                sponsors = await cached_sponsors(user, force_refresh=True)
                
                if not sponsors:
                    continue
                
                # Проверяем подписки
                all_subscribed = await check_subscriptions_on_service("piarflow", user, sponsors)
                
                if not all_subscribed:
                    text = "⏰ <b>Напоминание о подписке на спонсоров</b>\n\n"
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
                            text="✅ Проверить подписку",
                            callback_data="check_piarflow_subscription"
                        )
                    ])
                    
                    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
                    
                    try:
                        await bot.send_message(user_id, text, reply_markup=markup, parse_mode="HTML")
                    except Exception as e:
                        print(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
            
            print("Проверка спонсоров завершена")
        
        except Exception as e:
            print(f"Ошибка в sponsor_checker_task: {e}")
            await asyncio.sleep(60)

async def main():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise ValueError("BOT_TOKEN не установлен в переменных окружения")
    
    bot = Bot(token=bot_token)
    dp = Dispatcher()
    
    print("Инициализация базы данных...")
    await init_sponsor_db()
    
    dp.include_router(sponsor_router)
    
    print("Запуск веб-сервера...")
    await start_web_server()
    
    print("Запуск фоновой задачи проверки спонсоров...")
    asyncio.create_task(sponsor_checker_task(bot))
    
    print("Бот запущен и ожидает сообщения...")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
