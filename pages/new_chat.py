import streamlit as st
import requests
import json
import os
from PIL import Image
import hashlib
from utils.utils import verify_user_access, update_remaining_generations, get_data_file_path
from utils.chat_database import ChatDatabase
from tinydb import TinyDB, Query
from googletrans import Translator
from datetime import datetime
from utils.page_config import setup_pages
import time
from utils.translation import translate_text, display_message_with_translation
from flowise import Flowise, PredictionData
import uuid
from langdetect import detect
from utils.database.database_manager import get_database

def generate_response(prompt: str, chat_id: str, session_id: str):
    try:
        # Создаем клиент Flowise, если он еще не создан
        if not hasattr(st.session_state, 'flowise_client'):
            base_url = st.secrets["flowise"]["base_url"].replace('/api/v1/prediction', '')
            st.session_state.flowise_client = Flowise(base_url=base_url)
        
        try:
            # Отправляем запрос к API
            prediction_data = PredictionData(
                question=prompt,
                chatId=chat_id,
                sessionId=session_id,
                overrideConfig={}
            )
            
            response = st.session_state.flowise_client.predict(
                chat_id,
                prediction_data
            )
            
            if isinstance(response, dict):
                text_response = response.get('text', '')
            else:
                text_response = str(response)
            
            if text_response:
                try:
                    detected_lang = detect(text_response)
                    if detected_lang == 'en':
                        translator = Translator()
                        translated = translator.translate(text_response, dest='ru')
                        if translated and translated.text:
                            return translated.text
                        else:
                            return text_response
                    else:
                        return text_response
                except Exception as e:
                    print(f"Ошибка при переводе: {str(e)}")
                    return text_response
            
            return "Не удалось получить ответ в ожидаемом формате. Пожалуйста, попробуйте еще раз."
                    
        except Exception as e:
            if "Unknown model" in str(e):
                return "Ошибка конфигурации модели. Пожалуйста, проверьте настройки чата."
            return f"Ошибка при получении ответа: {str(e)}"
            
    except Exception as e:
        return f"Произошла ошибка: {str(e)}"

# Получаем экземпляр базы данных
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

# Проверка аутентификации
if "authenticated" not in st.session_state or not st.session_state.authenticated:
    st.warning("Пожалуйста, войдите в систему")
    st.stop()

# Проверка конфигурации Flowise
if "flowise" not in st.secrets or "base_url" not in st.secrets["flowise"]:
    st.error("Ошибка: URL Flowise API не настроен в secrets.toml")
    st.stop()

# Получаем базовый URL и очищаем его от /api/v1/prediction
base_url = st.secrets["flowise"]["base_url"].replace('/api/v1/prediction', '')

# Создаем клиент Flowise
flowise_client = Flowise(base_url=base_url)

# Инициализируем уникальный идентификатор сессии для пользователя
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = []
else:
    # Загружаем историю при первом запуске
    if "messages" not in st.session_state:
        st.session_state.messages = []

# Инициализация директории для хранения историй сессий
HISTORY_DIR = os.path.join(os.path.dirname(__file__), '..', 'chat_history')
if not os.path.exists(HISTORY_DIR):
    os.makedirs(HISTORY_DIR)

def get_session_display_name(username: str, flow_id: str, session_id: str) -> str:
    """Получает отображаемое имя сессии"""
    session = db.chat_sessions.find_one({
        "username": username,
        "flow_id": flow_id,
        "session_id": session_id
    })
    return session.get("name", f"Сессия {session_id[:8]}") if session else f"Сессия {session_id[:8]}"

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
    
    # Обновляем или создаем сессию
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

def load_session_history(username: str, flow_id: str, session_id: str) -> list:
    """Загружает историю сессии из MongoDB"""
    history = db.chat_history.find_one({
        "username": username,
        "flow_id": flow_id,
        "session_id": session_id
    })
    return history.get("messages", []) if history else []

def get_available_sessions(username: str, flow_id: str) -> list:
    """Получает список доступных сессий для чата"""
    sessions = list(db.chat_sessions.find({
        "username": username,
        "flow_id": flow_id
    }).sort("created_at", 1))
    
    return [{
        'id': session['session_id'],
        'display_name': session.get('name', f"Сессия {session['session_id'][:8]}")
    } for session in sessions]

def rename_session(username: str, flow_id: str, session_id: str, new_name: str):
    """Переименовывает сессию"""
    try:
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
        st.success(f"Сессия успешно переименована в '{new_name}'")
        st.rerun()
        return True
    except Exception as e:
        st.error(f"Ошибка при переименовании сессии: {e}")
        return False

def delete_session(username: str, flow_id: str, session_id: str):
    """Удаляет сессию полностью"""
    try:
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
        
        # Если удалена текущая сессия, переключаемся на первую доступную
        if ('current_chat_flow' in st.session_state and 
            'current_session' in st.session_state.current_chat_flow and 
            st.session_state.current_chat_flow['current_session'] == session_id):
            
            # Получаем список оставшихся сессий
            available_sessions = get_available_sessions(username, flow_id)
            
            if available_sessions:
                # Переключаемся на первую доступную сессию
                st.session_state.current_chat_flow['current_session'] = available_sessions[0]['id']
            else:
                # Если сессий нет, создаем новую
                new_session_id = str(uuid.uuid4())
                st.session_state.current_chat_flow['current_session'] = new_session_id
                save_session_history(username, flow_id, new_session_id, [])
        
        st.rerun()
    except Exception as e:
        st.error(f"Ошибка при удалении сессии: {e}")

def clear_session_history(username: str, flow_id: str, session_id: str):
    """Очищает историю конкретной сессии"""
    try:
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
        
        # Очищаем состояние сообщений в текущей сессии
        st.session_state.messages = []
        st.rerun()
    except Exception as e:
        st.error(f"Ошибка при очистке истории: {e}")

def get_message_hash(role, content):
    """Создает уникальный хэш для сообщения"""
    return hashlib.md5(f"{role}:{content}".encode()).hexdigest()

def get_user_profile_image(username):
    """Получение изображения профиля пользователя"""
    user_data = db.get_user(username)
    if user_data and user_data.get('profile_image'):
        try:
            return Image.open(user_data['profile_image'])
        except Exception:
            return "👤"
    return "👤"

def display_message(message, role):
    """Отображает сообщение"""
    avatar = "🤖" if role == "assistant" else get_user_profile_image(st.session_state.username)
    with st.chat_message(role, avatar=avatar):
        st.markdown(message["content"])

def save_chat_flow(username, flow_id, flow_name=None):
    """Сохранение нового чат-потока"""
    if not flow_name:
        flow_name = f"Чат {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    # Создаем новую сессию
    session_id = str(uuid.uuid4())
    
    # Сохраняем информацию о чат-потоке
    db.chat_sessions.insert_one({
        "username": username,
        "flow_id": flow_id,
        "session_id": session_id,
        "name": flow_name,
        "created_at": datetime.now(),
        "updated_at": datetime.now()
    })
    
    # Обновляем данные пользователя
    db.users.update_one(
        {"username": username},
        {
            "$push": {
                "chat_flows": {
                    "id": flow_id,
                    "name": flow_name,
                    "created_at": datetime.now(),
                    "current_session": session_id
                }
            }
        }
    )
    
    return True

def get_user_chat_flows(username):
    """Получение списка чат-потоков пользователя"""
    user = db.get_user(username)
    return user.get('chat_flows', []) if user else []

def delete_chat_flow(username, flow_id):
    """Удаление чат-потока"""
    try:
        # Удаляем все сессии чата
        db.chat_sessions.delete_many({
            "username": username,
            "flow_id": flow_id
        })
        
        # Удаляем всю историю чата
        db.chat_history.delete_many({
            "username": username,
            "flow_id": flow_id
        })
        
        # Удаляем чат из списка чатов пользователя
        db.users.update_one(
            {"username": username},
            {"$pull": {"chat_flows": {"id": flow_id}}}
        )
        
        return True
    except Exception as e:
        st.error(f"Ошибка при удалении чата: {e}")
        return False

# Отображение оставшихся генераций
user_data = db.get_user(st.session_state.username)
if user_data:
    remaining_generations = user_data.get('remaining_generations', 0)
    st.sidebar.metric("Осталось генераций:", remaining_generations)
    
    if remaining_generations <= 0:
        st.error("У вас закончились генераций. Пожалуйста, активируйте новый токен.")
        st.stop()

# Управление чат-потоками в боковой панели
st.sidebar.title("Управление чат-потоками")

# Выбор существующего чат-потока
chat_flows = get_user_chat_flows(st.session_state.username)
if chat_flows:
    flow_names = [flow['name'] for flow in chat_flows]
    
    # Определяем текущий индекс
    current_index = 0
    if 'current_chat_flow' in st.session_state:
        try:
            current_flow_name = st.session_state.current_chat_flow['name']
            if current_flow_name in flow_names:
                current_index = flow_names.index(current_flow_name)
        except:
            current_index = 0
    
    selected_flow_name = st.sidebar.radio(
        "Выберите чат:",
        flow_names,
        index=current_index
    )
    
    selected_flow = next(
        (flow for flow in chat_flows if flow['name'] == selected_flow_name),
        chat_flows[0]
    )
    
    # Проверяем, изменился ли выбранный чат
    if ('current_chat_flow' not in st.session_state or 
        st.session_state.current_chat_flow['id'] != selected_flow['id']):
        st.session_state.current_chat_flow = selected_flow
        if "message_hashes" in st.session_state:
            del st.session_state.message_hashes
        st.rerun()

# Создание нового чат-потока
st.sidebar.markdown("---")
with st.sidebar.expander("Создать новый чат"):
    new_flow_name = st.text_input("Название чата:")
    new_flow_id = st.text_input(
        "ID чат-потока:",
        help="Введите ID чата или закажите сборку в https://t.me/startintellect"
    )
    
    if st.button("Создать") and new_flow_id:
        if save_chat_flow(st.session_state.username, new_flow_id, new_flow_name):
            st.session_state.current_chat_flow = {
                'id': new_flow_id,
                'name': new_flow_name or f"Чат {len(chat_flows) + 1}"
            }
            st.success("Новый чат создан!")
            st.rerun()

# Управление текущим чатом
if 'current_chat_flow' in st.session_state:
    st.title(f"💬 {st.session_state.current_chat_flow['name']}")
    
    # Управление сессиями
    available_sessions = get_available_sessions(
        st.session_state.username,
        st.session_state.current_chat_flow['id']
    )
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        if available_sessions:
            session_map = {session['display_name']: session['id'] for session in available_sessions}
            display_names = list(session_map.keys())
            
            current_display_name = next(
                (session['display_name'] for session in available_sessions 
                 if session['id'] == st.session_state.current_chat_flow.get('current_session')),
                display_names[0] if display_names else None
            )
            
            selected_display_name = st.selectbox(
                "Выберите сессию:",
                display_names,
                index=display_names.index(current_display_name) if current_display_name in display_names else 0
            )
            
            selected_session_id = session_map[selected_display_name]
            
            if selected_session_id != st.session_state.current_chat_flow.get('current_session'):
                st.session_state.current_chat_flow['current_session'] = selected_session_id
                st.rerun()
    
    with col2:
        st.markdown(
            """
            <style>
            div[data-testid="column"] button {
                width: 100%;
                margin: 5px 0;
                min-height: 45px;
            }
            </style>
            """, 
            unsafe_allow_html=True
        )
        
        if st.button("💫 Новая сессия"):
            new_session_id = str(uuid.uuid4())
            st.session_state.current_chat_flow['current_session'] = new_session_id
            save_session_history(
                st.session_state.username,
                st.session_state.current_chat_flow['id'],
                new_session_id,
                []
            )
            st.rerun()
        
        if st.button("🧹 Очистить"):
            clear_session_history(
                st.session_state.username,
                st.session_state.current_chat_flow['id'],
                st.session_state.current_chat_flow['current_session']
            )
        
        if st.button("🗑 Удалить"):
            delete_session(
                st.session_state.username,
                st.session_state.current_chat_flow['id'],
                st.session_state.current_chat_flow['current_session']
            )
    
    st.markdown("---")
    
    # Отображение истории чата
    if 'current_session' in st.session_state.current_chat_flow:
        messages = load_session_history(
            st.session_state.username,
            st.session_state.current_chat_flow['id'],
            st.session_state.current_chat_flow['current_session']
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
        # Сохраняем сообщение пользователя
        messages = load_session_history(
            st.session_state.username,
            st.session_state.current_chat_flow['id'],
            st.session_state.current_chat_flow['current_session']
        )
        
        user_message = {
            "role": "user",
            "content": user_input,
            "timestamp": datetime.now().isoformat()
        }
        messages.append(user_message)
        
        save_session_history(
            st.session_state.username,
            st.session_state.current_chat_flow['id'],
            st.session_state.current_chat_flow['current_session'],
            messages
        )
        
        # Получаем ответ от модели
        with st.chat_message("assistant", avatar="🤖"):
            response = generate_response(
                user_input,
                st.session_state.current_chat_flow['id'],
                st.session_state.current_chat_flow['current_session']
            )
            st.write(response)
        
        # Сохраняем ответ ассистента
        assistant_message = {
            "role": "assistant",
            "content": response,
            "timestamp": datetime.now().isoformat()
        }
        messages.append(assistant_message)
        
        save_session_history(
            st.session_state.username,
            st.session_state.current_chat_flow['id'],
            st.session_state.current_chat_flow['current_session'],
            messages
        )
        
        # Обновляем количество оставшихся генераций
        db.users.update_one(
            {"username": st.session_state.username},
            {"$inc": {"remaining_generations": -1}}
        )
        
        st.rerun()

else:
    st.info("Создайте новый чат для начала общения")

# Функция отправки сообщения
def display_timer():
    """Отображает анимированный секундомер"""
    placeholder = st.empty()
    for seconds in range(60):
        time_str = f"⏱️ {seconds}с"
        placeholder.markdown(f"""
            <div style='animation: blink 1s infinite'>
                {time_str}
            </div>
            <style>
                div {{
                    font-size: 1.2em;
                    font-weight: bold;
                    color: #1E88E5;
                    padding: 10px;
                    border-radius: 8px;
                    background-color: #E3F2FD;
                    text-align: center;
                }}
                @keyframes blink {{
                    0%, 100% {{ opacity: 1.0 }}
                    50% {{ opacity: 0.5 }}
                }}
            </style>
        """, unsafe_allow_html=True)
        time.sleep(1)
        if not st.session_state.get('waiting_response', True):
            break
    placeholder.empty()