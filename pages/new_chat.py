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
    history_file = os.path.join(HISTORY_DIR, f"{username}_{flow_id}_{session_id}.json")
    try:
        with open(history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict) and 'display_name' in data:
                return data['display_name']
    except:
        pass
    return f"Сессия {session_id}"

def save_session_history(username: str, flow_id: str, session_id: str, messages: list, display_name: str = None):
    """Сохраняет историю сессии в файл"""
    history_file = os.path.join(HISTORY_DIR, f"{username}_{flow_id}_{session_id}.json")
    data = {
        'messages': messages,
        'display_name': display_name if display_name else f"Сессия {len(get_available_sessions(username, flow_id)) + 1}"
    }
    try:
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка при сохранении истории: {e}")

def load_session_history(username: str, flow_id: str, session_id: str) -> list:
    """Загружает историю сессии из файла"""
    history_file = os.path.join(HISTORY_DIR, f"{username}_{flow_id}_{session_id}.json")
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data.get('messages', [])
                return data
        except Exception as e:
            print(f"Ошибка при загрузке истории: {e}")
    return []

def get_available_sessions(username: str, flow_id: str) -> list:
    """Получает список доступных сессий для чата"""
    prefix = f"{username}_{flow_id}_"
    sessions = []
    for file in os.listdir(HISTORY_DIR):
        if file.startswith(prefix) and file.endswith('.json'):
            session_id = file[len(prefix):-5]  # Убираем префикс и .json
            display_name = get_session_display_name(username, flow_id, session_id)
            sessions.append({
                'id': session_id,
                'display_name': display_name
            })
    return sorted(sessions, key=lambda x: x['display_name'])

def rename_session(username: str, flow_id: str, session_id: str, new_name: str):
    """Переименовывает сессию"""
    try:
        history_file = os.path.join(HISTORY_DIR, f"{username}_{flow_id}_{session_id}.json")
        if os.path.exists(history_file):
            with open(history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if isinstance(data, dict):
                messages = data.get('messages', data)
            else:
                messages = data
            
            save_session_history(username, flow_id, session_id, messages, new_name)
            st.success(f"Сессия успешно переименована в '{new_name}'")
            st.rerun()
            return True
    except Exception as e:
        st.error(f"Ошибка при переименовании сессии: {e}")
        return False

def delete_session(username: str, flow_id: str, session_id: str):
    """Удаляет сессию полностью"""
    try:
        history_file = os.path.join(HISTORY_DIR, f"{username}_{flow_id}_{session_id}.json")
        if os.path.exists(history_file):
            os.remove(history_file)
            print(f"Удалена сессия: {username}_{flow_id}_{session_id}")
            
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
                    # Если сессий нет, создаем новую с ID 'session_1'
                    st.session_state.current_chat_flow['current_session'] = 'session_1'
                    save_session_history(username, flow_id, 'session_1', [], get_session_display_name(username, flow_id, 'session_1'))
            
            st.rerun()
    except Exception as e:
        print(f"Ошибка при удалении сессии: {e}")

def clear_session_history(username: str, flow_id: str, session_id: str):
    """Очищает историю конкретной сессии"""
    try:
        # Очищаем файл истории
        save_session_history(username, flow_id, session_id, [], get_session_display_name(username, flow_id, session_id))
        
        # Очищаем состояние сообщений в текущей сессии
        st.session_state.messages = []
        
        print(f"Очищена история сессии: {username}_{flow_id}_{session_id}")
        st.rerun()
    except Exception as e:
        print(f"Ошибка при очистке истории: {e}")

def delete_message_from_session(username: str, flow_id: str, session_id: str, message_hash: str):
    """Удаляет конкретное сообщение из истории сессии"""
    try:
        # Загружаем текущие сообщения
        messages = load_session_history(username, flow_id, session_id)
        
        # Фильтруем сообщения, исключая сообщение с указанным хэшем
        updated_messages = [
            msg for msg in messages 
            if get_message_hash(msg["role"], msg["content"]) != message_hash
        ]
        
        # Сохраняем обновленную историю
        save_session_history(username, flow_id, session_id, updated_messages, get_session_display_name(username, flow_id, session_id))
        
        # Обновляем состояние сообщений в текущей сессии
        st.session_state.messages = updated_messages
        
        print(f"Удалено сообщение из сессии: {username}_{flow_id}_{session_id}")
        st.rerun()
    except Exception as e:
        print(f"Ошибка при удалении сообщения: {e}")

# Инициализация путей для аватаров
PROFILE_IMAGES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'profile_images'))
ASSISTANT_ICON_PATH = os.path.join(PROFILE_IMAGES_DIR, 'assistant_icon.png')

# Загрузка аватара ассистента
if os.path.exists(ASSISTANT_ICON_PATH):
    try:
        assistant_avatar = Image.open(ASSISTANT_ICON_PATH)
    except Exception as e:
        assistant_avatar = "🤖"
else:
    assistant_avatar = "🤖"

def get_message_hash(role, content):
    """Создает уникальный хэш для сообщения"""
    return hashlib.md5(f"{role}:{content}".encode()).hexdigest()

def get_user_profile_image(username):
    for ext in ['png', 'jpg', 'jpeg']:
        image_path = os.path.join(PROFILE_IMAGES_DIR, f"{username}.{ext}")
        if os.path.exists(image_path):
            try:
                return Image.open(image_path)
            except Exception as e:
                return "👤"
    return "👤"

def display_message(message, role):
    """Отображает сообщение"""
    avatar = assistant_avatar if role == "assistant" else get_user_profile_image(st.session_state.username)
    
    with st.chat_message(role, avatar=avatar):
        st.markdown(message["content"])

# Функция для сохранения нового чат-потока
def save_chat_flow(username, flow_id, flow_name=None):
    user = user_db.get(User.username == username)
    if not user:
        return False
        
    chat_flows = user.get('chat_flows', [])
    
    if not flow_name:
        flow_name = f"Чат {len(chat_flows) + 1}"
    else:
        try:
            if not isinstance(flow_name, str):
                flow_name = str(flow_name)
            flow_name = flow_name.encode('utf-8').decode('utf-8')
        except:
            try:
                flow_name = flow_name.encode('cp1251').decode('utf-8')
            except:
                pass
    
    # Создаем первую сессию при создании чата
    session_id = 'session_1'
    
    new_flow = {
        'id': flow_id,
        'name': flow_name,
        'created_at': datetime.now().isoformat(),
        'current_session': session_id
    }
    
    chat_flows.append(new_flow)
    user_db.update({'chat_flows': chat_flows}, User.username == username)
    
    # Инициализируем историю для первой сессии
    save_session_history(username, flow_id, session_id, [], get_session_display_name(username, flow_id, session_id))
    return True

# Функция для получения списка чат-потоков пользователя
def get_user_chat_flows(username):
    user = user_db.get(User.username == username)
    if not user:
        return []
    
    chat_flows = user.get('chat_flows', [])
    # Исправляем кодировку имен чатов
    for flow in chat_flows:
        try:
            name = flow['name']
            if not isinstance(name, str):
                name = str(name)
            # Если строка уже в UTF-8, оставляем как есть
            flow['name'] = name.encode('utf-8').decode('utf-8')
        except:
            try:
                flow['name'] = name.encode('cp1251').decode('utf-8')
            except:
                pass
    
    return chat_flows

# Функция для очистки истории конкретного чата
def clear_chat_history(username, flow_id):
    """Очистка истории конкретного чата"""
    chat_db = ChatDatabase(f"{username}_{flow_id}")
    chat_db.clear_history()
    
    # Очищаем все связанные состояния
    keys_to_delete = []
    for key in st.session_state.keys():
        # Удаляем все состояния, связанные с сообщениями
        if any(key.startswith(prefix) for prefix in [
            "message_hashes",
            "message_counter",
            "message_ids",
            "translation_state_",
            "translation_states",
            "_last_input"
        ]):
            keys_to_delete.append(key)
    
    # Удаляем все найденные ключи
    for key in keys_to_delete:
        del st.session_state[key]
    
    # Принудительно сбрасываем счетчик сообщений
    st.session_state.message_counter = 0
    
    st.rerun()

# Добавьте эту функцию после функции clear_chat_history

def delete_chat_flow(username, flow_id):
    # Получаем текущего пользователя
    user = user_db.get(User.username == username)
    if not user:
        return False
    
    # Получаем список чатов
    chat_flows = user.get('chat_flows', [])
    
    # Удаляем чат из списка
    chat_flows = [flow for flow in chat_flows if flow['id'] != flow_id]
    
    # Обновляем список чатов в базе данных
    user_db.update({'chat_flows': chat_flows}, User.username == username)
    
    # Удаляем историю чата
    chat_db = ChatDatabase(f"{username}_{flow_id}")
    chat_db.clear_history()
    
    return True

# Инициализация базы данных
user_db = TinyDB(get_data_file_path('user_database.json'))
User = Query()

st.title("Личный помощник")

# Отображение оставшихся генераций
user = user_db.get(User.username == st.session_state.username)
if user:
    remaining_generations = user.get('remaining_generations', 0)
    st.sidebar.metric("Осталось генераций:", remaining_generations)
    
    if remaining_generations <= 0:
        st.error("У вас закончились генераций. Пожалуйста, активируйте новый токен, купить новый можно в https://startintellect.ru/products")
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
        # Обноляем текущий чат
        st.session_state.current_chat_flow = selected_flow
        # Очищаем историю сообщений предыдущего чата
        if "message_hashes" in st.session_state:
            del st.session_state.message_hashes
        # Перезагружаем страницу для отображения новой истории
        st.rerun()

# Создание нового чат-потока
st.sidebar.markdown("---")
with st.sidebar.expander("Создать новый чат"):
    new_flow_name = st.text_input("Название чата:")
    new_flow_id = st.text_input(
        "ID чат-потока:",
        help="Введите например: 28d13206-3a4d-4ef8-80e6-50b671b5766c или закжите сборку чата в https://t.me/startintellect"
    )
    
    if st.button("Создать") and new_flow_id:
        if save_chat_flow(st.session_state.username, new_flow_id, new_flow_name):
            st.session_state.current_chat_flow = {
                'id': new_flow_id,
                'name': new_flow_name or f"Чат {len(chat_flows) + 1}"
            }
            chat_db = ChatDatabase(f"{st.session_state.username}_{new_flow_id}")
            st.success("Новый чат создан!")
            st.rerun()

# Добавляем состояния подтверждения с уникальными ключами
if "new_chat_delete_confirm" not in st.session_state:
    st.session_state.new_chat_delete_confirm = False

# Заменяем кнопку удаления чата
if st.sidebar.button(
    "🗑️ Удалить текущий чат" if not st.session_state.new_chat_delete_confirm else "⚠️ Подтвердите удаление чата",
    type="secondary" if not st.session_state.new_chat_delete_confirm else "primary",
    key="new_chat_delete_button"
):
    if st.session_state.new_chat_delete_confirm:
        if 'current_chat_flow' in st.session_state:
            if delete_chat_flow(st.session_state.username, st.session_state.current_chat_flow['id']):
                st.sidebar.success("Чат успешно удален!")
                if 'current_chat_flow' in st.session_state:
                    del st.session_state.current_chat_flow
                if 'message_hashes' in st.session_state:
                    del st.session_state.message_hashes
                st.session_state.new_chat_delete_confirm = False
                st.rerun()
    else:
        st.session_state.new_chat_delete_confirm = True
        st.sidebar.warning("⚠️ Вы уверены, что хотите удалить этот чат? Это действие нельзя отменить!")

# Добавляем кнопку отмены для удаления
if st.session_state.new_chat_delete_confirm:
    if st.sidebar.button("Отмена", key="new_chat_cancel_action"):
        st.session_state.new_chat_delete_confirm = False
        st.rerun()

# Проверяем наличие текущего чат-потока
if 'current_chat_flow' not in st.session_state:
    st.info("Создайте новый чат для начала общения")
    st.stop()

# Отображение текущего чата
if 'current_chat_flow' in st.session_state:
    chat_name = st.session_state.current_chat_flow['name']
    try:
        if not isinstance(chat_name, str):
            chat_name = str(chat_name)
        chat_name = chat_name.encode('utf-8').decode('utf-8')
    except:
        try:
            chat_name = chat_name.encode('cp1251').decode('utf-8')
        except:
            pass
    
    st.markdown(f"### 💬 {chat_name}")
    
    # Добавляем выбор сессии
    available_sessions = get_available_sessions(
        st.session_state.username,
        st.session_state.current_chat_flow['id']
    )
    
    # Если нет текущей сессии, создаем новую
    if 'current_session' not in st.session_state.current_chat_flow:
        st.session_state.current_chat_flow['current_session'] = str(uuid.uuid4())
        save_session_history(
            st.session_state.username,
            st.session_state.current_chat_flow['id'],
            st.session_state.current_chat_flow['current_session'],
            [],
            get_session_display_name(st.session_state.username, st.session_state.current_chat_flow['id'], st.session_state.current_chat_flow['current_session'])
        )
    
    # Отображаем доступные сессии и кнопки управления
    col1, col2 = st.columns([3, 1])
    
    with col1:
        if available_sessions:
            # Создаем словарь для маппинга отображаемых имен к ID
            session_map = {session['display_name']: session['id'] for session in available_sessions}
            display_names = list(session_map.keys())
            
            # Определяем текущий индекс
            current_display_name = next(
                (session['display_name'] for session in available_sessions 
                 if session['id'] == st.session_state.current_chat_flow['current_session']),
                display_names[0] if display_names else None
            )
            
            selected_display_name = st.selectbox(
                "Выберите сессию:",
                display_names,
                index=display_names.index(current_display_name) if current_display_name in display_names else 0
            )
            
            selected_session_id = session_map[selected_display_name]
            
            # Поле для ввода нового имени
            new_name = st.text_input(
                "Введите новое имя сессии:",
                value=selected_display_name,
                key="new_session_name"
            )
            
            if selected_session_id != st.session_state.current_chat_flow['current_session']:
                st.session_state.current_chat_flow['current_session'] = selected_session_id
                st.rerun()
    
    # Создаем контейнер для кнопок управления с одинаковой шириной
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
        
        if st.button("💫 Новая сессия", use_container_width=True):
            new_session_id = str(uuid.uuid4())
            st.session_state.current_chat_flow['current_session'] = new_session_id
            
            # Получаем список существующих сессий для определения номера новой
            existing_sessions = get_available_sessions(
                st.session_state.username,
                st.session_state.current_chat_flow['id']
            )
            new_session_number = len(existing_sessions) + 1
            
            save_session_history(
                st.session_state.username,
                st.session_state.current_chat_flow['id'],
                new_session_id,
                [],
                f"Сессия {new_session_number}"
            )
            st.rerun()
        
        if available_sessions and st.button("✏️ Переименовать", use_container_width=True):
            if new_name and new_name.strip() != selected_display_name:
                rename_session(
                    st.session_state.username,
                    st.session_state.current_chat_flow['id'],
                    selected_session_id,
                    new_name.strip()
                )
        
        if st.button("🧹 Очистить", use_container_width=True):
            clear_session_history(
                st.session_state.username,
                st.session_state.current_chat_flow['id'],
                st.session_state.current_chat_flow['current_session']
            )
        
        if st.button("🗑 Удалить", type="primary", use_container_width=True):
            delete_session(
                st.session_state.username,
                st.session_state.current_chat_flow['id'],
                st.session_state.current_chat_flow['current_session']
            )
    
    st.markdown("---")

    # Загружаем историю текущей сессии
    session_messages = load_session_history(
        st.session_state.username,
        st.session_state.current_chat_flow['id'],
        st.session_state.current_chat_flow['current_session']
    )
    
    # Отображаем сообщения
    for message in session_messages:
        display_message(message, message["role"])

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

def generate_response(prompt: str, chat_id: str, session_id: str):
    """Генерирует ответ от Flowise"""
    try:
        print('Начало генерации ответа')
        
        # Формируем данные для запроса
        prediction_data = PredictionData(
            chatflowId=chat_id,
            question=prompt,
            streaming=False,
            overrideConfig={
                "sessionId": f"{st.session_state.username}_{chat_id}_{session_id}",
                "modelName": "gpt-3.5-turbo"
            }
        )
        
        try:
            print("Отправка запроса к API...")
            prediction = flowise_client.create_prediction(prediction_data)
            print("Получение ответа от API...")
            response = next(prediction)
            print(f"Тип полученного ответа: {type(response)}")
            print(f"Содержимое ответа: {response}")
            
            # Получаем текст ответа
            text_response = None
            if isinstance(response, dict):
                if response.get('text'):
                    print("Найден прямой текстовый ответ")
                    text_response = response['text']
                elif 'agentReasoning' in response:
                    print("Обработка сообщений агентов")
                    agents = response['agentReasoning']
                    for agent in reversed(agents):
                        if agent.get('messages') and len(agent['messages']) > 0:
                            last_message = agent['messages'][-1]
                            if isinstance(last_message, dict):
                                content = last_message.get('content', '')
                                if content:
                                    print("Найдено сообщение агента")
                                    text_response = content
                                    break
                            elif isinstance(last_message, str):
                                print("Найдено текстовое сообщение агента")
                                text_response = last_message
                                break
            else:
                text_response = str(response) if response else "Получен пустой ответ от API"

            if text_response:
                print(f"Исходный текст для перевода: {text_response[:100]}...")  # Показываем первые 100 символов
                try:
                    # Определяем язык текста
                    detected_lang = detect(text_response)
                    print(f"Определен язык ответа: {detected_lang}")
                    
                    # Если текст на английском, переводим на русский
                    if detected_lang == 'en':
                        print("Начинаем перевод на русский...")
                        translator = Translator()
                        translated = translator.translate(text_response, dest='ru')
                        if translated and translated.text:
                            print("Перевод успешно выполнен")
                            return translated.text
                        else:
                            print("Ошибка: перевод вернул пустой результат")
                            return text_response
                    else:
                        print(f"Перевод не требуется, текст уже на языке: {detected_lang}")
                        return text_response
                except Exception as e:
                    print(f"Ошибка при переводе: {str(e)}")
                    return text_response
            
            print("Не удалось получить текст для ответа")
            return "Не удалось получить ответ в ожидаемом формате. Пожалуйста, попробуйте еще раз."
                    
        except Exception as e:
            print(f"Ошибка при получении ответа от API: {str(e)}")
            if "Unknown model" in str(e):
                return "Ошибка конфигурации модели. Пожалуйста, проверьте настройки чата."
            return f"Ошибка при получении ответа: {str(e)}"
            
    except Exception as e:
        print(f"Общая ошибка в generate_response: {str(e)}")
        return f"Произошла ошибка: {str(e)}"

def submit_message(user_input):
    if not user_input:
        st.warning("Пожалуйста, введите сообщение")
        return

    try:
        print("Начало обработки сообщения")
        
        # Получаем текущую сессию и историю
        current_session_id = st.session_state.current_chat_flow['current_session']
        print(f"Текущая сессия: {current_session_id}")
        
        session_messages = load_session_history(
            st.session_state.username,
            st.session_state.current_chat_flow['id'],
            current_session_id
        )
        print("История сессии загружена")

        # Добавляем сообщение пользователя
        user_message = {"role": "user", "content": user_input}
        session_messages.append(user_message)
        
        try:
            save_session_history(
                st.session_state.username,
                st.session_state.current_chat_flow['id'],
                current_session_id,
                session_messages,
                get_session_display_name(st.session_state.username, st.session_state.current_chat_flow['id'], current_session_id)
            )
            print("Сообщение пользователя сохранено")
        except Exception as save_error:
            print(f"Ошибка при сохранении сообщения: {str(save_error)}")
        
        display_message(user_message, "user")

        # Получаем и отображаем ответ
        with st.chat_message("assistant", avatar=assistant_avatar):
            print("Запрос ответа от API...")
            response = generate_response(
                user_input,
                st.session_state.current_chat_flow['id'],
                current_session_id
            )
            print(f"Получен ответ от API: {response}")
            
            # Отображаем ответ
            st.write(response)
            
            # Сохраняем ответ в историю сессии
            assistant_message = {"role": "assistant", "content": response}
            session_messages.append(assistant_message)
            
            try:
                save_session_history(
                    st.session_state.username,
                    st.session_state.current_chat_flow['id'],
                    current_session_id,
                    session_messages,
                    get_session_display_name(st.session_state.username, st.session_state.current_chat_flow['id'], current_session_id)
                )
                print("Ответ сохранен в истории")
            except Exception as save_error:
                print(f"Ошибка при сохранении ответа: {str(save_error)}")
            
            try:
                update_remaining_generations(st.session_state.username, -1)
                print("Счетчик генераций обновлен")
            except Exception as update_error:
                print(f"Ошибка при обновлении счетчика: {str(update_error)}")
            
            st.rerun()

    except Exception as e:
        error_msg = f"Общая ошибка при обработке сообщения: {str(e)}"
        print(error_msg)
        st.error(error_msg)

# Создаем контейнер для поля ввода
input_container = st.container()

def clear_input():
    # Используем callback для очистки
    st.session_state.message_input = ""

# Поле ввода с возможностью растягивания
user_input = st.text_area(
    "Введите ваше сообщение",
    height=100,
    key="message_input",
    placeholder="Введите текст сообщения здесь..."  
)

# Создаем три колонки для кнопок
col1, col2, col3 = st.columns(3)
    
with col1:
    send_button = st.button("Отправить", key="send_message", use_container_width=True)
with col2:
    # Используем on_click для очистки
    clear_button = st.button("Очистить", key="clear_input", on_click=clear_input, use_container_width=True)
with col3:
    # Для кнопки отмены используем тот же callback
    cancel_button = st.button("Отменить", key="cancel_request", on_click=clear_input, use_container_width=True)

# Изменяем логику отправки сообщения
if send_button:  # Отправляем только при явном нажатии кнопки
    if user_input and user_input.strip():
        st.session_state['_last_input'] = user_input
        submit_message(user_input)