import streamlit as st
from streamlit_extras.switch_page_button import switch_page
from utils.page_config import PAGE_CONFIG, setup_pages
from utils.database.database_manager import get_database
import os
import json
from datetime import datetime

# Получаем экземпляр базы данных
db = get_database()

# Проверяем и устанавливаем состояние бокового меню
if st.session_state.get("sidebar_state") == "expanded":
    st.set_page_config(
        page_title="Ввод/Покупка ключа",
        page_icon="🔑",
        layout="wide",
        initial_sidebar_state="expanded"
    )
else:
    st.set_page_config(
        page_title="Ввод/Покупка ключа",
        page_icon="🔑",
        layout="wide",
        initial_sidebar_state="collapsed"
    )

# Затем настройка страниц
setup_pages()

# Проверка аутентификации
if "authenticated" not in st.session_state or not st.session_state.authenticated:
    st.warning("Пожалуйста, войдите в систему")
    switch_page(PAGE_CONFIG["registr"]["name"])
    st.stop()

st.title("Ввод токена")

# Поле для ввода токена
access_token = st.text_input("Вставьте токен доступа (например: b99176c5-8bca-4be9-b066-894e4103f32c)")

# Загрузка ключей доступа из Redis
def load_access_keys():
    """Загрузка ключей доступа из Redis"""
    keys_data = db.cache_get("access_keys")
    if keys_data:
        return json.loads(keys_data)
    return {}

# Добавить функцию проверки токена
def verify_token(token, username):
    # Получаем данные пользователя
    user = db.get_user(username)
    
    if not user:
        return False, "Пользователь не найден"
    
    # Проверка использования токена
    existing_user = db.users.find_one({"active_token": token})
    if existing_user and existing_user['username'] != username:
        return False, "Токен уже используется другим пользователем"
    
    # Проверяем токен в Redis
    access_keys = load_access_keys()
    if token in access_keys:
        # Получаем количество генераций для данного токена
        generations = access_keys.get(token, {}).get("generations", 500)
        
        # Обновляем данные пользователя
        db.update_user(username, {
            'active_token': token,
            'remaining_generations': generations,
            'token_activated_at': datetime.now()
        })
        
        return True, "Токен активирован"
    
    return False, "Недействительный токен"

# Проверка токена
if st.button("Активировать токен"):
    success, message = verify_token(access_token, st.session_state.username)
    if success:
        st.success(message)
        st.session_state.access_granted = True
        switch_page(PAGE_CONFIG["app"]["name"])
    else:
        st.error(message)

# Кнопка для покупки токена
if st.button("Купить токен", key="buy_link"):
    st.markdown('<a href="https://startintellect.ru/products" target="_blank">Перейти на сайт</a>', unsafe_allow_html=True)
