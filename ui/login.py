"""
ui/login.py
===========
صفحة تسجيل الدخول وإنشاء حساب جديد.
"""

import streamlit as st

from core.auth import AuthManager
from config import APP_NAME, APP_ICON

def show_login():
    st.markdown(
        f"<h1 style='text-align:center;'>{APP_ICON} {APP_NAME}</h1>"
        "<p style='text-align:center; color:gray;'>تحليل بياناتك بلغة طبيعية</p>",
        unsafe_allow_html=True,
    )

    auth = AuthManager()
    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        tab_login, tab_register = st.tabs(["🔑 تسجيل الدخول", "🆕 حساب جديد"])

        with tab_login:
            with st.form("login_form"):
                username = st.text_input("اسم المستخدم")
                password = st.text_input("كلمة المرور", type="password")
                submitted = st.form_submit_button("دخول", width='stretch')
                if submitted:
                    r = auth.login(username, password)
                    if r["ok"]:
                        st.session_state.token = r["token"]
                        st.session_state.user_id = r["user_id"]
                        st.session_state.username = r["username"]
                        st.success("تم تسجيل الدخول بنجاح")
                        st.rerun()
                    else:
                        st.error(r["error"])

        with tab_register:
            with st.form("register_form"):
                new_username = st.text_input("اسم المستخدم الجديد")
                new_password = st.text_input("كلمة المرور", type="password", key="reg_pass")
                confirm_password = st.text_input("تأكيد كلمة المرور", type="password")
                submitted = st.form_submit_button("إنشاء حساب", width='stretch')
                if submitted:
                    if new_password != confirm_password:
                        st.error("كلمتا المرور غير متطابقتين")
                    else:
                        r = auth.register(new_username, new_password)
                        if r["ok"]:
                            st.success("تم إنشاء الحساب بنجاح، يمكنك تسجيل الدخول الآن")
                        else:
                            st.error(r["error"])
