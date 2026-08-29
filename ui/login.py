"""
ui/login.py
===========
صفحة تسجيل الدخول وإنشاء حساب جديد.

🆕 البريد الإلكتروني:
------------------------
حقل البريد أصبح اختيارياً عند إنشاء الحساب — لو أُدخل، يُرسَل رمز
تأكيد تلقائياً (المستخدم يستطيع تأكيده لاحقاً من صفحة الإعدادات، أو
لاحقاً في نفس الجلسة إن رغب). بدون بريد، تبقى ميزة "نسيت كلمة المرور"
غير متاحة لهذا الحساب حتى يُضيف بريداً لاحقاً من الإعدادات.

🆕 نسيت كلمة المرور:
------------------------
تبويب/قسم مستقل قبل تسجيل الدخول (لا يتطلب جلسة) — خطوتان: طلب رمز
عبر اسم المستخدم (يُرسَل للبريد المسجَّل)، ثم إدخال الرمز + كلمة مرور
جديدة. راجع core/auth.py::request_password_reset/reset_password_with_code.
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
        tab_login, tab_register, tab_forgot = st.tabs(
            ["🔑 تسجيل الدخول", "🆕 حساب جديد", "❓ نسيت كلمة المرور"]
        )

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
                new_email = st.text_input(
                    "البريد الإلكتروني (اختياري)",
                    help="يُستخدم لاحقاً لاستعادة كلمة المرور عند نسيانها ولتأكيد الحساب. "
                         "يمكن إضافته أو تعديله لاحقاً من صفحة الإعدادات.",
                )
                new_password = st.text_input("كلمة المرور", type="password", key="reg_pass")
                confirm_password = st.text_input("تأكيد كلمة المرور", type="password")
                submitted = st.form_submit_button("إنشاء حساب", width='stretch')
                if submitted:
                    if new_password != confirm_password:
                        st.error("كلمتا المرور غير متطابقتين")
                    else:
                        r = auth.register(new_username, new_password, email=new_email)
                        if r["ok"]:
                            msg = "تم إنشاء الحساب بنجاح، يمكنك تسجيل الدخول الآن"
                            if new_email.strip():
                                msg += " — تحقق من بريدك لتأكيده لاحقاً من صفحة الإعدادات"
                            st.success(msg)
                        else:
                            st.error(r["error"])

        with tab_forgot:
            _render_forgot_password(auth)


def _render_forgot_password(auth: AuthManager):
    """
    استعادة كلمة مرور منسية عبر رمز يُرسَل للبريد المسجَّل — لا تتطلب
    جلسة تسجيل دخول. خطوتان منفصلتان (طلب الرمز، ثم استخدامه) بدل
    نموذج واحد، حتى يعرف المستخدم بوضوح أن الرمز أُرسل فعلاً قبل
    محاولة إدخاله.
    """
    st.caption("أدخل اسم المستخدم لإرسال رمز استعادة إلى بريدك الإلكتروني المسجَّل.")

    with st.form("forgot_request_form"):
        fp_username = st.text_input("اسم المستخدم")
        request_submitted = st.form_submit_button("📩 إرسال رمز الاستعادة", width='stretch')
        if request_submitted:
            if not fp_username.strip():
                st.error("الرجاء إدخال اسم المستخدم")
            else:
                r = auth.request_password_reset(fp_username)
                if r["ok"]:
                    st.success("تم إرسال رمز الاستعادة إلى بريدك الإلكتروني")
                    st.session_state["_forgot_pw_username"] = fp_username.strip().lower()
                else:
                    st.error(r["error"])

    st.divider()
    st.caption("أدخل الرمز المُرسَل إليك مع كلمة المرور الجديدة.")

    with st.form("forgot_reset_form"):
        reset_username = st.text_input(
            "اسم المستخدم", value=st.session_state.get("_forgot_pw_username", ""),
            key="reset_username_field",
        )
        code = st.text_input("رمز الاستعادة (6 أرقام)")
        new_password = st.text_input("كلمة المرور الجديدة", type="password", key="fp_new_pass")
        confirm_new_password = st.text_input("تأكيد كلمة المرور الجديدة", type="password")
        reset_submitted = st.form_submit_button("🔓 تعيين كلمة المرور الجديدة", width='stretch')
        if reset_submitted:
            if new_password != confirm_new_password:
                st.error("كلمتا المرور غير متطابقتين")
            else:
                r = auth.reset_password_with_code(reset_username, code, new_password)
                if r["ok"]:
                    st.success("تم تغيير كلمة المرور بنجاح — يمكنك تسجيل الدخول الآن")
                    st.session_state.pop("_forgot_pw_username", None)
                else:
                    st.error(r["error"])