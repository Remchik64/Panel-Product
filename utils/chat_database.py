from tinydb import TinyDB, Query
import os
from datetime import datetime
import hashlib
import json

def get_message_hash(role, content):
    """Создает уникальный хэш для сообщения"""
    return hashlib.md5(f"{role}:{content}".encode()).hexdigest()

class ChatDatabase:
    def __init__(self, chat_id):
        # Создаем директорию chat_history, если её нет
        chat_history_dir = 'chat_history'
        if not os.path.exists(chat_history_dir):
            os.makedirs(chat_history_dir)
        
        # Создаем путь к файлу в папке chat_history
        self.db_path = os.path.join(chat_history_dir, f'{chat_id}.json')
        
        # Проверяем существование файла и его содержимое
        if not os.path.exists(self.db_path):
            # Создаем пустой файл с правильной структурой
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump({"_default": {}}, f, ensure_ascii=False, indent=2)
        
        # Инициализируем TinyDB с указанием кодировки
        self.db = TinyDB(self.db_path, encoding='utf-8')
        
    def add_message(self, role, content):
        try:
            self.db.insert({
                'role': role,
                'content': content,
                'timestamp': datetime.now().isoformat()
            })
        except Exception as e:
            print(f"Ошибка при добавлении сообщения: {e}")
        
    def get_history(self):
        try:
            return self.db.all()
        except Exception as e:
            print(f"Ошибка при получении истории: {e}")
            return []
        
    def clear_history(self):
        try:
            # Сначала закрываем текущее соединение
            self.db.close()
            
            # Создаем новый пустой файл
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump({"_default": {}}, f, ensure_ascii=False, indent=2)
            
            # Переоткрываем базу данных
            self.db = TinyDB(self.db_path, encoding='utf-8')
        except Exception as e:
            print(f"Ошибка при очистке истории: {e}")
        
    def delete_message(self, message_hash):
        """Удаляет конкретное сообщение из истории чата по его хэшу"""
        try:
            history = self.get_history()
            updated_history = [msg for msg in history if get_message_hash(msg["role"], msg["content"]) != message_hash]
            
            # Очищаем базу и добавляем обновленную историю
            self.clear_history()
            for msg in updated_history:
                self.add_message(msg["role"], msg["content"])
        except Exception as e:
            print(f"Ошибка при удалении сообщения: {e}")            
    def __del__(self):
        """Деструктор для закрытия соединения с базой данных"""
        try:
            self.db.close()
        except:
            pass
