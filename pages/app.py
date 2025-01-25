import streamlit as st
import requests
import json
import os
from PIL import Image
import hashlib
import base64
import mimetypes
from datetime import datetime
from googletrans import Translator
from utils.page_config import setup_pages
import time
from utils.translation import translate_text, display_message_with_translation
from flowise import Flowise, PredictionData
import uuid
from utils.database.database_manager import get_database

# Получаем экземпляр менеджера базы данных
db = get_database()

# Настройка страницы
st.set_page_config(
    page_title="Личный помощник",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Настройка страниц
setup_pages()

def get_available_sessions(username: str, flow_id: str) -> list:
    """Получение доступных сессий чата"""
    return db.get_chat_sessions(username, flow_id)

def rename_session(username: str, flow_id: str, session_id: str, new_name: str):
    """Переименование сессии чата"""
    db.chat_sessions.update_one(
        {
            "username": username,
            "flow_id": flow_id,
            "session_id": session_id
        },
        {
            "$set": {
                "name": new_name,
                "updated_at": datetime.now()
            }
        }
    )

def delete_session(username: str, flow_id: str, session_id: str):
    """Удаление сессии чата"""
    # Удаляем сессию
    db.chat_sessions.delete_one({
        "username": username,
        "flow_id": flow_id,
        "session_id": session_id
    })
    
    # Удаляем историю чата
    db.chat_history.delete_one({
        "username": username,
        "flow_id": flow_id,
        "session_id": session_id
    })

def clear_session_history(username: str, flow_id: str, session_id: str):
    """Очистка истории сессии"""
    db.chat_history.update_one(
        {
            "username": username,
            "flow_id": flow_id,
            "session_id": session_id
        },
        {
            "$set": {
                "messages": [],
                "updated_at": datetime.now()
            }
        }
    )

def get_message_hash(role, content):
    """Создание хэша сообщения"""
    return hashlib.md5(f"{role}:{content}".encode()).hexdigest()

def get_user_profile_image(username):
    """Получение изображения профиля пользователя"""
    user_data = db.get_user(username)
    if user_data and "profile_image" in user_data:
        return user_data["profile_image"]
    return None

def display_message(message, role):
    """Отображение сообщения в чате"""
    display_message_with_translation(message, role)

def save_chat_flow(username, flow_id, flow_name=None):
    """Сохранение потока чата"""
    if not flow_name:
        flow_name = f"Чат {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    session_id = str(uuid.uuid4())
    
    # Сохраняем сессию
    db.chat_sessions.insert_one({
        "username": username,
        "flow_id": flow_id,
        "session_id": session_id,
        "name": flow_name,
        "created_at": datetime.now(),
        "updated_at": datetime.now()
    })
    
    return session_id

def get_user_chat_flows(username):
    """Получение потоков чата пользователя"""
    return db.chat_sessions.find({"username": username})

def generate_response(prompt: str, chat_id: str, session_id: str, uploaded_files=None):
    """Генерация ответа от модели"""
    try:
        # Подготовка данных для отправки
        prediction_data = PredictionData(
            question=prompt,
            chat_id=chat_id,
            session_id=session_id,
            files=uploaded_files
        )
        
        # Получение ответа от Flowise
        flowise = Flowise()
        response = flowise.predict(prediction_data)
        
        if response and "text" in response:
            return response["text"]
        else:
            return "Извините, произошла ошибка при генерации ответа."
            
    except Exception as e:
        print(f"Ошибка при генерации ответа: {str(e)}")
        return "Произошла ошибка при обработке запроса."

def submit_message(user_input, uploaded_files=None):
    """Обработка отправки сообщения"""
    if not user_input.strip():
        return
    
    # Получаем текущую сессию
    current_flow = st.session_state.get("current_flow")
    current_session = st.session_state.get("current_session")
    
    if not current_flow or not current_session:
        st.error("Ошибка: сессия не выбрана")
        return
    
    # Получаем историю чата
    messages = db.get_chat_history(
        st.session_state["username"],
        current_flow,
        current_session
    )
    
    # Добавляем сообщение пользователя
    user_message = {
        "role": "user",
        "content": user_input,
        "timestamp": datetime.now().isoformat(),
        "message_id": get_message_hash("user", user_input)
    }
    messages.append(user_message)
    
    # Сохраняем обновленную историю
    db.save_chat_history(
        st.session_state["username"],
        current_flow,
        current_session,
        messages
    )
    
    # Обрабатываем загруженные файлы
    files_data = []
    if uploaded_files:
        for file in uploaded_files:
            file_content = file.read()
            mime_type = mimetypes.guess_type(file.name)[0]
            
            if mime_type and mime_type.startswith('image/'):
                # Для изображений конвертируем в base64
                encoded_file = encode_file_to_base64(file_content)
                files_data.append({
                    "name": file.name,
                    "type": mime_type,
                    "content": encoded_file
                })
    
    # Генерируем ответ
    assistant_response = generate_response(
        user_input,
        current_flow,
        current_session,
        files_data if files_data else None
    )
    
    # Добавляем ответ ассистента
    assistant_message = {
        "role": "assistant",
        "content": assistant_response,
        "timestamp": datetime.now().isoformat(),
        "message_id": get_message_hash("assistant", assistant_response)
    }
    messages.append(assistant_message)
    
    # Сохраняем финальную историю
    db.save_chat_history(
        st.session_state["username"],
        current_flow,
        current_session,
        messages
    )
    
    # Обновляем время последнего обновления сессии
    db.chat_sessions.update_one(
        {
            "username": st.session_state["username"],
            "flow_id": current_flow,
            "session_id": current_session
        },
        {
            "$set": {"updated_at": datetime.now()}
        }
    )

def encode_file_to_base64(file_content: bytes) -> str:
    """Кодирование файла в base64"""
    return base64.b64encode(file_content).decode('utf-8')
