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
from utils.page_config import setup_pages, PAGE_CONFIG, check_token_access
import time
from utils.translation import translate_text, display_message_with_translation
from flowise import Flowise, PredictionData
import uuid
from utils.database.database_manager import get_database

def save_session_history(username: str, flow_id: str, session_id: str, messages: list, display_name: str = None):
    """Сохраняет историю сессии в MongoDB"""
    if not display_name:
        display_name = f"Сессия {len(get_available_sessions(username, flow_id)) + 1}"
    
    db.chat_history.update_one(
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
    
    db.chat_sessions.update_one(
        {
            "username": username,
            "flow_id": flow_id,
            "session_id": session_id
        },
        {
            "$set": {
                "name": display_name,
                "updated_at": datetime.now()
            },
            "$setOnInsert": {
                "created_at": datetime.now()
            }
        },
        upsert=True
    )

def get_available_sessions(username: str, flow_id: str) -> list:
    """Получение доступных сессий чата"""
    sessions = list(db.chat_sessions.find({
        "username": username,
        "flow_id": flow_id
    }).sort("created_at", 1))
    
    return [{
        'id': session['session_id'],
        'display_name': session.get('name', f"Сессия {session['session_id'][:8]}")
    } for session in sessions]

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
    db.chat_sessions.delete_one({
        "username": username,
        "flow_id": flow_id,
        "session_id": session_id
    })
    
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
        prediction_data = PredictionData(
            question=prompt,
            chat_id=chat_id,
            session_id=session_id,
            files=uploaded_files
        )
        flowise = Flowise()
        response = flowise.predict(prediction_data)
        return response["text"] if response and "text" in response else "Извините, произошла ошибка при генерации ответа."
    except Exception as e:
        print(f"Ошибка при генерации ответа: {str(e)}")
        return "Произошла ошибка при обработке запроса."

def submit_message(user_input, uploaded_files=None):
    """Обработка отправки сообщения"""
    if not user_input.strip():
        return
        
    check_token_access()
    current_flow = st.session_state.get("current_flow")
    current_session = st.session_state.get("current_session")
    
    if not current_flow or not current_session:
        st.error("Ошибка: сессия не выбрана")
        return

    messages = db.get_chat_history(st.session_state["username"], current_flow, current_session)
    user_message = {
        "role": "user",
        "content": user_input,
        "timestamp": datetime.now().isoformat(),
        "message_id": get_message_hash("user", user_input)
    }
    messages.append(user_message)
    db.save_chat_history(st.session_state["username"], current_flow, current_session, messages)
    
    files_data = []
    if uploaded_files:
        for file in uploaded_files:
            file_content = file.read()
            mime_type = mimetypes.guess_type(file.name)[0]
            if mime_type and mime_type.startswith('image/'):
                files_data.append({
                    "name": file.name,
                    "type": mime_type,
                    "content": encode_file_to_base64(file_content)
                })
    
    assistant_response = generate_response(user_input, current_flow, current_session, files_data if files_data else None)
    assistant_message = {
        "role": "assistant",
        "content": assistant_response,
        "timestamp": datetime.now().isoformat(),
        "message_id": get_message_hash("assistant", assistant_response)
    }
    messages.append(assistant_message)
    db.save_chat_history(st.session_state["username"], current_flow, current_session, messages)
    db.chat_sessions.update_one(
        {
            "username": st.session_state["username"],
            "flow_id": current_flow,
            "session_id": current_session
        },
        {"$set": {"updated_at": datetime.now()}}
    )

def encode_file_to_base64(file_content: bytes) -> str:
    """Кодирование файла в base64"""
    return base64.b64encode(file_content).decode('utf-8')

# Получаем экземпляр менеджера базы данных
db = get_database()

# Настройка страницы
st.set_page_config(
    page_title=PAGE_CONFIG["app"]["name"],
    page_icon=PAGE_CONFIG["app"]["icon"],
    layout="wide",
    initial_sidebar_state="expanded"
)

# Настройка страниц
setup_pages()

# Проверка аутентификации
if "authenticated" not in st.session_state or not st.session_state.authenticated:
    st.warning("Пожалуйста, войдите в систему")
    st.switch_page("pages/registr.py")
    st.stop()

# Проверка наличия активного токена
user = db.get_user(st.session_state.username)
if not user or not user.get('active_token'):
    st.warning("Необходим активный токен")
    st.switch_page("pages/key_input.py")
    st.stop()

# Заголовок страницы
st.title(f"{PAGE_CONFIG['app']['icon']} {PAGE_CONFIG['app']['name']}")

# Отображение оставшихся генераций
user_data = db.get_user(st.session_state.username)
if user_data:
    remaining_generations = user_data.get('remaining_generations', 0)
    st.sidebar.metric("Осталось генераций:", remaining_generations)
    
    if remaining_generations <= 0:
        st.error("У вас закончились генераций. Пожалуйста, активируйте новый токен.")
        st.stop()

# Управление сессиями в боковой панели
st.sidebar.title("Управление сессиями")

# Получаем доступные сессии
available_sessions = get_available_sessions(st.session_state.username, "search")

col1, col2 = st.columns([3, 1])

with col1:
    if available_sessions:
        session_map = {session['display_name']: session['id'] for session in available_sessions}
        display_names = list(session_map.keys())
        
        current_session = st.session_state.get("current_session")
        current_display_name = next(
            (session['display_name'] for session in available_sessions 
             if session['id'] == current_session),
            display_names[0] if display_names else None
        )
        
        selected_display_name = st.selectbox(
            "Выберите сессию:",
            display_names,
            index=display_names.index(current_display_name) if current_display_name in display_names else 0
        )
        
        selected_session_id = session_map[selected_display_name]
        
        if selected_session_id != st.session_state.get("current_session"):
            st.session_state.current_session = selected_session_id
            st.session_state.current_flow = "search"
            st.rerun()

with col2:
    st.markdown(
        """
        <style>
        div[data-testid="column"] button {
            width: 100%;
            margin: 5px 0;
            min-height: 45px;
            padding: 0.5rem;
        }
        div.row-widget.stButton {
            margin-bottom: 10px;
        }
        </style>
        """, 
        unsafe_allow_html=True
    )
    
    # Кнопка новой сессии
    if st.button("💫 Новая сессия", use_container_width=True):
        new_session_id = str(uuid.uuid4())
        st.session_state.current_session = new_session_id
        st.session_state.current_flow = "search"
        save_session_history(
            st.session_state.username,
            "search",
            new_session_id,
            []
        )
        st.rerun()
    
    # Кнопка переименования
    if st.button("✏️ Переименовать", use_container_width=True):
        new_name = st.text_input("Новое название:", value=selected_display_name, key="rename_session")
        if new_name and new_name != selected_display_name:
            rename_session(
                st.session_state.username,
                "search",
                selected_session_id,
                new_name
            )
    
    # Кнопка очистки
    if st.button("🧹 Очистить", use_container_width=True):
        if st.session_state.get("current_session"):
            clear_session_history(
                st.session_state.username,
                "search",
                st.session_state.current_session
            )
    
    # Кнопка удаления
    if st.button("🗑 Удалить", use_container_width=True):
        if st.session_state.get("current_session"):
            delete_session(
                st.session_state.username,
                "search",
                st.session_state.current_session
            )

st.markdown("---")

# Отображение истории чата
if "current_session" in st.session_state:
    messages = db.get_chat_history(
        st.session_state.username,
        "search",
        st.session_state.current_session
    )
    
    for message in messages:
        display_message(message, message["role"])

# Поле ввода сообщения
user_input = st.text_area(
    "Введите ваше сообщение",
    height=100,
    key="message_input",
    placeholder="Введите текст сообщения здесь..."
)

col1, col2, col3 = st.columns(3)
with col1:
    send_button = st.button("Отправить", use_container_width=True)
with col2:
    clear_button = st.button("Очистить", on_click=lambda: setattr(st.session_state, 'message_input', ''), use_container_width=True)
with col3:
    cancel_button = st.button("Отменить", on_click=lambda: setattr(st.session_state, 'message_input', ''), use_container_width=True)

if send_button and user_input and user_input.strip():
    submit_message(user_input)
