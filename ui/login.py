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

🆕 توحيد النماذج (نظام التصميم الموحّد في ui/common.py):
------------------------------------------------------------------
كل تبويب هنا هو st.form واحد متكامل من أوله لآخره (الحقول + زر
الإرسال داخل نفس النموذج)، وهذا فعلياً ما يجعل الضغط على Enter في أي
حقل نصي يُرسل النموذج مباشرة بنفس تأثير زر الإرسال — سلوك Streamlit
الطبيعي لأي st.form. التبويبات الثلاثة (دخول / حساب جديد / نسيت كلمة
المرور←خطوتان) مبنية بهذا الشكل؛ ما تغيّر هنا هو الترتيب البصري
(تجميع كل نموذج داخل container، فواصل وعناوين فرعية واضحة) ورسائل
النجاح التي تسبق st.rerun() — تلك تحديداً تحوّلت من st.success()
الثابت (لا يظهر إطلاقاً لأن الصفحة تُعاد فوراً) إلى notify() العابر
(toast) من ui/common.py الذي يبقى مرئياً عبر الـ rerun. رسائل الخطأ
التي لا تُتبَع برن (rerun) بقيت st.error() لأنها يجب أن تبقى ظاهرة
وواضحة حتى يصححها المستخدم.

لا تغيير على منطق AuthManager أو استدعاءاته.
"""

import streamlit as st

from core.auth import AuthManager
from config import APP_NAME, APP_ICON
from ui.common import notify


def show_login():
    st.markdown(
        f"<h1 style='text-align:center;'>{APP_ICON} {APP_NAME}</h1>",
        unsafe_allow_html=True,
    )
    #st.write("")

    auth = AuthManager()
    col1, col2, col3 = st.columns([1, 1, 1])

    with col2:
        tab_login, tab_register, tab_forgot = st.tabs(
            ["🔑 تسجيل الدخول", "🆕 حساب جديد", "❓ نسيت كلمة المرور"]
        )

        with tab_login:
            _render_login_tab(auth)

        with tab_register:
            _render_register_tab(auth)

        with tab_forgot:
            _render_forgot_password(auth)
    st.markdown(
        f"<p style='text-align:center; color:gray;'>تحليل بياناتك بلغة طبيعية</p>",
        unsafe_allow_html=True,
    )


def _render_login_tab(auth: AuthManager):
    """نموذج تسجيل الدخول — Enter في أي حقل يُسجّل الدخول مباشرة."""
    
    with st.form("login_form"):
        username = st.text_input("اسم المستخدم", placeholder="اسم المستخدم")
        password = st.text_input(
            "كلمة المرور", type="password", placeholder="كلمة المرور"
        )
        st.write("")
        submitted = st.form_submit_button(
            "🔑 دخول", type="primary", width="stretch"
        )

    if submitted:
        if not username.strip() or not password:
            st.error("الرجاء إدخال اسم المستخدم وكلمة المرور")
        else:
            r = auth.login(username, password)
            if r["ok"]:
                st.session_state.token = r["token"]
                st.session_state.user_id = r["user_id"]
                st.session_state.username = r["username"]
                notify("تم تسجيل الدخول بنجاح", kind="success")
                st.rerun()
            else:
                st.error(r["error"])


def _render_register_tab(auth: AuthManager):
    """نموذج إنشاء حساب جديد — Enter في أي حقل يُنشئ الحساب مباشرة."""
    with st.container(border=True):
        st.subheader("إنشاء حساب جديد")
        st.caption("البريد الإلكتروني اختياري ويمكن إضافته لاحقاً من الإعدادات.")

        with st.form("register_form"):
            new_username = st.text_input(
                "اسم المستخدم الجديد", placeholder="اسم المستخدم"
            )
            new_email = st.text_input(
                "البريد الإلكتروني (اختياري)",
                placeholder="example@email.com",
                help="يُستخدم لاحقاً لاستعادة كلمة المرور عند نسيانها ولتأكيد الحساب. "
                     "يمكن إضافته أو تعديله لاحقاً من صفحة الإعدادات.",
            )
            st.divider()
            new_password = st.text_input(
                "كلمة المرور", type="password", key="reg_pass", placeholder="كلمة المرور"
            )
            confirm_password = st.text_input(
                "تأكيد كلمة المرور", type="password", placeholder="أعد كتابة كلمة المرور"
            )
            st.write("")
            submitted = st.form_submit_button(
                "🆕 إنشاء حساب", type="primary", width="stretch"
            )

        if submitted:
            if not new_username.strip() or not new_password:
                st.error("الرجاء إدخال اسم المستخدم وكلمة المرور")
            elif new_password != confirm_password:
                st.error("كلمتا المرور غير متطابقتين")
            else:
                r = auth.register(new_username, new_password, email=new_email)
                if r["ok"]:
                    msg = "تم إنشاء الحساب بنجاح، يمكنك تسجيل الدخول الآن"
                    if new_email.strip():
                        msg += " — تحقق من بريدك لتأكيده لاحقاً من صفحة الإعدادات"
                    notify(msg, kind="success")
                    st.success(msg)
                else:
                    st.error(r["error"])


def _render_forgot_password(auth: AuthManager):
    """
    استعادة كلمة مرور منسية عبر رمز يُرسَل للبريد المسجَّل — لا تتطلب
    جلسة تسجيل دخول. خطوتان منفصلتان (طلب الرمز، ثم استخدامه) بدل
    نموذج واحد، حتى يعرف المستخدم بوضوح أن الرمز أُرسل فعلاً قبل
    محاولة إدخاله. كل خطوة st.form مستقل بذاته، فـ Enter يُنفّذها
    مباشرة.
    """
    with st.container(border=True):
        st.subheader("١. طلب رمز الاستعادة")
        st.caption("أدخل اسم المستخدم لإرسال رمز استعادة إلى بريدك الإلكتروني المسجَّل.")

        with st.form("forgot_request_form"):
            fp_username = st.text_input("اسم المستخدم", placeholder="اسم المستخدم")
            request_submitted = st.form_submit_button(
                "📩 إرسال رمز الاستعادة", width="stretch"
            )

        if request_submitted:
            if not fp_username.strip():
                st.error("الرجاء إدخال اسم المستخدم")
            else:
                r = auth.request_password_reset(fp_username)
                if r["ok"]:
                    notify("تم إرسال رمز الاستعادة إلى بريدك الإلكتروني", kind="success")
                    st.session_state["_forgot_pw_username"] = fp_username.strip().lower()
                else:
                    st.error(r["error"])

    st.write("")

    with st.container(border=True):
        st.subheader("٢. تعيين كلمة مرور جديدة")
        st.caption("أدخل الرمز المُرسَل إليك مع كلمة المرور الجديدة.")

        with st.form("forgot_reset_form"):
            reset_username = st.text_input(
                "اسم المستخدم", value=st.session_state.get("_forgot_pw_username", ""),
                key="reset_username_field", placeholder="اسم المستخدم",
            )
            code = st.text_input("رمز الاستعادة (6 أرقام)", placeholder="000000")
            st.divider()
            new_password = st.text_input(
                "كلمة المرور الجديدة", type="password", key="fp_new_pass",
                placeholder="كلمة المرور الجديدة",
            )
            confirm_new_password = st.text_input(
                "تأكيد كلمة المرور الجديدة", type="password",
                placeholder="أعد كتابة كلمة المرور الجديدة",
            )
            st.write("")
            reset_submitted = st.form_submit_button(
                "🔓 تعيين كلمة المرور الجديدة", type="primary", width="stretch"
            )

        if reset_submitted:
            if not reset_username.strip() or not code.strip():
                st.error("الرجاء إدخال اسم المستخدم ورمز الاستعادة")
            elif new_password != confirm_new_password:
                st.error("كلمتا المرور غير متطابقتين")
            else:
                r = auth.reset_password_with_code(reset_username, code, new_password)
                if r["ok"]:
                    notify("تم تغيير كلمة المرور بنجاح — يمكنك تسجيل الدخول الآن", kind="success")
                    st.session_state.pop("_forgot_pw_username", None)
                else:
                    st.error(r["error"])
