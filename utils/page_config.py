from st_pages import Page, show_pages, add_page_title
import streamlit as st
import os
from utils.database.database_manager import get_database

# Словарь с настройками страниц
PAGE_CONFIG = {
    "registr": {
        "name": "Вход/Регистрация",
        "icon": "🔐",
        "order": 1,
        "show_when_authenticated": False,
        "show_in_menu": False
    },
    "key_input": {
        "name": "Ввод/Покупка ключа",
        "icon": "🔑",
        "order": 2,
        "show_when_authenticated": True,
        "show_in_menu": True
    },
    "simple_chat": {
        "name": "Бесплатный чат",
        "icon": "💬",
        "order": 3,
        "show_when_authenticated": True,
        "show_in_menu": True
    },
    "app": {
        "name": "Поисковый отдел",
        "icon": "🔍",
        "order": 4,
        "show_when_authenticated": True,
        "show_in_menu": True,
        "requires_token": True
    },
    "new_chat": {
        "name": "Личный помощник",
        "icon": "💭",
        "order": 5,
        "show_when_authenticated": True,
        "show_in_menu": True,
        "requires_token": True
    },
    "profile": {
        "name": "Профиль",
        "icon": "👤",
        "order": 6,
        "show_when_authenticated": True,
        "show_in_menu": True
    },
    "admin/generate_tokens": {
        "name": "Генерация ключей",
        "icon": "🔑",
        "order": 7,
        "show_when_authenticated": True,
        "show_in_menu": True,
        "admin_only": True
    },
    "admin/memory": {
        "name": "Память",
        "icon": "🧠",
        "order": 8,
        "show_when_authenticated": True,
        "admin_only": True
    }
}

def setup_pages():
    """Настройка страниц приложения"""
    pages_to_show = []
    is_authenticated = st.session_state.get("authenticated", False)
    is_admin = st.session_state.get("is_admin", False)
    
    # Показываем страницу регистрации только если пользователь не аутентифицирован
    if not is_authenticated:
        pages_to_show.append(
            Page("pages/registr.py", name=PAGE_CONFIG["registr"]["name"], icon=PAGE_CONFIG["registr"]["icon"])
        )
    
    # Добавляем остальные страницы
    for page_id, config in sorted(PAGE_CONFIG.items(), key=lambda x: x[1]["order"]):
        if page_id == "registr":
            continue
            
        should_show = (
            is_authenticated and 
            config["show_when_authenticated"] and
            (not config.get("admin_only", False) or is_admin)
        )
        
        if should_show and config.get("show_in_menu", True):
            page_path = f"pages/{page_id}.py"
            if os.path.exists(page_path):
                pages_to_show.append(
                    Page(page_path, name=config["name"], icon=config["icon"])
                )
    
    show_pages(pages_to_show)

def check_token_access():
    """Проверка доступа к функционалу, требующему токен"""
    if not st.session_state.get("authenticated", False):
        st.warning("Пожалуйста, войдите в систему")
        st.switch_page("pages/registr.py")
        st.stop()
        
    db = get_database()
    user = db.get_user(st.session_state.get("username"))
    if not user or not user.get("active_token"):
        st.warning("Для использования этой функции необходим активный ключ")
        st.switch_page("pages/key_input.py")
        st.stop()