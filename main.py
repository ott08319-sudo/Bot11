# main.py
import os
import asyncio
from aiogram import Bot, Dispatcher
from aiohttp import web

from piarflow_sponsor import sponsor_router, init_sponsor_db

# Веб-сервер для Render
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
    
    print("Бот запущен и ожидает сообщения...")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
