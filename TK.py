import requests
from bs4 import BeautifulSoup
import sqlite3
import time
import logging
import os
from datetime import datetime
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ====== НАСТРОЙКИ ======
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
URL_TO_MONITOR = "https://www.kino-teatr.ru/mourn/y2025/m12/"
CHECK_INTERVAL = 300  # 5 минут в секундах
DATABASE_FILE = "profiles.db"
# =======================

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class ProfileMonitor:
    def __init__(self):
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных для хранения отслеживаемых профилей"""
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tracked_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_url TEXT UNIQUE,
                name TEXT,
                photo_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("База данных инициализирована")
    
    def extract_profiles(self):
        """
        Парсит страницу и извлекает информацию о профилях актеров
        """
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(URL_TO_MONITOR, headers=headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            profiles = []
            
            # Ищем контейнеры с профилями актеров
            # Нужно адаптировать селекторы под конкретную структуру страницы
            profile_blocks = soup.find_all('div', class_=['actor-item', 'person-item'])
            
            if not profile_blocks:
                # Альтернативный поиск - ищем любые карточки с фото и ссылками
                profile_blocks = soup.find_all('div', class_=lambda x: x and ('item' in x or 'card' in x))
            
            for block in profile_blocks:
                try:
                    # Извлекаем ссылку на профиль
                    link_tag = block.find('a')
                    if not link_tag or not link_tag.get('href'):
                        continue
                    
                    profile_url = link_tag['href']
                    if not profile_url.startswith('http'):
                        profile_url = 'https://www.kino-teatr.ru' + profile_url
                    
                    # Извлекаем имя
                    name_tag = block.find(['h3', 'h4', 'div'], class_=lambda x: x and ('name' in x or 'title' in x))
                    name = name_tag.get_text().strip() if name_tag else "Неизвестно"
                    
                    # Извлекаем фото
                    img_tag = block.find('img')
                    photo_url = img_tag['src'] if img_tag and img_tag.get('src') else None
                    if photo_url and not photo_url.startswith('http'):
                        photo_url = 'https://www.kino-teatr.ru' + photo_url
                    
                    profiles.append({
                        'url': profile_url,
                        'name': name,
                        'photo': photo_url
                    })
                    
                except Exception as e:
                    logger.warning(f"Ошибка при парсинге блока профиля: {e}")
                    continue
            
            logger.info(f"Найдено профилей на странице: {len(profiles)}")
            return profiles
            
        except Exception as e:
            logger.error(f"Ошибка при парсинге страницы: {e}")
            return []
    
    def save_profile(self, profile):
        """Сохраняет профиль в базу данных если его еще нет"""
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO tracked_profiles (profile_url, name, photo_url)
                VALUES (?, ?, ?)
            ''', (profile['url'], profile['name'], profile['photo']))
            
            conn.commit()
            is_new = cursor.rowcount > 0
            return is_new
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении профиля: {e}")
            return False
        finally:
            conn.close()
    
    def get_new_profiles(self):
        """Проверяет наличие новых профилей"""
        current_profiles = self.extract_profiles()
        new_profiles = []
        
        for profile in current_profiles:
            if self.save_profile(profile):
                new_profiles.append(profile)
        
        return new_profiles

async def send_notification(bot, new_profiles):
    """Отправляет уведомления о новых профилях"""
    for profile in new_profiles:
        try:
            message = f"🎭 Новый профиль актера:\n\n👤 Имя: {profile['name']}\n🔗 Ссылка: {profile['url']}"
            
            if profile['photo']:
                await bot.send_photo(
                    chat_id=TELEGRAM_CHAT_ID,
                    photo=profile['photo'],
                    caption=message
                )
            else:
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=message
                )
            
            logger.info(f"Отправлено уведомление о профиле: {profile['name']}")
            
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления: {e}")
            # Пытаемся отправить без фото
            try:
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=f"🎭 Новый профиль (ошибка загрузки фото):\n👤 {profile['name']}\n🔗 {profile['url']}"
                )
            except Exception as e2:
                logger.error(f"Не удалось отправить даже текстовое уведомление: {e2}")

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /ping для проверки работы бота"""
    check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Проверяем подключение к базе данных
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tracked_profiles")
        count = cursor.fetchone()[0]
        conn.close()
        db_status = "✅ Работает"
    except Exception as e:
        db_status = f"❌ Ошибка: {e}"
    
    # Проверяем доступность целевой страницы
    try:
        response = requests.get(URL_TO_MONITOR, timeout=10)
        page_status = "✅ Доступна" if response.status_code == 200 else f"❌ Код: {response.status_code}"
    except Exception as e:
        page_status = f"❌ Ошибка: {e}"
    
    status_message = (
        f"🤖 Статус бота:\n"
        f"🕐 Время проверки: {check_time}\n"
        f"📊 Профилей в базе: {count}\n"
        f"🗄️ База данных: {db_status}\n"
        f"🌐 Целевая страница: {page_status}\n"
        f"⏰ Следующая проверка через 5 минут"
    )
    
    await update.message.reply_text(status_message)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome_message = (
        "👋 Привет! Я бот для отслеживания новых профилей актеров.\n\n"
        "📊 Доступные команды:\n"
        "/ping - Проверить статус бота\n"
        "/start - Показать это сообщение\n\n"
        "Я буду автоматически проверять страницу каждые 5 минут и присылать уведомления о новых профилях."
    )
    await update.message.reply_text(welcome_message)

async def monitor_task(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая задача для мониторинга"""
    bot = context.bot
    monitor = ProfileMonitor()
    
    logger.info("Запуск проверки новых профилей...")
    new_profiles = monitor.get_new_profiles()
    
    if new_profiles:
        logger.info(f"Найдено новых профилей: {len(new_profiles)}")
        await send_notification(bot, new_profiles)
    else:
        logger.info("Новых профилей не найдено")

def main():
    """Основная функция запуска бота"""
    # Проверяем наличие необходимых переменных окружения
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Не установлены TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID")
        return
    
    # Создаем приложение
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("ping", ping_command))
    
    # Настраиваем периодическую задачу мониторинга
    job_queue = application.job_queue
    job_queue.run_repeating(monitor_task, interval=CHECK_INTERVAL, first=10)
    
    # Запускаем бота
    logger.info("Бот запущен и начал мониторинг...")
    application.run_polling()

if __name__ == '__main__':

    main()
