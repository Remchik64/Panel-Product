import os
import json
from typing import Optional, Dict, List
from datetime import datetime
from pymongo import MongoClient
from redis import Redis
from tinydb import TinyDB
import streamlit as st

class DatabaseManager:
    def __init__(self):
        # MongoDB подключение
        self.mongo_client = MongoClient(st.secrets["mongodb"]["uri"])
        self.db = self.mongo_client[st.secrets["mongodb"]["database"]]
        
        # Redis подключение
        self.redis_client = Redis(
            host=st.secrets["redis"]["host"],
            port=st.secrets["redis"]["port"],
            password=st.secrets["redis"]["password"],
            decode_responses=True
        )
        
        # Коллекции MongoDB
        self.users = self.db.users
        self.chat_sessions = self.db.chat_sessions
        self.chat_history = self.db.chat_history
        
        # Создаем индексы
        self._setup_indexes()
    
    def _setup_indexes(self):
        """Настройка индексов для оптимизации запросов"""
        self.users.create_index("username", unique=True)
        self.chat_sessions.create_index([("username", 1), ("flow_id", 1)])
        self.chat_history.create_index([
            ("username", 1),
            ("flow_id", 1),
            ("session_id", 1)
        ])
    
    def get_user(self, username: str) -> Optional[Dict]:
        """Получение данных пользователя"""
        return self.users.find_one({"username": username})
    
    def update_user(self, username: str, data: Dict):
        """Обновление данных пользователя"""
        self.users.update_one(
            {"username": username},
            {"$set": data},
            upsert=True
        )
    
    def get_chat_sessions(self, username: str, flow_id: str) -> List[Dict]:
        """Получение сессий чата"""
        return list(self.chat_sessions.find({
            "username": username,
            "flow_id": flow_id
        }))
    
    def save_chat_history(self, username: str, flow_id: str, session_id: str, messages: List[Dict]):
        """Сохранение истории чата"""
        self.chat_history.update_one(
            {
                "username": username,
                "flow_id": flow_id,
                "session_id": session_id
            },
            {
                "$set": {
                    "messages": messages,
                    "updated_at": datetime.now()
                }
            },
            upsert=True
        )
    
    def get_chat_history(self, username: str, flow_id: str, session_id: str) -> List[Dict]:
        """Получение истории чата"""
        history = self.chat_history.find_one({
            "username": username,
            "flow_id": flow_id,
            "session_id": session_id
        })
        return history.get("messages", []) if history else []
    
    def cache_set(self, key: str, value: str, expire: int = 3600):
        """Сохранение данных в Redis"""
        self.redis_client.set(key, value, ex=expire)
    
    def cache_get(self, key: str) -> Optional[str]:
        """Получение данных из Redis"""
        return self.redis_client.get(key)

def migrate_from_tinydb():
    """Функция для миграции данных из TinyDB в MongoDB"""
    db_manager = DatabaseManager()
    
    # Путь к файлу TinyDB
    tinydb_path = os.path.join(os.path.dirname(__file__), '..', '..', 'user_database.json')
    old_db = TinyDB(tinydb_path)
    
    # Миграция пользователей
    for user in old_db.all():
        username = user.get('username')
        if username:
            # Обновляем формат данных если нужно
            user['migrated_at'] = datetime.now()
            db_manager.update_user(username, user)
    
    # Миграция историй чатов
    chat_history_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'chat_history')
    if os.path.exists(chat_history_dir):
        for filename in os.listdir(chat_history_dir):
            if filename.endswith('.json'):
                try:
                    # Парсим имя файла для получения данных
                    parts = filename[:-5].split('_')
                    if len(parts) >= 3:
                        username = parts[0]
                        flow_id = parts[1]
                        session_id = '_'.join(parts[2:])
                        
                        # Читаем историю из файла
                        with open(os.path.join(chat_history_dir, filename), 'r', encoding='utf-8') as f:
                            messages = json.load(f)
                            
                        # Сохраняем в MongoDB
                        db_manager.save_chat_history(username, flow_id, session_id, messages)
                except Exception as e:
                    print(f"Ошибка при миграции файла {filename}: {str(e)}")
    
    print("Миграция завершена успешно!")

# Singleton instance
_instance = None

def get_database():
    """Получение единственного экземпляра DatabaseManager"""
    global _instance
    if _instance is None:
        _instance = DatabaseManager()
    return _instance 