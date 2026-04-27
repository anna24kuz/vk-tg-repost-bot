import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from vk_api import VkApi, VkApiError
import time
import json
import os
import requests
import sqlite3
import asyncio

# Укажите токен вашего бота-посредника
BOT_TOKEN = '8516690865:AAHAQfxmWGone0UX5K9Pdckp0ebaAG7aANY'  # Замените на ваш токен

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

USER_DATA_FILE = 'user_data.json'
DB_FILE = 'user_data.db'

class UserConfig:
    def __init__(self):
        self.init_db()

    def init_db(self):
        """Initialize the database and create tables if they don't exist"""
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Create users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                data TEXT
            )
        ''')
        
        conn.commit()
        conn.close()

    def get_user_data(self, user_id: int) -> dict:
        """Get user data from database"""
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute('SELECT data FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        
        conn.close()
        
        if result:
            return json.loads(result[0])
        return {}

    def update_user_data(self, user_id: int, key: str, value):
        """Update user data in database"""
        # Get current user data
        user_data = self.get_user_data(user_id)
        user_data[key] = value
        
        # Save updated data
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, data) 
            VALUES (?, ?)
        ''', (user_id, json.dumps(user_data)))
        
        conn.commit()
        conn.close()

    def get_bots(self, user_id: int) -> list:
        """Get all bot configurations for a user"""
        user_data = self.get_user_data(user_id)
        return user_data.get('bots', [])

    def get_bot(self, user_id: int, bot_index: int) -> dict:
        """Get specific bot configuration by index (0-2)"""
        bots = self.get_bots(user_id)
        if 0 <= bot_index < len(bots):
            return bots[bot_index]
        return {}

    def update_bot(self, user_id: int, bot_index: int, bot_data: dict):
        """Update specific bot configuration"""
        # Get current user data
        user_data = self.get_user_data(user_id)
        
        # Initialize bots list if it doesn't exist
        if 'bots' not in user_data:
            user_data['bots'] = [{}, {}, {}]  # Initialize with 3 empty slots
        
        # Ensure we have enough slots
        while len(user_data['bots']) <= bot_index:
            user_data['bots'].append({})
            
        user_data['bots'][bot_index] = bot_data
        
        # Save updated data
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, data) 
            VALUES (?, ?)
        ''', (user_id, json.dumps(user_data)))
        
        conn.commit()
        conn.close()

    def delete_bot(self, user_id: int, bot_index: int):
        """Delete specific bot configuration"""
        # Get current user data
        user_data = self.get_user_data(user_id)
        
        # Check if user has bots data
        if 'bots' in user_data:
            if 0 <= bot_index < len(user_data['bots']):
                user_data['bots'][bot_index] = {}
                
                # Save updated data
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT OR REPLACE INTO users (user_id, data) 
                    VALUES (?, ?)
                ''', (user_id, json.dumps(user_data)))
                
                conn.commit()
                conn.close()

    def get_last_post_id(self, user_id: int, bot_index: int = 0) -> int:
        """Get last post ID for specific bot"""
        user_data = self.get_user_data(user_id)
        bots = user_data.get('bots', [])
        if 0 <= bot_index < len(bots) and bots[bot_index]:
            return bots[bot_index].get('last_post_id', 0)
        return 0

    def set_last_post_id(self, user_id: int, bot_index: int, post_id: int):
        """Set last post ID for specific bot"""
        # Get current bots data
        bots = self.get_bots(user_id)
        
        # Ensure we have enough slots
        while len(bots) <= bot_index:
            bots.append({})
        
        # Initialize bot data if empty
        if not bots[bot_index]:
            bots[bot_index] = {}
            
        # Set the last post ID
        bots[bot_index]['last_post_id'] = post_id
        
        # Update the bot data in database
        self.update_bot(user_id, bot_index, bots[bot_index])

class VKParser:
    def __init__(self, token: str, group_id: str):
        self.vk_session = VkApi(token=token)
        self.vk = self.vk_session.get_api()
        self.group_id = group_id
        self.api_version = '5.131'

    def get_new_posts(self, last_checked_id: int) -> tuple[list, int]:
        try:
            # Получаем 10 последних постов для лучшего обнаружения
            response = self.vk.wall.get(
                owner_id=self.group_id,
                count=10,
                filter='owner',
                v=self.api_version
            )
            
            new_posts = []
            current_max_id = last_checked_id
            
            # Сортируем посты по ID (от старых к новым)
            sorted_posts = sorted(response['items'], key=lambda x: x['id'])
            
            for post in sorted_posts:
                # Пропускаем закрепленный пост и рекламу
                if post.get('is_pinned') or post.get('marked_as_ads'):
                    continue
                    
                # Проверяем, является ли пост новым
                if post['id'] > last_checked_id:
                    new_posts.append(post)
                    if post['id'] > current_max_id:
                        current_max_id = post['id']
            
            # Сортируем новые посты по ID (от старых к новым) для правильного порядка публикации
            new_posts.sort(key=lambda x: x['id'])
            
            if new_posts:
                logger.info(f"Найдены новые посты: {len(new_posts)}. Максимальный ID: {current_max_id}")
            else:
                logger.info(f"Новых постов не найдено. Последний ID: {last_checked_id}")
            
            return new_posts, current_max_id
            
        except VkApiError as e:
            logger.error(f"Ошибка VK API: {e}")
            return [], last_checked_id
        except Exception as e:
            logger.error(f"Неизвестная ошибка при получении постов: {e}", exc_info=True)
            return [], last_checked_id

class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.user_config = UserConfig()
        
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Главное меню с красивым дизайном для управления несколькими ботами"""
        user = update.effective_user
        user_id = user.id
        user_data = self.user_config.get_user_data(user_id)
        bots = self.user_config.get_bots(user_id)
        
        # Проверяем, есть ли у пользователя старая конфигурация (один бот)
        # Если да, то переносим её в новую систему
        if not bots and any(k in user_data for k in ['vk_token', 'vk_group_id', 'tg_bot_token', 'tg_channel']):
            # Создаем новый бот с данными старого
            old_bot = {}
            for key in ['vk_token', 'vk_group_id', 'tg_bot_token', 'tg_channel']:
                if key in user_data:
                    old_bot[key] = user_data[key]
            
            # Добавляем last_post_id если он есть
            if 'last_post_id' in user_data:
                old_bot['last_post_id'] = user_data['last_post_id']
            
            # Сохраняем старый бот как первый бот в новой системе
            bots = [old_bot, {}, {}]
            # Обновляем данные пользователя
            for i, bot in enumerate(bots):
                if bot:  # Только если бот не пустой
                    self.user_config.update_bot(user_id, i, bot)
            
            # Удаляем старые данные
            for key in ['vk_token', 'vk_group_id', 'tg_bot_token', 'tg_channel', 'last_post_id']:
                if key in user_data:
                    user_data.pop(key)
            self.user_config.data[str(user_id)] = user_data
            self.user_config.save()
        else:
            # Убедимся, что у нас всегда 3 слота для ботов
            while len(bots) < 3:
                bots.append({})
        
        text = (
            f"✨ <b>Добро пожаловать, {user.first_name}!</b> ✨\n\n"
            "🚀 <i>Это профессиональный инструмент для автоматической публикации "
            "постов из ВКонтакте в Telegram</i>\n\n"
            "📊 <b>Ваши боты:</b>\n"
        )
        
        # Отображаем статус каждого бота
        for i, bot in enumerate(bots):
            if bot:
                # Проверяем, все ли настройки заполнены
                is_complete = all(k in bot for k in ['vk_token', 'vk_group_id', 'tg_bot_token', 'tg_channel'])
                status_emoji = "🟢" if is_complete else "🟡"
                text += f"{status_emoji} <b>Бот #{i+1}:</b> {'Готов к работе' if is_complete else 'Настройка не завершена'}\n"
            else:
                text += f"🔴 <b>Бот #{i+1}:</b> Не настроен\n"
        
        text += "\n🔧 <b>Выберите действие:</b>"
        
        keyboard = [
            [InlineKeyboardButton("🤖 Управление ботами", callback_data='manage_bots')],
            [InlineKeyboardButton("🔄 Проверить все боты", callback_data='check_all_bots')],
            [InlineKeyboardButton("❓ Помощь", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')

    async def manage_bots_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню управления ботами"""
        user_id = update.effective_user.id
        bots = self.user_config.get_bots(user_id)
        
        # Убедимся, что у нас всегда 3 слота для ботов
        while len(bots) < 3:
            bots.append({})
        
        text = "🤖 <b>Управление ботами</b>\n\nВыберите бота для настройки:"
        
        keyboard = []
        for i, bot in enumerate(bots):
            if bot:
                # Проверяем, все ли настройки заполнены
                is_complete = all(k in bot for k in ['vk_token', 'vk_group_id', 'tg_bot_token', 'tg_channel'])
                status_text = "Готов" if is_complete else "Не завершено"
                keyboard.append([InlineKeyboardButton(f"🔧 Бот #{i+1} ({status_text})", callback_data=f'edit_bot_{i}')])
            else:
                keyboard.append([InlineKeyboardButton(f"➕ Бот #{i+1} (Добавить)", callback_data=f'edit_bot_{i}')])
        
        keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data='back_to_start')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

    async def edit_bot_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE, bot_index: int):
        """Меню редактирования конкретного бота"""
        user_id = update.effective_user.id
        bot = self.user_config.get_bot(user_id, bot_index)
        
        # Эмодзи-индикаторы статуса
        vk_token_status = "🟢" if bot.get('vk_token') else "🔴"
        vk_group_status = "🟢" if bot.get('vk_group_id') else "🔴"
        tg_bot_status = "🟢" if bot.get('tg_bot_token') else "🔴"
        tg_channel_status = "🟢" if bot.get('tg_channel') else "🔴"
        
        text = (
            f"🔧 <b>Настройка Бота #{bot_index+1}</b>\n\n"
            "📊 <b>Статус настроек:</b>\n"
            f"{vk_token_status} <b>Токен VK:</b> {'установлен' if bot.get('vk_token') else 'не установлен'}\n"
            f"{vk_group_status} <b>ID группы VK:</b> {'установлен' if bot.get('vk_group_id') else 'не установлен'}\n"
            f"{tg_bot_status} <b>Токен бота:</b> {'установлен' if bot.get('tg_bot_token') else 'не установлен'}\n"
            f"{tg_channel_status} <b>Канал для публикаций:</b> {'установлен' if bot.get('tg_channel') else 'не установлен'}\n\n"
            "🔧 <b>Выберите действие:</b>"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("🔐 Токен VK", callback_data=f'set_vk_token_{bot_index}'),
                InlineKeyboardButton("🖼️ ID группы", callback_data=f'set_vk_group_id_{bot_index}')
            ],
            [
                InlineKeyboardButton("🤖 Токен бота", callback_data=f'set_tg_bot_token_{bot_index}'),
                InlineKeyboardButton("📢 Канал", callback_data=f'set_tg_channel_{bot_index}')
            ],
            [
                InlineKeyboardButton("🔄 Проверить сейчас", callback_data=f'check_now_{bot_index}'),
                InlineKeyboardButton("🗑️ Удалить бот", callback_data=f'delete_bot_{bot_index}')
            ],
            [InlineKeyboardButton("◀️ Назад", callback_data='manage_bots')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

    async def input_setting(self, update: Update, context: ContextTypes.DEFAULT_TYPE, setting_type: str, bot_index: int = 0):
        """Красивые формы ввода настроек для конкретного бота"""
        # Определяем тип настройки и индекс бота из setting_type, если он содержит индекс
        if '_' in setting_type:
            parts = setting_type.split('_')
            if len(parts) >= 3 and parts[-1].isdigit():
                setting_type = '_'.join(parts[:-1])
                bot_index = int(parts[-1])
        
        prompts = {
            'vk_token': (
                "🔐 <b>Настройка токена VK</b>\n\n"
                "1. Перейдите по ссылке: vk.com/apps?act=manage\n"
                "2. Создайте приложение типа <i>Standalone</i>\n"
                "3. Скопируйте <b>Сервисный ключ доступа</b>\n\n"
                "📝 <b>Введите ваш токен:</b>"
            ),
            'vk_group_id': (
                "🖼️ <b>Настройка ID группы VK</b>\n\n"
                "1. Откройте вашу группу ВКонтакте\n"
                "2. Скопируйте цифры из адреса:\n"
                "   - Для <i>vk.com/club123456</i> введите: <b>-123456</b>\n"
                "   - Для <i>vk.com/public123456</i> введите: <b>-123456</b>\n\n"
                "📝 <b>Введите ID группы:</b>"
            ),
            'tg_bot_token': (
                "🤖 <b>Настройка бота Telegram</b>\n\n"
                "1. Напишите <i>@BotFather</i>\n"
                "2. Используйте команду <code>/newbot</code>\n"
                "3. Скопируйте токен для вашего бота\n\n"
                "📝 <b>Введите токен бота:</b>"
            ),
            'tg_channel': (
                "📢 <b>Настройка канала Telegram</b>\n\n"
                "1. Создайте канал или выберите существующий\n"
                "2. Добавьте вашего бота как администратора\n"
                "3. Укажите @username (например <i>@my_channel</i>)\n"
                "   или ID канала (например <i>-100123456789</i>)\n\n"
                "📝 <b>Введите данные канала:</b>"
            )
        }
        
        keyboard = [
            [InlineKeyboardButton("◀️ Назад", callback_data=f'edit_bot_{bot_index}')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            prompts[setting_type],
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        # Сохраняем информацию о том, какие настройки мы ожидаем и для какого бота
        self.user_config.update_user_data(update.effective_user.id, 'awaiting_input', f"{setting_type}_{bot_index}")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка введенных данных для конкретного бота"""
        user_id = update.effective_user.id
        user_data = self.user_config.get_user_data(user_id)
        
        if user_data.get('awaiting_input'):
            # Получаем тип настройки и индекс бота
            awaiting_input = user_data['awaiting_input']
            if '_' in awaiting_input and awaiting_input.split('_')[-1].isdigit():
                parts = awaiting_input.split('_')
                bot_index = int(parts[-1])
                setting_type = '_'.join(parts[:-1])
            else:
                setting_type = awaiting_input
                bot_index = 0
            
            value = update.message.text.strip()
            
            # Валидация ввода
            if setting_type == 'tg_channel':
                if not (value.startswith('@') or value.startswith('-')):
                    await update.message.reply_text("❌ Канал должен начинаться с @ или -")
                    return
            elif setting_type == 'vk_group_id':
                if not value.lstrip('-').isdigit():
                    await update.message.reply_text("❌ ID группы должен быть числом (для групп с минусом)")
                    return
                # Для групп добавляем минус если его нет
                if not value.startswith('-') and int(value) != 0:
                    value = f"-{value.lstrip('-')}"
            
            # Для токена VK делаем проверку
            if setting_type == 'vk_token':
                try:
                    test_vk = VkApi(token=value)
                    test_vk.method('groups.getById', {'group_id': '1', 'v': '5.131'})
                except Exception as e:
                    await update.message.reply_text(f"❌ Неверный токен VK: {e}\nПожалуйста, попробуйте еще раз")
                    return
            
            # Для токена бота Telegram делаем проверку
            if setting_type == 'tg_bot_token':
                try:
                    test_url = f"https://api.telegram.org/bot{value}/getMe"
                    response = requests.get(test_url, timeout=10)
                    if not response.json().get('ok'):
                        await update.message.reply_text("❌ Неверный токен бота Telegram. Проверьте токен и попробуйте снова.")
                        return
                except Exception as e:
                    await update.message.reply_text(f"❌ Ошибка проверки токена бота: {e}")
                    return
                
            # Получаем текущие данные бота
            bot_data = self.user_config.get_bot(user_id, bot_index)
            if not bot_data:
                bot_data = {}
            
            # Обновляем данные бота
            bot_data[setting_type] = value
            self.user_config.update_bot(user_id, bot_index, bot_data)
            self.user_config.update_user_data(user_id, 'awaiting_input', None)
            
            await update.message.reply_text(f"✅ {setting_type} успешно сохранен для Бота #{bot_index+1}!")
            # Возвращаемся к меню редактирования бота
            try:
                # Создаем новое сообщение с меню редактирования бота
                await self.show_bot_menu_in_message(update, context, bot_index)
            except:
                # Если не удалось вернуться к меню, отправляем простое сообщение с кнопкой назад
                keyboard = [
                    [InlineKeyboardButton("◀️ Назад к настройке бота", callback_data=f'edit_bot_{bot_index}')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    "✅ Настройка сохранена!\n\nВыберите действие:",
                    reply_markup=reply_markup
                )

    async def show_bot_menu_in_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, bot_index: int):
        """Показать меню редактирования бота как новое сообщение"""
        user_id = update.effective_user.id
        bot = self.user_config.get_bot(user_id, bot_index)
        
        # Эмодзи-индикаторы статуса
        vk_token_status = "🟢" if bot.get('vk_token') else "🔴"
        vk_group_status = "🟢" if bot.get('vk_group_id') else "🔴"
        tg_bot_status = "🟢" if bot.get('tg_bot_token') else "🔴"
        tg_channel_status = "🟢" if bot.get('tg_channel') else "🔴"
        
        text = (
            f"🔧 <b>Настройка Бота #{bot_index+1}</b>\n\n"
            "📊 <b>Статус настроек:</b>\n"
            f"{vk_token_status} <b>Токен VK:</b> {'установлен' if bot.get('vk_token') else 'не установлен'}\n"
            f"{vk_group_status} <b>ID группы VK:</b> {'установлен' if bot.get('vk_group_id') else 'не установлен'}\n"
            f"{tg_bot_status} <b>Токен бота:</b> {'установлен' if bot.get('tg_bot_token') else 'не установлен'}\n"
            f"{tg_channel_status} <b>Канал для публикаций:</b> {'установлен' if bot.get('tg_channel') else 'не установлен'}\n\n"
            "🔧 <b>Выберите действие:</b>"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("🔐 Токен VK", callback_data=f'set_vk_token_{bot_index}'),
                InlineKeyboardButton("🖼️ ID группы", callback_data=f'set_vk_group_id_{bot_index}')
            ],
            [
                InlineKeyboardButton("🤖 Токен бота", callback_data=f'set_tg_bot_token_{bot_index}'),
                InlineKeyboardButton("📢 Канал", callback_data=f'set_tg_channel_{bot_index}')
            ],
            [
                InlineKeyboardButton("🔄 Проверить сейчас", callback_data=f'check_now_{bot_index}'),
                InlineKeyboardButton("🗑️ Удалить бот", callback_data=f'delete_bot_{bot_index}')
            ],
            [InlineKeyboardButton("◀️ Назад", callback_data='manage_bots')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок для управления несколькими ботами"""
        query = update.callback_query
        await query.answer()
        
        # Обработка кнопок управления ботами
        if query.data == 'manage_bots':
            await self.manage_bots_menu(update, context)
        elif query.data == 'check_all_bots':
            await self.check_all_bots(update, context)
        elif query.data.startswith('edit_bot_'):
            bot_index = int(query.data.split('_')[-1])
            await self.edit_bot_menu(update, context, bot_index)
        elif query.data.startswith('delete_bot_'):
            bot_index = int(query.data.split('_')[-1])
            await self.delete_bot(update, context, bot_index)
        elif query.data.startswith('confirm_delete_bot_'):
            bot_index = int(query.data.split('_')[-1])
            await self.confirm_delete_bot(update, context, bot_index)
        
        # Обработка кнопок настройки для конкретного бота
        elif query.data.startswith('set_vk_token_'):
            bot_index = int(query.data.split('_')[-1])
            await self.input_setting(update, context, f'vk_token_{bot_index}', bot_index)
        elif query.data.startswith('set_vk_group_id_'):
            bot_index = int(query.data.split('_')[-1])
            await self.input_setting(update, context, f'vk_group_id_{bot_index}', bot_index)
        elif query.data.startswith('set_tg_bot_token_'):
            bot_index = int(query.data.split('_')[-1])
            await self.input_setting(update, context, f'tg_bot_token_{bot_index}', bot_index)
        elif query.data.startswith('set_tg_channel_'):
            bot_index = int(query.data.split('_')[-1])
            await self.input_setting(update, context, f'tg_channel_{bot_index}', bot_index)
        elif query.data.startswith('check_now_'):
            bot_index = int(query.data.split('_')[-1])
            await self.check_now_bot(update, context, bot_index)
            
        # Обработка старых кнопок (для совместимости)
        elif query.data == 'back_to_start':
            await self.start(update, context)
        elif query.data == 'help':
            await self.show_help(update, context)
        elif query.data == 'check_now':
            await self.check_now(update, context)
        elif query.data == 'set_vk_token':
            await self.input_setting(update, context, 'vk_token', 0)
        elif query.data == 'set_vk_group_id':
            await self.input_setting(update, context, 'vk_group_id', 0)
        elif query.data == 'set_tg_bot_token':
            await self.input_setting(update, context, 'tg_bot_token', 0)
        elif query.data == 'set_tg_channel':
            await self.input_setting(update, context, 'tg_channel', 0)
        elif query.data == 'check_settings':
            await self.start(update, context)

    async def show_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Красивая страница помощи"""
        text = (
            "📚 <b>Полное руководство по настройке</b> 📚\n\n"
            "🔹 <b>1. Получение токена VK</b>\n"
            "   • Перейдите на <a href='https://vk.com/apps?act=manage'>страницу управления приложениями</a>\n"
            "   • Создайте приложение типа <i>Standalone</i>\n"
            "   • Скопируйте <b>Сервисный ключ доступа</b>\n\n"
            "🔹 <b>2. ID группы VK</b>\n"
            "   • Откройте вашу группу\n"
            "   • Для адреса <i>vk.com/club123456</i> введите <b>-123456</b>\n"
            "   • Для страницы <i>vk.com/id123456</i> введите <b>123456</b>\n\n"
            "🔹 <b>3. Создание Telegram бота</b>\n"
            "   • Напишите <i>@BotFather</i>\n"
            "   • Используйте команду <code>/newbot</code>\n"
            "   • Скопируйте полученный токен\n\n"
            "🔹 <b>4. Настройка канала</b>\n"
            "   • Создайте канал или выберите существующий\n"
            "   • Добавьте бота как администратора\n"
            "   • Укажите @username или ID канала\n\n"
            "🔄 <b>Автоматическая проверка</b>\n"
            "Бот проверяет новые посты каждые 5 минут и автоматически публикует их в ваш канал!"
        )
        
        keyboard = [
            [InlineKeyboardButton("📹 Видеоинструкция", url='https://example.com/tutorial')],
            [InlineKeyboardButton("◀️ Назад в меню", callback_data='back_to_start')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            text, 
            reply_markup=reply_markup, 
            parse_mode='HTML',
            disable_web_page_preview=True
        )

    async def delete_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE, bot_index: int):
        """Удаление бота"""
        user_id = update.effective_user.id
        
        # Подтверждение удаления
        text = f"🗑️ <b>Удаление Бота #{bot_index+1}</b>\n\nВы уверены, что хотите удалить этого бота? Все настройки будут потеряны."
        
        keyboard = [
            [InlineKeyboardButton("✅ Да, удалить", callback_data=f'confirm_delete_bot_{bot_index}')],
            [InlineKeyboardButton("❌ Отмена", callback_data=f'edit_bot_{bot_index}')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

    async def confirm_delete_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE, bot_index: int):
        """Подтверждение удаления бота"""
        user_id = update.effective_user.id
        
        # Удаляем бота
        self.user_config.delete_bot(user_id, bot_index)
        
        text = f"✅ <b>Бот #{bot_index+1} успешно удален!</b>\n\nВсе настройки для этого бота были удалены."
        
        keyboard = [
            [InlineKeyboardButton("◀️ Назад к ботам", callback_data='manage_bots')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

    async def check_all_bots(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Проверка всех ботов"""
        user_id = update.effective_user.id
        bots = self.user_config.get_bots(user_id)
        
        # Убедимся, что у нас всегда 3 слота для ботов
        while len(bots) < 3:
            bots.append({})
        
        # Анимация загрузки
        message = await update.callback_query.edit_message_text(
            "🔍 <b>Поиск новых постов во всех ботах...</b>\n\n"
            "⏳ Пожалуйста, подождите несколько секунд",
            parse_mode='HTML'
        )
        
        results = []
        for i, bot in enumerate(bots):
            if bot and all(k in bot for k in ['vk_token', 'vk_group_id', 'tg_bot_token', 'tg_channel']):
                try:
                    last_post_id = self.user_config.get_last_post_id(user_id, i)
                    logger.info(f"Проверка постов для бота #{i+1}, последний ID: {last_post_id}")
                    vk_parser = VKParser(bot['vk_token'], bot['vk_group_id'])
                    posts, new_last_post_id = vk_parser.get_new_posts(last_post_id)
                    
                    if posts:
                        # Сохраняем новый last_post_id перед отправкой
                        self.user_config.set_last_post_id(user_id, i, new_last_post_id)
                        logger.info(f"Найдено {len(posts)} новых постов для бота #{i+1}, новый последний ID: {new_last_post_id}")
                        
                        # Отправляем посты
                        sent_posts = 0
                        failed_posts = 0
                        for post in posts:
                            try:
                                await self._forward_post(post, bot['tg_bot_token'], bot['tg_channel'], context)
                                sent_posts += 1
                                logger.info(f"Пост #{post['id']} успешно отправлен для бота #{i+1}")
                            except Exception as e:
                                failed_posts += 1
                                logger.error(f"Ошибка отправки поста #{post['id']} для бота #{i+1}: {e}")
                            
                            await asyncio.sleep(1)  # Задержка между постами
                        
                        results.append(f"✅ Бот #{i+1}: Опубликовано {sent_posts} постов, ошибок: {failed_posts}")
                    else:
                        results.append(f"🟢 Бот #{i+1}: Новых постов нет")
                except VkApiError as e:
                    logger.error(f"Ошибка VK API для бота #{i+1}: {e}")
                    results.append(f"🔴 Бот #{i+1}: Ошибка VK API")
                except Exception as e:
                    logger.error(f"Неизвестная ошибка для бота #{i+1}: {e}", exc_info=True)
                    results.append(f"🔴 Бот #{i+1}: Ошибка")
            elif bot:
                results.append(f"🟡 Бот #{i+1}: Настройки не завершены")
            else:
                results.append(f"🔴 Бот #{i+1}: Не настроен")
        
        # Формируем итоговое сообщение
        text = "📊 <b>Результаты проверки всех ботов:</b>\n\n" + "\n".join(results)
        
        keyboard = [
            [InlineKeyboardButton("🔄 Проверить снова", callback_data='check_all_bots')],
            [InlineKeyboardButton("◀️ В меню", callback_data='back_to_start')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await message.edit_text(text, reply_markup=reply_markup, parse_mode='HTML')

    async def check_now_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE, bot_index: int):
        """Проверка постов для конкретного бота"""
        user_id = update.effective_user.id
        bot = self.user_config.get_bot(user_id, bot_index)
        
        # Проверка заполненности настроек
        missing = []
        if not bot.get('vk_token'):
            missing.append("токен VK")
        if not bot.get('vk_group_id'):
            missing.append("ID группы VK")
        if not bot.get('tg_bot_token'):
            missing.append("токен бота")
        if not bot.get('tg_channel'):
            missing.append("канал для публикаций")
            
        if missing:
            keyboard = [
                [InlineKeyboardButton("⚙️ Заполнить настройки", callback_data=f'edit_bot_{bot_index}')],
                [InlineKeyboardButton("◀️ Назад", callback_data='manage_bots')]
            ]
            await update.callback_query.edit_message_text(
                f"🔴 <b>Настройки не завершены для Бота #{bot_index+1}!</b>\n\n"
                f"Отсутствуют: <b>{', '.join(missing)}</b>\n\n"
                "Пожалуйста, заполните все необходимые настройки для работы бота.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
            return
        
        # Анимация загрузки
        message = await update.callback_query.edit_message_text(
            f"🔍 <b>Поиск новых постов для Бота #{bot_index+1}...</b>\n\n"
            "⏳ Пожалуйста, подождите несколько секунд",
            parse_mode='HTML'
        )
        
        try:
            last_post_id = self.user_config.get_last_post_id(user_id, bot_index)
            logger.info(f"Проверка постов для бота #{bot_index+1}, последний ID: {last_post_id}")
            vk_parser = VKParser(bot['vk_token'], bot['vk_group_id'])
            posts, new_last_post_id = vk_parser.get_new_posts(last_post_id)
            
            if not posts:
                keyboard = [
                    [InlineKeyboardButton("🔄 Проверить снова", callback_data=f'check_now_{bot_index}')],
                    [InlineKeyboardButton("◀️ Назад", callback_data=f'edit_bot_{bot_index}')]
                ]
                await message.edit_text(
                    f"🟢 <b>Новых постов не найдено для Бота #{bot_index+1}</b>\n\n"
                    "Все актуальные посты уже опубликованы в вашем канале.\n\n"
                    f"Последний проверенный ID: <code>{last_post_id}</code>",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='HTML'
                )
            else:
                # Сохраняем новый last_post_id перед отправкой
                self.user_config.set_last_post_id(user_id, bot_index, new_last_post_id)
                logger.info(f"Найдено {len(posts)} новых постов для бота #{bot_index+1}, новый последний ID: {new_last_post_id}")
                
                # Прогресс-бар отправки
                total_posts = len(posts)
                sent_posts = 0
                failed_posts = 0
                
                for i, post in enumerate(posts, 1):
                    await message.edit_text(
                        f"📤 <b>Публикация постов для Бота #{bot_index+1}...</b>\n\n"
                        f"⏳ Отправлено: <b>{sent_posts}/{total_posts}</b>\n"
                        f"❌ Ошибок: <b>{failed_posts}</b>\n"
                        f"🔄 Обрабатываю пост #{post['id']}",
                        parse_mode='HTML'
                    )
                    try:
                        await self._forward_post(post, bot['tg_bot_token'], bot['tg_channel'], context)
                        sent_posts += 1
                        logger.info(f"Пост #{post['id']} успешно отправлен для бота #{bot_index+1}")
                    except Exception as e:
                        failed_posts += 1
                        logger.error(f"Ошибка отправки поста #{post['id']} для бота #{bot_index+1}: {e}")
                    
                    await asyncio.sleep(1)  # Задержка между постами
                
                keyboard = [
                    [InlineKeyboardButton("🔄 Проверить снова", callback_data=f'check_now_{bot_index}')],
                    [InlineKeyboardButton("◀️ Назад", callback_data=f'edit_bot_{bot_index}')]
                ]
                await message.edit_text(
                    f"✅ <b>Готово для Бота #{bot_index+1}!</b>\n\n"
                    f"Успешно опубликовано: <b>{sent_posts}</b> постов\n"
                    f"Ошибок: <b>{failed_posts}</b>\n\n"
                    f"Канал: <b>{bot['tg_channel']}</b>\n"
                    f"Последний обработанный ID: <code>{new_last_post_id}</code>",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='HTML'
                )
                
        except VkApiError as e:
            logger.error(f"Ошибка VK API для бота #{bot_index+1}: {e}")
            keyboard = [
                [InlineKeyboardButton("⚙️ Проверить настройки", callback_data=f'edit_bot_{bot_index}')],
                [InlineKeyboardButton("❓ Помощь", callback_data='help')],
                [InlineKeyboardButton("◀️ Назад", callback_data='manage_bots')]
            ]
            await message.edit_text(
                f"🔴 <b>Ошибка VK API для Бота #{bot_index+1}</b>\n\n"
                f"<code>{str(e)}</code>\n\n"
                "Проверьте правильность токена VK и ID группы.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Неизвестная ошибка для бота #{bot_index+1}: {e}", exc_info=True)
            keyboard = [
                [InlineKeyboardButton("❓ Помощь", callback_data='help')],
                [InlineKeyboardButton("◀️ Назад", callback_data='manage_bots')]
            ]
            await message.edit_text(
                f"🔴 <b>Критическая ошибка для Бота #{bot_index+1}</b>\n\n"
                f"<code>{str(e)}</code>\n\n"
                "Пожалуйста, попробуйте позже или обратитесь в поддержку.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )

    async def check_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Красивая страница проверки постов - перенаправляет в новое меню"""
        # Перенаправляем пользователя в новое меню управления ботами
        await self.manage_bots_menu(update, context)

    async def _forward_post(self, post: dict, bot_token: str, channel: str, context: ContextTypes.DEFAULT_TYPE):
        """Отправка поста через бота пользователя"""
        try:
            text = post.get('text', 'Новый пост')
            
            # Ограничиваем длину текста до 4096 символов (лимит Telegram)
            if len(text) > 4096:
                text = text[:4093] + "..."
            
            if post.get('attachments'):
                media = []
                for attach in post['attachments']:
                    if attach['type'] == 'photo':
                        photo = attach['photo']
                        sizes = photo['sizes']
                        max_size = max(sizes, key=lambda x: x['width'] * x['height'])
                        media.append(max_size['url'])
                
                if media:
                    if len(media) > 1:
                        await self._send_media_group(text, media, bot_token, channel)
                    else:
                        await self._send_photo(text, media[0], bot_token, channel)
                    return
            
            # Если нет вложений или не удалось их обработать
            if text.strip():  # Отправляем только если есть текст
                await self._send_message(text, bot_token, channel)
            
        except Exception as e:
            logger.error(f"Ошибка отправки поста: {e}")

    async def _send_message(self, text: str, bot_token: str, channel: str):
        """Отправка текстового сообщения"""
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            'chat_id': channel,
            'text': text,
            'parse_mode': 'HTML'
        }
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            logger.error(f"Ошибка отправки сообщения: {response.text}")
        return response

    async def _send_photo(self, text: str, photo_url: str, bot_token: str, channel: str):
        """Отправка фото"""
        # Ограничиваем длину текста для подписи (лимит 1024 символа)
        if len(text) > 1024:
            text = text[:1021] + "..."
            
        url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        payload = {
            'chat_id': channel,
            'photo': photo_url,
            'caption': text,
            'parse_mode': 'HTML'
        }
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            logger.error(f"Ошибка отправки фото: {response.text}")
        return response

    async def _send_media_group(self, text: str, media_urls: list, bot_token: str, channel: str):
        """Отправка медиагруппы"""
        url = f"https://api.telegram.org/bot{bot_token}/sendMediaGroup"
        
        # Ограничиваем длину текста для подписи (лимит 1024 символа)
        if len(text) > 1024:
            text = text[:1021] + "..."
        
        # Ограничиваем количество медиа в группе до 10 (лимит Telegram)
        media_urls = media_urls[:10]
        
        media = [{
            'type': 'photo',
            'media': url,
            'caption': text if i == 0 else '',
            'parse_mode': 'HTML'
        } for i, url in enumerate(media_urls)]
        
        payload = {
            'chat_id': channel,
            'media': media  # Передаем список напрямую, а не как JSON строку
        }
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            logger.error(f"Ошибка отправки медиагруппы: {response.text}")
        return response

    async def _auto_check_posts(self, context: ContextTypes.DEFAULT_TYPE):
        """Автоматическая проверка постов для всех ботов"""
        # Получаем всех пользователей из базы данных
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, data FROM users')
        users = cursor.fetchall()
        conn.close()
        
        # Проверяем каждого пользователя
        for user_row in users:
            user_id = user_row[0]
            user_data = json.loads(user_row[1]) if user_row[1] else {}
            
            # Получаем все боты пользователя
            bots = user_data.get('bots', [])
            
            # Проверяем каждый бот пользователя
            for bot_index, bot in enumerate(bots):
                if bot and all(k in bot for k in ['vk_token', 'vk_group_id', 'tg_bot_token', 'tg_channel']):
                    try:
                        logger.info(f"Проверяем посты для пользователя {user_id}, бот #{bot_index+1}")
                        last_post_id = self.user_config.get_last_post_id(user_id, bot_index)
                        vk_parser = VKParser(bot['vk_token'], bot['vk_group_id'])
                        posts, new_last_post_id = vk_parser.get_new_posts(last_post_id)
                        
                        if posts:
                            logger.info(f"Найдено {len(posts)} новых постов для пользователя {user_id}, бот #{bot_index+1}")
                            # Сохраняем новый last_post_id перед отправкой
                            self.user_config.set_last_post_id(user_id, bot_index, new_last_post_id)
                            
                            for post in posts:
                                await self._forward_post(post, bot['tg_bot_token'], bot['tg_channel'], context)
                                await asyncio.sleep(1)  # Задержка между постами
                            
                    except VkApiError as e:
                        logger.error(f"Ошибка VK API для пользователя {user_id}, бот #{bot_index+1}: {e}")
                    except Exception as e:
                        logger.error(f"Неизвестная ошибка для пользователя {user_id}, бот #{bot_index+1}: {e}", exc_info=True)

    def run(self):
        """Запуск бота"""
        application = Application.builder().token(self.token).build()
        
        # Добавляем обработчики
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CallbackQueryHandler(self.button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        # Автопроверка новых постов
        job_queue = application.job_queue
        job_queue.run_repeating(
            self._auto_check_posts,
            interval=33.0,  # Проверка каждые 5 минут
            first=10.0
        )
        
        application.run_polling()

if __name__ == '__main__':
    bot = TelegramBot(BOT_TOKEN)
    bot.run()
