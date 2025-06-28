#!/usr/bin/env python3
"""
Полностью автономный Telegram бот для работы в облаке
Использует GitHub Actions + webhook для 24/7 работы
"""

import os
import json
import time
import logging
import requests
from typing import Dict, Optional, Set
from datetime import datetime, timedelta
from threading import Thread
import signal
import sys

# Конфигурация
BOT_TOKEN = os.getenv("7063629673:AAH14qV3jFQ6LDtOLBwZs0-wyniZJ59lhxU", "")
ADMIN_USER_ID = int(os.getenv("5028276712", "0")) if os.getenv("ADMIN_USER_ID") else None
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # URL для webhook
KEEP_ALIVE_URL = os.getenv("KEEP_ALIVE_URL", "")  # URL для keep-alive

# Хранилище (в продакшене - база данных)
message_mappings: Dict[int, Dict] = {}
admin_users: Set[int] = {ADMIN_USER_ID} if ADMIN_USER_ID else set()
blocked_users: Set[int] = set()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)

# Keep-alive система
class KeepAlive:
    def __init__(self):
        self.running = True
        self.thread = None
        
    def start(self):
        """Запуск keep-alive сервиса"""
        if KEEP_ALIVE_URL:
            self.thread = Thread(target=self._keep_alive_loop, daemon=True)
            self.thread.start()
            logger.info("🔄 Keep-alive сервис запущен")
    
    def stop(self):
        """Остановка keep-alive сервиса"""
        self.running = False
        if self.thread:
            self.thread.join()
    
    def _keep_alive_loop(self):
        """Цикл поддержания активности"""
        while self.running:
            try:
                response = requests.get(KEEP_ALIVE_URL, timeout=30)
                logger.debug(f"Keep-alive ping: {response.status_code}")
            except Exception as e:
                logger.warning(f"Keep-alive error: {e}")
            
            time.sleep(300)  # Пинг каждые 5 минут

# Автономное восстановление
class SelfHealing:
    def __init__(self):
        self.error_count = 0
        self.max_errors = 50
        self.restart_count = 0
        
    def handle_error(self, error: Exception):
        """Обработка ошибок с самовосстановлением"""
        self.error_count += 1
        logger.error(f"Ошибка #{self.error_count}: {error}")
        
        if self.error_count >= self.max_errors:
            logger.critical("🚨 Критический уровень ошибок, самоперезапуск...")
            self.self_restart()
    
    def self_restart(self):
        """Самоперезапуск бота"""
        try:
            self.restart_count += 1
            logger.info(f"🔄 Самоперезапуск #{self.restart_count}")
            
            # Отправляем уведомление админам
            for admin_id in admin_users:
                try:
                    send_message(admin_id, f"🔄 Бот автоматически перезапущен #{self.restart_count}")
                except:
                    pass
            
            # Перезапуск через внешний webhook (если настроен)
            if WEBHOOK_URL:
                requests.post(WEBHOOK_URL, json={"action": "restart"}, timeout=10)
            
            # Принудительный выход для перезапуска процесса
            os._exit(1)
            
        except Exception as e:
            logger.error(f"Ошибка самоперезапуска: {e}")
            os._exit(1)

# Глобальные объекты
keep_alive = KeepAlive()
self_healing = SelfHealing()

def send_message(chat_id: int, text: str, reply_to_message_id: Optional[int] = None) -> Optional[Dict]:
    """Отправить сообщение через Telegram Bot API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_to_message_id:
        data["reply_to_message_id"] = reply_to_message_id
    
    try:
        response = requests.post(url, json=data, timeout=30)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"API error {response.status_code}: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")
        self_healing.handle_error(e)
        return None

def forward_message(from_chat_id: int, message_id: int, to_chat_id: int) -> Optional[Dict]:
    """Переслать сообщение через Telegram Bot API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/forwardMessage"
    data = {
        "chat_id": to_chat_id,
        "from_chat_id": from_chat_id,
        "message_id": message_id
    }
    
    try:
        response = requests.post(url, json=data, timeout=30)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"API forward error {response.status_code}: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Ошибка пересылки сообщения: {e}")
        self_healing.handle_error(e)
        return None

def broadcast_to_all_users(text: str, admin_id: int) -> int:
    """Отправить сообщение всем пользователям"""
    sent_count = 0
    
    # Получаем список всех пользователей из маппингов
    user_ids = set()
    for mapping in message_mappings.values():
        user_ids.add(mapping['user_id'])
    
    # Отправляем всем пользователям
    for user_id in user_ids:
        if user_id not in blocked_users and user_id not in admin_users:
            try:
                if send_message(user_id, text):
                    sent_count += 1
                    time.sleep(0.1)  # Небольшая задержка между отправками
            except Exception as e:
                logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
    
    # Уведомляем админа о результате
    send_message(admin_id, f"📢 Рассылка завершена: отправлено {sent_count} пользователям")
    logger.info(f"Массовая рассылка от админа {admin_id}: {sent_count} получателей")
    
    return sent_count

def forward_to_admin(user_id: int, message_id: int) -> Optional[int]:
    """Переслать сообщение пользователя всем админам"""
    for admin_id in admin_users:
        result = forward_message(user_id, message_id, admin_id)
        if result:
            return result.get("result", {}).get("message_id")
    return None

def reply_to_user(user_id: int, text: str) -> bool:
    """Отправить ответ пользователю"""
    result = send_message(user_id, text)
    return result is not None

def is_admin(user_id: int) -> bool:
    """Проверить, является ли пользователь админом"""
    return user_id in admin_users

def is_blocked(user_id: int) -> bool:
    """Проверить, заблокирован ли пользователь"""
    return user_id in blocked_users

def add_mapping(admin_message_id: int, user_id: int):
    """Добавить маппинг между сообщением админа и пользователем"""
    message_mappings[admin_message_id] = {
        'user_id': user_id,
        'created_at': datetime.now()
    }
    cleanup_mappings()

def get_user_from_mapping(admin_message_id: int) -> Optional[int]:
    """Получить ID пользователя по ID сообщения админа"""
    mapping = message_mappings.get(admin_message_id)
    return mapping['user_id'] if mapping else None

def cleanup_mappings():
    """Очистить старые маппинги"""
    cutoff_time = datetime.now() - timedelta(hours=24)
    to_remove = []
    
    for msg_id, mapping in message_mappings.items():
        if mapping['created_at'] < cutoff_time:
            to_remove.append(msg_id)
    
    for msg_id in to_remove:
        del message_mappings[msg_id]

def handle_admin_commands(user_id: int, text: str):
    """Обработать админские команды"""
    if not is_admin(user_id):
        return
    
    parts = text.split()
    command = parts[0].lower()
    
    try:
        if command == "/addadmin" and len(parts) >= 2:
            new_admin_id = int(parts[1])
            admin_users.add(new_admin_id)
            send_message(user_id, f"✅ Пользователь {new_admin_id} добавлен в админы")
        
        elif command == "/removeadmin" and len(parts) >= 2:
            admin_id = int(parts[1])
            if admin_id == ADMIN_USER_ID:
                send_message(user_id, "❌ Нельзя удалить главного админа")
            elif admin_id in admin_users:
                admin_users.remove(admin_id)
                send_message(user_id, f"✅ Пользователь {admin_id} удален из админов")
            else:
                send_message(user_id, "❌ Пользователь не является админом")
        
        elif command == "/block" and len(parts) >= 2:
            block_id = int(parts[1])
            if block_id in admin_users:
                send_message(user_id, "❌ Нельзя заблокировать админа")
            else:
                blocked_users.add(block_id)
                send_message(user_id, f"✅ Пользователь {block_id} заблокирован")
        
        elif command == "/unblock" and len(parts) >= 2:
            unblock_id = int(parts[1])
            if unblock_id in blocked_users:
                blocked_users.remove(unblock_id)
                send_message(user_id, f"✅ Пользователь {unblock_id} разблокирован")
            else:
                send_message(user_id, "❌ Пользователь не заблокирован")
        
        elif command == "/admins":
            admins_list = "\n".join([f"• {admin_id}" for admin_id in admin_users])
            send_message(user_id, f"👥 Список админов:\n{admins_list}")
        
        elif command == "/blocked":
            if blocked_users:
                blocked_list = "\n".join([f"• {blocked_id}" for blocked_id in blocked_users])
                send_message(user_id, f"🚫 Заблокированные пользователи:\n{blocked_list}")
            else:
                send_message(user_id, "✅ Нет заблокированных пользователей")
        
        elif command == "/panel":
            panel_text = """🔧 АДМИН ПАНЕЛЬ

👥 Управление админами:
/addadmin [ID] - добавить админа
/removeadmin [ID] - удалить админа
/admins - список админов

🚫 Управление блокировками:
/block [ID] - заблокировать пользователя
/unblock [ID] - разблокировать пользователя
/blocked - список заблокированных

📊 Статистика:
/status - статус бота
/panel - эта панель

📢 Рассылка:
/broadcast [текст] - отправить всем пользователям
Или просто напишите сообщение без reply для рассылки

💾 Управление:
/restart - перезапустить бота
/logs - показать последние логи"""
            send_message(user_id, panel_text)
        
        elif command == "/broadcast" and len(parts) >= 2:
            broadcast_text = " ".join(parts[1:])
            broadcast_to_all_users(broadcast_text, user_id)
        
        elif command == "/restart":
            send_message(user_id, "🔄 Перезапуск бота...")
            logger.info(f"🔄 Перезапуск инициирован админом {user_id}")
            self_healing.self_restart()
        
        elif command == "/logs":
            try:
                if os.path.exists("bot.log"):
                    with open("bot.log", "r", encoding="utf-8") as f:
                        lines = f.readlines()
                        last_lines = lines[-10:] if len(lines) >= 10 else lines
                        log_text = "📋 Последние логи:\n\n" + "".join(last_lines)
                        if len(log_text) > 4000:
                            log_text = log_text[:4000] + "..."
                        send_message(user_id, log_text)
                else:
                    send_message(user_id, "📋 Файл логов не найден")
            except Exception as e:
                send_message(user_id, f"❌ Ошибка чтения логов: {e}")
                
    except ValueError:
        send_message(user_id, "❌ Неверный ID пользователя")
    except Exception as e:
        logger.error(f"Ошибка в команде {command}: {e}")
        send_message(user_id, f"❌ Ошибка выполнения команды: {e}")

def handle_message(update: Dict):
    """Обработать входящее сообщение"""
    message = update.get("message", {})
    if not message:
        return
    
    user = message.get("from", {})
    user_id = user.get("id")
    text = message.get("text", "")
    message_id = message.get("message_id")
    
    # Проверяем наличие медиа
    has_media = any(key in message for key in ["photo", "document", "video", "audio", "voice", "sticker"])
    
    if not user_id or (not text and not has_media):
        return
    
    if is_blocked(user_id):
        return
    
    # Логируем
    if text:
        logger.info(f"Сообщение от {user_id}: {text[:50]}...")
    else:
        logger.info(f"Медиа от {user_id}")
    
    if is_admin(user_id):
        reply_to = message.get("reply_to_message", {})
        if reply_to:
            # Ответ на конкретное сообщение
            replied_message_id = reply_to.get("message_id")
            target_user_id = get_user_from_mapping(replied_message_id)
            
            if target_user_id:
                if has_media:
                    result = forward_message(user_id, message_id, target_user_id)
                    success = result is not None
                else:
                    success = reply_to_user(target_user_id, text)
                
                if success:
                    logger.info(f"Ответ админа отправлен пользователю {target_user_id}")
                else:
                    logger.error(f"Не удалось отправить ответ пользователю {target_user_id}")
        else:
            # Сообщение без reply - команда или рассылка
            if text and text.startswith("/"):
                handle_admin_commands(user_id, text)
            elif text or has_media:
                # Рассылка всем пользователям
                if text:
                    broadcast_to_all_users(text, user_id)
                else:
                    send_message(user_id, "📢 Медиа-рассылка пока не поддерживается")
    else:
        # Сообщение от пользователя
        forwarded_msg_id = forward_to_admin(user_id, message_id)
        if forwarded_msg_id:
            add_mapping(forwarded_msg_id, user_id)
            logger.info(f"Сообщение от {user_id} переслано админам")

def handle_command(update: Dict):
    """Обработать команды"""
    message = update.get("message", {})
    if not message:
        return
    
    text = message.get("text", "")
    user_id = message.get("from", {}).get("id")
    
    if is_blocked(user_id):
        return
    
    if text == "/start":
        welcome_text = """Добро пожаловать в команду e3 | Wa

🔥 АКТУАЛЬНЫЙ ПРАЙС:
1ч - 8$ 2ч - 14$ 3ч - 18$

ЧТО БЫ СДАТЬ НОМЕР КИДАЙ ЕГО СЮДА 👇

‼️ НОМЕР КИДАЙ КАЖДУЮ МИНУТУ ПОКА НЕ НАПИШУТ: В-взял!
(тут сидят реальные люди и берут ваши номера, чат быстро пролетает и они не успевают замечать все)"""
        send_message(user_id, welcome_text)
    
    elif text == "/help":
        if is_admin(user_id):
            help_text = """📋 Админские команды:

👥 Управление: /addadmin, /removeadmin, /admins
🚫 Блокировки: /block, /unblock, /blocked  
📊 Информация: /status, /panel, /logs
📢 Рассылка: /broadcast или просто напишите текст
💾 Управление: /restart"""
        else:
            help_text = """Добро пожаловать в команду e3 | Wa

🔥 АКТУАЛЬНЫЙ ПРАЙС:
1ч - 8$ 2ч - 14$ 3ч - 18$

ЧТО БЫ СДАТЬ НОМЕР КИДАЙ ЕГО СЮДА 👇

‼️ НОМЕР КИДАЙ КАЖДУЮ МИНУТУ ПОКА НЕ НАПИШУТ: В-взял!
(тут сидят реальные люди и берут ваши номера, чат быстро пролетает и они не успевают замечать все)"""
        send_message(user_id, help_text)
    
    elif text == "/status" and is_admin(user_id):
        active_mappings = len(message_mappings)
        unique_users = len(set(mapping['user_id'] for mapping in message_mappings.values()))
        total_admins = len(admin_users)
        total_blocked = len(blocked_users)
        
        status_text = f"""📊 Статус бота:

👥 Админов: {total_admins}
💬 Активных диалогов: {unique_users}
📨 Активных маппингов: {active_mappings}
🚫 Заблокированных: {total_blocked}
🔄 Перезапусков: {self_healing.restart_count}
🔄 Бот работает автономно 24/7

Используйте /panel для управления"""
        send_message(user_id, status_text)
    
    elif text == "/panel" and is_admin(user_id):
        handle_admin_commands(user_id, text)

def get_updates(offset: int = 0) -> Optional[Dict]:
    """Получить обновления от Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 30}
    
    try:
        response = requests.get(url, params=params, timeout=35)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"API error {response.status_code}: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Ошибка получения обновлений: {e}")
        self_healing.handle_error(e)
        return None

def signal_handler(signum, frame):
    """Обработчик сигналов"""
    logger.info(f"Получен сигнал {signum}, корректное завершение...")
    keep_alive.stop()
    
    # Уведомляем админов
    for admin_id in admin_users:
        try:
            send_message(admin_id, "⏹️ Бот завершил работу")
        except:
            pass
    
    sys.exit(0)

def main():
    """Основная функция автономного бота"""
    # Регистрируем обработчики сигналов
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    if not BOT_TOKEN or not ADMIN_USER_ID:
        logger.error("❌ Не установлены BOT_TOKEN или ADMIN_USER_ID")
        return
    
    logger.info("🤖 Запуск автономного Telegram бота...")
    logger.info(f"👤 Главный админ: {ADMIN_USER_ID}")
    logger.info("🔄 Режим 24/7 с автовосстановлением")
    
    # Запускаем keep-alive
    keep_alive.start()
    
    # Уведомляем админов о запуске
    for admin_id in admin_users:
        try:
            send_message(admin_id, "🚀 Автономный бот запущен и готов к работе 24/7")
        except:
            pass
    
    offset = 0
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while True:
        try:
            updates = get_updates(offset)
            if not updates:
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    logger.warning("⚠️ Много ошибок подряд, короткая пауза...")
                    time.sleep(10)
                    consecutive_errors = 0
                continue
            
            if not updates.get("ok"):
                logger.warning(f"⚠️ API ошибка: {updates.get('description')}")
                time.sleep(3)
                continue
            
            consecutive_errors = 0  # Сбрасываем счетчик при успехе
            
            for update in updates.get("result", []):
                try:
                    update_id = update.get("update_id")
                    
                    if "message" in update:
                        message = update["message"]
                        text = message.get("text", "")
                        
                        if text.startswith("/"):
                            handle_command(update)
                        else:
                            handle_message(update)
                    
                    offset = max(offset, update_id + 1)
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка обработки обновления: {e}")
                    continue
        
        except KeyboardInterrupt:
            logger.info("⏹️ Остановка по запросу пользователя")
            break
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"💥 Критическая ошибка: {e}")
            
            if consecutive_errors >= max_consecutive_errors:
                logger.critical("🚨 Слишком много критических ошибок")
                self_healing.self_restart()
            
            time.sleep(5)
    
    keep_alive.stop()
    logger.info("🔚 Автономный бот завершил работу")

if __name__ == "__main__":
    main()
