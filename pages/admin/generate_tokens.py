import streamlit as st
from utils.utils import generate_and_save_token
from utils.page_config import setup_pages
from utils.database.database_manager import get_database
from datetime import datetime

# Настраиваем страницы
setup_pages()

# Получаем экземпляр базы данных
db = get_database()

# Проверка прав администратора
if not st.session_state.get("is_admin", False):
    st.error("Доступ запрещен. Страница доступна только администраторам.")
    st.stop()

# Дополнительная проверка имени пользователя и пароля администратора
if "admin_verified" not in st.session_state:
    admin_username = st.text_input("Введите имя пользователя администратора")
    admin_password = st.text_input("Введите пароль администратора", type="password")
    
    if admin_username != st.secrets["admin"]["admin_username"] or admin_password != st.secrets["admin"]["admin_password"]:
        st.error("Неверное имя пользователя или пароль администратора")
        st.stop()
    
    st.session_state.admin_verified = True

st.title("Генерация токенов (Админ панель)")

with st.form("token_generation"):
    num_tokens = st.number_input("Количество токенов", min_value=1, max_value=10, value=1)
    generations = st.number_input("Количество генераций на токен", 
                                min_value=10, max_value=1000, value=500)
    submit = st.form_submit_button("Сгенерировать")

if submit:
    for _ in range(num_tokens):
        # Генерируем новый токен
        new_token = generate_and_save_token(generations)
        
        # Сохраняем токен в MongoDB
        db.access_tokens.insert_one({
            "token": new_token,
            "generations": generations,
            "used": False,
            "created_at": datetime.now()
        })
        
        st.code(new_token)
        st.write(f"Токен успешно создан с {generations} генерациями")

# Отображение существующих токенов
st.markdown("---")
st.subheader("Существующие токены")

tokens = list(db.access_tokens.find().sort("created_at", -1))
if tokens:
    for token in tokens:
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            st.code(token["token"])
        with col2:
            st.write(f"Генераций: {token['generations']}")
        with col3:
            st.write("Использован" if token["used"] else "Не использован")

