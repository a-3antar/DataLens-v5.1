"""
ai/engine_registry.py
=======================
سجل مركزي لكل محركات AI المتوافقة مع بروتوكول OpenAI
(/models و /chat/completions). كل محرك مبني عبر
ai.openai_compatible_engine.OpenAICompatibleEngine.

لإضافة محرك جديد متوافق مع OpenAI مستقبلاً (مثل OpenAI نفسه،
Together AI، Fireworks...): أضف إدخالاً واحداً هنا فقط — لا حاجة
لتعديل ai/ai_manager.py أو config.py أو أي مكان آخر؛ الاسم يظهر
تلقائياً في قائمة المحركات (config.AI_ENGINES يُبنى من هنا).

"gemini" و "ollama" مستثنيان من هذا السجل لأن كلاً منهما له بروتوكول
API مختلف تماماً عن OpenAI (كلاس منفصل خاص بكل منهما).
"""

OPENAI_COMPATIBLE_ENGINES = {
    "groq": {
        "display_name" : "Groq",
        "base_url"     : "https://api.groq.com/openai/v1",
        "default_model": "openai/gpt-oss-120b",
    },
    "openrouter": {
        "display_name" : "OpenRouter",
        "base_url"     : "https://openrouter.ai/api/v1",
        "default_model": "mistralai/mistral-7b-instruct",
    },
    # مثال لإضافة OpenAI نفسه مستقبلاً — يكفي إزالة التعليق وتعديل
    # الموديل الافتراضي عند الحاجة الفعلية:
    # "openai": {
    #     "display_name" : "OpenAI",
    #     "base_url"     : "https://api.openai.com/v1",
    #     "default_model": "gpt-4o-mini",
    # },
}


def get_registry_entry(engine_name: str) -> dict:
    """إرجاع تعريف محرك من السجل، أو {} لو غير موجود."""
    return OPENAI_COMPATIBLE_ENGINES.get(engine_name.lower().strip(), {})


def all_openai_compatible_names() -> list[str]:
    """كل أسماء المحركات المسجّلة هنا (بدون gemini/ollama)."""
    return list(OPENAI_COMPATIBLE_ENGINES.keys())
