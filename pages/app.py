import streamlit as st
import requests
from streamlit_extras.switch_page_button import switch_page
from googletrans import Translator
import os
from PIL import Image
import streamlit.components.v1 as components
from tinydb import TinyDB, Query
User = Query()
from utils.utils import update_remaining_generations, get_data_file_path
from utils.chat_database import ChatDatabase
from utils.page_config import PAGE_CONFIG, setup_pages
import json
from utils.translation import translate_text, display_message_with_translation
from flowise import Flowise, PredictionData
from datetime import datetime
import time

# Создаем директорию для хранения истории чата, если её нет
CHAT_HISTORY_DIR = 'chat_history'
if not os.path.exists(CHAT_HISTORY_DIR):
    os.makedirs(CHAT_HISTORY_DIR)

def save_chat_history(username: str, messages: list):
    """Сохраняет историю чата в файл"""
    try:
        # Создаем имя файла с использованием username и main_chat
        filename = f"{username}_main_chat.json"
        filepath = os.path.join(CHAT_HISTORY_DIR, filename)
        
        # Проверяем, что messages не пустой
        if not messages:
            print("Нет сообщений для сохранения")
            return
            
        # Подготавливаем данные для сохранения
        data = {
            'username': username,
            'messages': messages,
            'last_updated': datetime.now().isoformat()
        }
        
        # Сохраняем историю в файл
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"История чата сохранена в {filepath}")
    except Exception as e:
        print(f"Ошибка при сохранении истории чата: {e}")

def load_chat_history(username: str) -> list:
    """Загружает историю чата из файла пользователя"""
    try:
        # Формируем путь к файлу
        filename = f"{username}_main_chat.json"
        filepath = os.path.join(CHAT_HISTORY_DIR, filename)
        
        # Проверяем существование файла
        if not os.path.exists(filepath):
            print(f"Файл истории не найден: {filepath}")
            return []
        
        # Читаем файл
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                print("Файл истории пуст")
                return []
                
            data = json.loads(content)
            
            # Проверяем структуру данных
            if not isinstance(data, dict):
                print("Неверный формат данных в файле истории")
                return []
                
            messages = data.get('messages', [])
            if not isinstance(messages, list):
                print("Неверный формат сообщений в файле истории")
                return []
                
            # Проверяем каждое сообщение
            valid_messages = []
            for msg in messages:
                if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                    valid_messages.append(msg)
                    
            return valid_messages
            
    except json.JSONDecodeError as e:
        print(f"Ошибка при разборе JSON: {e}")
        return []
    except Exception as e:
        print(f"Ошибка при загрузке истории чата: {e}")
        return []

# Сначала конфигурация страницы
st.set_page_config(
    page_title="Поисковый отдел",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Затем настройка страниц
setup_pages()

# Проверка аутентификации и доступа к API
if "authenticated" not in st.session_state or not st.session_state.authenticated:
    st.warning("Пожалуйста, войдите в систему")
    switch_page(PAGE_CONFIG["registr"]["name"])
    st.stop()

# Инициализация session state
if 'username' not in st.session_state:
    st.warning("Пожалуйста, войдите в систему")
    st.session_state.authenticated = False
    setup_pages()
    switch_page("Вход/Регистрация")
    st.stop()

# Проверяем наличие активного токена и доступа
user_db = TinyDB(get_data_file_path('user_database.json'))
user_data = user_db.search(User.username == st.session_state.username)

if user_data:
    user_data = user_data[0]
    
    # Синхронизируем session state с данными из базы
    st.session_state.active_token = user_data.get('active_token')
    st.session_state.remaining_generations = user_data.get('remaining_generations', 0)
    st.session_state.access_granted = bool(user_data.get('active_token'))
    
    # Проверяем токен и статус доступа
    if not st.session_state.active_token:
        st.warning("Пожалуйста, введите ключ доступа")
        switch_page(PAGE_CONFIG["key_input"]["name"])
else:
    st.error("Пользователь не найден")
    st.session_state.authenticated = False
    setup_pages()
    switch_page(PAGE_CONFIG["registr"]["name"])

# Инициализируем базу данных чата
chat_db = ChatDatabase(f"{st.session_state.username}_main_chat")

# Папка с изображениями профиля
PROFILE_IMAGES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'profile_images'))
ASSISTANT_ICON_PATH = os.path.join(PROFILE_IMAGES_DIR, 'assistant_icon.png')

# Проверяем существование файла иконки ассистента
if os.path.exists(ASSISTANT_ICON_PATH):
    try:
        assistant_avatar = Image.open(ASSISTANT_ICON_PATH)
    except Exception as e:
        st.error(f"Ошибка при открытии изображения ассистента: {e}")
        assistant_avatar = "🤖"
else:
    assistant_avatar = "🤖"

def get_user_profile_image(username):
    for ext in ['png', 'jpg', 'jpeg']:
        image_path = os.path.join(PROFILE_IMAGES_DIR, f"{username}.{ext}")
        if os.path.exists(image_path):
            try:
                return Image.open(image_path)
            except Exception as e:
                st.error(f"Ошибка при открытии изображения {image_path}: {e}")
                return "👤"
    return "👤"

def display_remaining_generations():
    if "remaining_generations" in st.session_state:
        st.sidebar.write(f"Осталось генераций: {st.session_state.remaining_generations}")

def generate_response(prompt: str):
    print('generating response')
    try:
        # Используем base_url из secrets и убираем лишние слеши
        base_url = st.secrets["flowise"]["api_base_url"].rstrip('/')
        flow_id = st.secrets["flowise"]["main_chat_id"]
        
        # Создаем уникальный идентификатор сессии для пользователя
        session_id = f"{st.session_state.username}_{flow_id}"
        
        print(f"Using base URL: {base_url}")
        print(f"Flow ID: {flow_id}")
        print(f"Session ID: {session_id}")
        print(f"User: {st.session_state.username}")
        print(f"Question: {prompt}")
        
        # Добавляем метаданные пользователя
        user_metadata = {
            "username": st.session_state.username,
            "session_start": st.session_state.get("session_start", datetime.now().isoformat()),
            "chat_type": "search_department"
        }
        
        # Формируем правильный URL для запроса
        prediction_url = f"{base_url}/{flow_id}"
        print(f"Full URL: {prediction_url}")
        
        # Отправляем запрос напрямую через requests
        response = requests.post(
            prediction_url,
            json={
                "question": prompt,
                "overrideConfig": {
                    "sessionId": session_id,
                    "userMetadata": user_metadata
                }
            }
        )
        
        # Проверяем статус ответа
        response.raise_for_status()
        
        # Получаем JSON ответ
        response_data = response.json()
        
        # Проверяем наличие текста в ответе
        if isinstance(response_data, dict):
            if 'text' in response_data:
                yield response_data['text']
            elif 'agentReasoning' in response_data:
                # Извлекаем текст из agentReasoning
                for agent in response_data['agentReasoning']:
                    if 'instructions' in agent:
                        yield agent['instructions']
            else:
                yield str(response_data)
        else:
            yield str(response_data)
                    
    except Exception as e:
        error_msg = f"Ошибка при получении ответа: {str(e)}"
        print(error_msg)
        yield error_msg

def translate_response(text: str, max_retries: int = 3) -> str:
    """Переводит текст с английского на русский с повторными попытками"""
    if not text or not isinstance(text, str):
        return text
        
    # Если текст содержит только русские символы, возвращаем как есть
    if all(ord(char) < 128 or char.isspace() or char in '.,!?-()[]{}' for char in text):
        return text
        
    for attempt in range(max_retries):
        try:
            translator = Translator(service_urls=['translate.google.com'])
            
            # Разбиваем длинный текст на части по 1000 символов
            chunks = [text[i:i+1000] for i in range(0, len(text), 1000)]
            translated_chunks = []
            
            for chunk in chunks:
                # Делаем паузу между переводами чанков
                time.sleep(1)
                
                # Переводим чанк
                translated = translator.translate(chunk, dest='ru', src='en')
                if translated and translated.text:
                    translated_chunks.append(translated.text)
                else:
                    translated_chunks.append(chunk)
            
            # Собираем переведенный текст
            return ' '.join(translated_chunks)
                
        except Exception as e:
            print(f"Попытка перевода {attempt + 1} не удалась: {str(e)}")
            if attempt < max_retries - 1:
                # Увеличиваем паузу с каждой попыткой
                time.sleep(2 * (attempt + 1))
                continue
            else:
                print("Все попытки перевода исчерпаны, возвращаем оригинальный текст")
                return text
    
    return text

def submit_question():
    if not verify_user_access():
        return

    user_input = st.session_state.get('message_input', '').strip()
    if not user_input:
        st.warning("Пожалуйста, введите ваш вопрос.")
        return

    try:
        # Сохраняем сообщение пользователя
        chat_db.add_message("user", user_input)
        display_message({"role": "user", "content": user_input}, "user")

        # Get and display the response
        with st.chat_message("assistant"):
            response = generate_response(user_input)
            full_response = ""
            for chunk in response:
                if chunk:
                    # Переводим каждый чанк отдельно
                    translated_chunk = translate_response(chunk)
                    full_response += translated_chunk + " "
                    # Отображаем переведенный чанк
                    st.write(translated_chunk)
            
        if full_response:
            # Сохраняем переведенный ответ в базу данных
            chat_db.add_message("assistant", full_response.strip())
            
            # Сохраняем обновленную историю в файл
            chat_history = chat_db.get_history()
            save_chat_history(st.session_state.username, chat_history)
            
            update_remaining_generations(st.session_state.username, -1)
            st.rerun()

    except Exception as e:
        st.error(f"Ошибка: {str(e)}")

def clear_input():
    """Очистка поля ввода"""
    st.session_state.message_input = ""

def display_message(message, role):
    """Отображает сообщение"""
    avatar = assistant_avatar if role == "assistant" else get_user_profile_image(st.session_state.username)
    
    # Отображаем сообщение
    with st.chat_message(role, avatar=avatar):
        st.markdown(message["content"])

def verify_user_access():
    """Проверка доступа пользователя"""
    if 'username' not in st.session_state:
        st.warning("Пожалуйста, войдите в систему")
        switch_page(PAGE_CONFIG["registr"]["name"])
        return False
        
    user_data = user_db.search(User.username == st.session_state.username)
    if not user_data:
        st.error("Пользователь не найден")
        switch_page(PAGE_CONFIG["registr"]["name"])
        return False
        
    if not user_data[0].get('active_token'):
        st.warning("Пожалуйста, введите ключ доступа")
        switch_page(PAGE_CONFIG["key_input"]["name"])
        return False
        
    return True

def clear_all_chat_history(username: str):
    """Полная очистка истории чата"""
    try:
        # Очищаем базу данных
        chat_db.clear_history()
        
        # Удаляем файлы истории для данного пользователя
        user_files = [f for f in os.listdir(CHAT_HISTORY_DIR) if f.startswith(username)]
        for file in user_files:
            try:
                os.remove(os.path.join(CHAT_HISTORY_DIR, file))
                print(f"Удален файл истории: {file}")
            except Exception as e:
                print(f"Ошибка при удалении файла {file}: {e}")
    except Exception as e:
        print(f"Ошибка при очистке истории: {e}")

def main():
    # Инициализируем session_id если его нет
    if "session_id" not in st.session_state:
        st.session_state.session_id = f"session_1"

    # Сначала пробуем загрузить историю из файла
    loaded_history = load_chat_history(st.session_state.username)
    
    # Если история найдена в файле, загружаем её в базу данных
    if loaded_history:
        # Очищаем текущую историю в базе данных
        chat_db.clear_history()
        # Загружаем сообщения из файла в базу данных
        for message in loaded_history:
            if isinstance(message, dict) and 'role' in message and 'content' in message:
                chat_db.add_message(message["role"], message["content"])
    
    # Получаем историю из базы данных для отображения
    chat_history = chat_db.get_history()
    
    # Отображаем историю чата
    for message in chat_history:
        display_message(message, message["role"])
    
    st.title("Поисковый отдел")

    # Отображаем количество генераций в начале
    display_remaining_generations()

    # Добавляем кнопку очистки чата
    if "main_clear_chat_confirm" not in st.session_state:
        st.session_state.main_clear_chat_confirm = False

    # Заменяем простую кнопку очистки на кнопку с подтверждением
    if st.sidebar.button(
        "Очистить чат" if not st.session_state.main_clear_chat_confirm else "⚠ Нажмите еще раз для подтверждения",
        type="secondary" if not st.session_state.main_clear_chat_confirm else "primary",
        key="main_clear_chat_button"
    ):
        if st.session_state.main_clear_chat_confirm:
            # Выполняем полную очистку
            clear_all_chat_history(st.session_state.username)
            st.session_state.main_clear_chat_confirm = False
            st.rerun()
        else:
            # Первое нажатие - показываем предупреждение
            st.session_state.main_clear_chat_confirm = True
            st.sidebar.warning("⚠️ Вы уверены? Это действие нельзя отменить!")

    # Добавляем кнопку отмены, если показано предупреждение
    if st.session_state.main_clear_chat_confirm:
        if st.sidebar.button("Отмена", key="main_cancel_clear"):
            st.session_state.main_clear_chat_confirm = False
            st.rerun()

    # Добавляем разделитель в боковом меню
    st.sidebar.markdown("---")

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
        clear_button = st.button("Очистить", key="clear_input", on_click=clear_input, use_container_width=True)
    with col3:
        cancel_button = st.button("Отменить", key="cancel_request", on_click=clear_input, use_container_width=True)

    # Обработка отправки сообщения
    if send_button and user_input and user_input.strip():
        st.session_state['_last_input'] = user_input
        with st.spinner('Отправляем ваш запрос...'):
            submit_question()

    # JavaScript для обработки Ctrl+Enter
    st.markdown("""
        <script>
        document.addEventListener('keydown', function(e) {
            if (e.ctrlKey && e.key === 'Enter') {
                window.parent.postMessage({
                    type: 'streamlit:setComponentValue',
                    key: 'ctrl_enter_pressed',
                    value: true
                }, '*');
            }
        });
        </script>
        """, unsafe_allow_html=True)

# Создаем клиент Flowise
client = Flowise(base_url=st.secrets["flowise"]["base_url"])

# Добавляем инициализацию времени начала сессии при входе на страницу
if "session_start" not in st.session_state:
    st.session_state.session_start = datetime.now().isoformat()

if __name__ == "__main__":
    main()
