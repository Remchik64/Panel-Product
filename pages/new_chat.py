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
from utils.page_config import setup_pages, PAGE_CONFIG, check_token_access
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
    page_title=PAGE_CONFIG["new_chat"]["name"],
    page_icon=PAGE_CONFIG["new_chat"]["icon"],
    layout="wide",
    initial_sidebar_state="expanded"
)

# Настройка страниц
setup_pages()

# Проверка аутентификации
if "authenticated" not in st.session_state or not st.session_state.authenticated:
    st.warning("Пожалуйста, войдите в систему")
    st.switch_page("registr.py")
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
    # Сначала получаем основную сессию
    primary_session = db.chat_sessions.find_one({
        "username": username,
        "flow_id": flow_id,
        "is_primary": True
    })
    
    # Затем получаем все остальные сессии
    other_sessions = list(db.chat_sessions.find({
        "username": username,
        "flow_id": flow_id,
        "is_primary": {"$ne": True}
    }).sort("created_at", 1))
    
    sessions = []
    
    # Добавляем основную сессию первой
    if primary_session:
        sessions.append({
            'id': primary_session['session_id'],
            'display_name': primary_session.get('name', "Основная сессия"),
            'is_primary': True
        })
    
    # Добавляем остальные сессии
    sessions.extend([{
        'id': session['session_id'],
        'display_name': session.get('name', f"Сессия {session['session_id'][:8]}"),
        'is_primary': False
    } for session in other_sessions])
    
    return sessions

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
    """Удаляет сессию"""
    try:
        # Проверяем, не является ли сессия основной
        session = db.chat_sessions.find_one({
            "username": username,
            "flow_id": flow_id,
            "session_id": session_id
        })
        
        if session and session.get('is_primary'):
            st.error("Основная сессия не может быть удалена")
            return False
        
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
        
        # Если удалена текущая сессия, переключаемся на основную сессию
        if ('current_chat_flow' in st.session_state and 
            'current_session' in st.session_state.current_chat_flow and 
            st.session_state.current_chat_flow['current_session'] == session_id):
            
            # Находим основную сессию
            primary_session = db.chat_sessions.find_one({
                "username": username,
                "flow_id": flow_id,
                "is_primary": True
            })
            
            if primary_session:
                st.session_state.current_chat_flow['current_session'] = primary_session['session_id']
        
        st.rerun()
        return True
        
    except Exception as e:
        st.error(f"Ошибка при удалении сессии: {e}")
        return False

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
    try:
        if not flow_name:
            flow_name = f"Помощник {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        # Создаем первую сессию
        session_id = str(uuid.uuid4())
        
        # Проверяем существование пользователя
        user = db.get_user(username)
        if not user:
            return False
        
        # Проверяем, не существует ли уже такой flow_id
        existing_flow = next((flow for flow in user.get('chat_flows', []) if flow['id'] == flow_id), None)
        if existing_flow:
            return False
        
        # Сохраняем информацию о первой сессии
        db.chat_sessions.insert_one({
            "username": username,
            "flow_id": flow_id,
            "session_id": session_id,
            "name": "Основная сессия",  # Фиксированное имя для первой сессии
            "is_primary": True,  # Флаг основной сессии
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        })
        
        # Создаем новый чат-поток в списке пользователя
        new_flow = {
            "id": flow_id,
            "name": flow_name,
            "created_at": datetime.now(),
            "current_session": session_id
        }
        
        # Обновляем данные пользователя
        result = db.users.update_one(
            {"username": username},
            {
                "$push": {
                    "chat_flows": new_flow
                }
            }
        )
        
        return result.matched_count > 0
        
    except Exception as e:
        print(f"Ошибка при создании помощника: {str(e)}")
        return False

def get_user_chat_flows(username):
    """Получение списка чат-потоков пользователя"""
    try:
        user = db.get_user(username)
        if not user:
            return []
        
        chat_flows = user.get('chat_flows', [])
        
        # Проверяем и добавляем current_session если его нет
        for flow in chat_flows:
            if 'current_session' not in flow:
                # Ищем существующую сессию
                session = db.chat_sessions.find_one({
                    "username": username,
                    "flow_id": flow['id']
                })
                
                if session:
                    flow['current_session'] = session['session_id']
                else:
                    # Создаем новую сессию
                    new_session_id = str(uuid.uuid4())
                    flow['current_session'] = new_session_id
                    save_session_history(username, flow['id'], new_session_id, [])
        
        # Сортируем по дате создания (новые сверху)
        chat_flows.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return chat_flows
        
    except Exception as e:
        print(f"Ошибка при получении списка помощников: {str(e)}")
        return []

def delete_chat_flow(username, flow_id):
    """Удаление чат-потока и всех связанных данных"""
    try:
        # Удаляем все сессии и историю чатов
        db.chat_sessions.delete_many({"username": username, "flow_id": flow_id})
        db.chat_history.delete_many({"username": username, "flow_id": flow_id})
        
        # Удаляем помощника из списка у пользователя
        result = db.users.update_one(
            {"username": username},
            {"$pull": {"chat_flows": {"id": flow_id}}}
        )
        
        return result.modified_count > 0
        
    except Exception as e:
        print(f"Ошибка при удалении помощника: {str(e)}")
        return False

# Отображение оставшихся генераций
user_data = db.get_user(st.session_state.username)
if user_data:
    remaining_generations = user_data.get('remaining_generations', 0)
    st.sidebar.metric("Осталось генераций:", remaining_generations)
    
    if remaining_generations <= 0:
        st.error("У вас закончились генераций. Пожалуйста, активируйте новый токен.")
        st.stop()

# В боковом меню
with st.sidebar:
    st.title("Управление чат-потоками")
    
    # Выбор существующего чат-потока
    chat_flows = get_user_chat_flows(st.session_state.username)
    if chat_flows:
        st.subheader("📚 Ваши помощники")
        
        # Создаем словарь для маппинга помощников
        flow_options = {f"{flow.get('name', 'Без имени')} ({flow['id']})": flow for flow in chat_flows}
        
        # Определяем текущий выбор
        current_selection = None
        if 'current_chat_flow' in st.session_state:
            current_flow = st.session_state.current_chat_flow
            current_selection = f"{current_flow.get('name', 'Без имени')} ({current_flow['id']})"
        
        selected_option = st.selectbox(
            "Выберите помощника:",
            options=list(flow_options.keys()),
            index=list(flow_options.keys()).index(current_selection) if current_selection in flow_options else 0,
            key="flow_selector"
        )
        
        # Обновляем текущего помощника при изменении выбора
        selected_flow = flow_options[selected_option]
        if ('current_chat_flow' not in st.session_state or 
            st.session_state.current_chat_flow.get('id') != selected_flow['id']):
            st.session_state.current_chat_flow = selected_flow
            st.rerun()
    
    st.markdown("<hr style='margin: 10px 0px; border: none; height: 1px; background: rgba(250, 250, 250, 0.2);'>", unsafe_allow_html=True)
    
    # Создание нового чат-потока
    with st.expander("✨ Создать нового помощника", expanded=True):
        new_flow_name = st.text_input("Название помощника:", key="new_flow_name")
        new_flow_id = st.text_input(
            "ID помощника:",
            help="Введите ID чата или закажите сборку в https://t.me/startintellect",
            key="new_flow_id"
        )
        
        if st.button("Создать", use_container_width=True, key="create_flow_button"):
            if not new_flow_id:
                st.error("Введите ID помощника")
            else:
                try:
                    # Создаем нового помощника
                    if save_chat_flow(st.session_state.username, new_flow_id, new_flow_name):
                        # Создаем новую сессию
                        new_session_id = str(uuid.uuid4())
                        
                        # Обновляем состояние
                        st.session_state.current_chat_flow = {
                            'id': new_flow_id,
                            'name': new_flow_name or f"Помощник {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                            'current_session': new_session_id
                        }
                        
                        # Инициализируем пустую историю для новой сессии
                        save_session_history(
                            st.session_state.username,
                            new_flow_id,
                            new_session_id,
                            []
                        )
                        
                        st.success("✨ Новый помощник успешно создан!")
                        time.sleep(1)  # Даем время увидеть сообщение об успехе
                        st.rerun()
                    else:
                        st.error("Не удалось создать помощника")
                except Exception as e:
                    st.error(f"Ошибка при создании помощника: {str(e)}")
    
    st.markdown("<hr style='margin: 5px 0px; border: none; height: 1px; background: rgba(250, 250, 250, 0.2);'>", unsafe_allow_html=True)
    
    # Управление текущим помощником
    if 'current_chat_flow' in st.session_state:
        with st.expander("⚙️ Управление помощником", expanded=True):
            # Переименование помощника
            new_name = st.text_input("Новое название:", value=st.session_state.current_chat_flow.get('name', ''), key="rename_flow_input")
            if st.button("✏️ Переименовать", use_container_width=True, key="rename_flow_button") and new_name:
                try:
                    # Обновляем имя в списке чатов пользователя
                    result = db.users.update_one(
                        {
                            "username": st.session_state.username,
                            "chat_flows.id": st.session_state.current_chat_flow['id']
                        },
                        {
                            "$set": {
                                "chat_flows.$.name": new_name
                            }
                        }
                    )
                    
                    if result.modified_count > 0:
                        st.session_state.current_chat_flow['name'] = new_name
                        st.success("Помощник успешно переименован")
                        st.rerun()
                    else:
                        st.error("Не удалось переименовать помощника")
                except Exception as e:
                    st.error(f"Ошибка при переименовании помощника: {str(e)}")
            
            st.markdown("---")
            
            # Удаление помощника
            st.warning("⚠️ Удаление помощника")
            delete_confirmed = st.checkbox("Подтвердить удаление", key="confirm_delete")
            if delete_confirmed:
                if st.button("🗑️ Удалить помощника", type="primary", use_container_width=True, key="delete_flow"):
                    if delete_chat_flow(st.session_state.username, st.session_state.current_chat_flow['id']):
                        if 'current_chat_flow' in st.session_state:
                            del st.session_state.current_chat_flow
                        st.success("Помощник успешно удален")
                        st.rerun()
                    else:
                        st.error("Не удалось удалить помощника")
    
    st.markdown("<hr style='margin: 5px 0px; border: none; height: 1px; background: rgba(250, 250, 250, 0.2);'>", unsafe_allow_html=True)
    
    # Загрузка файлов
    with st.expander("📎 Загрузка файлов", expanded=False):
        uploaded_files = st.file_uploader(
            "Загрузите файлы",
            accept_multiple_files=True,
            type=["png", "jpg", "jpeg", "pdf", "doc", "docx", "txt"]
        )
        if uploaded_files:
            st.success(f"Загружено файлов: {len(uploaded_files)}")

# Управление текущим чатом
if 'current_chat_flow' in st.session_state:
    st.title(f"💬 {st.session_state.current_chat_flow['name']}")
    
    # Управление чатами
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
                "Выберите чат:",
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
                padding: 0.5rem;
            }
            div.row-widget.stButton {
                margin-bottom: 10px;
            }
            </style>
            """, 
            unsafe_allow_html=True
        )
        
        # Кнопка нового чата
        if st.button("💫 Новый чат", use_container_width=True, key="new_chat_button"):
            new_session_id = str(uuid.uuid4())
            st.session_state.current_chat_flow['current_session'] = new_session_id
            save_session_history(
                st.session_state.username,
                st.session_state.current_chat_flow['id'],
                new_session_id,
                []
            )
            st.rerun()
        
        # Кнопка переименования чата
        current_session = st.session_state.current_chat_flow.get('current_session')
        if current_session:
            # Получаем информацию о текущей сессии
            current_session_info = next(
                (session for session in available_sessions 
                 if session['id'] == current_session),
                None
            )
            
            if current_session_info:
                current_name = current_session_info['display_name']
                is_primary = current_session_info.get('is_primary', False)
                
                # Для основной сессии показываем специальное сообщение
                if is_primary:
                    st.info("Основная сессия")
                
                if st.button("✏️ Переименовать", use_container_width=True, key="rename_chat_button"):
                    new_name = st.text_input("Новое название:", value=current_name, key="rename_chat_input")
                    if new_name and new_name != current_name:
                        rename_session(
                            st.session_state.username,
                            st.session_state.current_chat_flow['id'],
                            current_session,
                            new_name
                        )
        
        # Кнопка очистки
        if st.button("🧹 Очистить", use_container_width=True, key="clear_chat_button"):
            if current_session:
                clear_session_history(
                    st.session_state.username,
                    st.session_state.current_chat_flow['id'],
                    current_session
                )
        
        # Кнопка удаления (только для не основных сессий)
        if current_session_info and not is_primary:
            if st.button("🗑 Удалить", use_container_width=True, key="delete_chat_button"):
                delete_session(
                    st.session_state.username,
                    st.session_state.current_chat_flow['id'],
                    current_session
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
        send_button = st.button("Отправить", use_container_width=True, key="send_message_button")
    with col2:
        clear_button = st.button("Очистить", on_click=lambda: setattr(st.session_state, 'message_input', ''), use_container_width=True, key="clear_message_button")
    with col3:
        cancel_button = st.button("Отменить", on_click=lambda: setattr(st.session_state, 'message_input', ''), use_container_width=True, key="cancel_message_button")
    
    if send_button and user_input and user_input.strip():
        # Проверяем наличие активного токена перед отправкой
        check_token_access()

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