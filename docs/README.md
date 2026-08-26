# تشغيل DataLens V5.0 عبر Docker (التطبيق + Ollama في حاوية واحدة معزولة)

## المتطلبات

- Docker Desktop (Windows) أو Docker Engine + Docker Compose (Linux)
- ذاكرة (RAM) لا تقل عن 8GB متاحة للحاوية إن كنت ستشغّل نموذج Ollama متوسط الحجم (7B تقريباً)

## التشغيل السريع

```bash
# (اختياري) لاختيار نموذج يُحمَّل تلقائياً عند أول تشغيل
cp .env.example .env
# ثم عدّل OLLAMA_DEFAULT_MODEL داخل .env

# البناء والتشغيل
docker compose up -d --build

# متابعة السجلات (خصوصاً أول مرة أثناء تحميل النموذج إن حددته)
docker compose logs -f
```

بعد التشغيل، افتح المتصفح على:
```
http://<عنوان-السيرفر-على-الشبكة>:8501
```

## ما الذي تحصل عليه؟

- حاوية واحدة فقط تحتوي: تطبيق Streamlit + خادم Ollama معاً
- **معزولة تماماً عن جهاز السيرفر**: لا شيء يُكتب على نظام الملفات المضيف مباشرة — كل البيانات (حسابات المستخدمين، المشاريع، التقارير، نماذج Ollama المُحمَّلة) تُحفظ في Docker volume واحد مُدار بالكامل بواسطة Docker (`datalens_data`)
- منفذ Ollama (11434) **غير مُتاح** خارج الحاوية — فقط واجهة التطبيق (8501) تظهر على الشبكة، حماية إضافية
- البيانات تبقى محفوظة عبر `docker compose down` / `docker compose up` (لا تُفقد إلا لو حذفت الـ volume صراحة)

## أوامر مفيدة

```bash
# إيقاف التطبيق (البيانات تبقى محفوظة)
docker compose down

# إيقاف + حذف كل البيانات نهائياً (احذر!)
docker compose down -v

# تحميل نموذج Ollama إضافي يدوياً من داخل الحاوية العاملة
docker compose exec datalens ollama pull qwen2.5:7b

# فتح سطر أوامر داخل الحاوية (لغرض تشخيصي)
docker compose exec datalens bash

# عرض المساحة التي يستخدمها الـ volume
docker system df -v
```

## تحديث التطبيق لاحقاً

عند وجود نسخة جديدة من كود التطبيق:

```bash
docker compose up -d --build
```
هذا يعيد بناء الصورة فقط — البيانات في الـ volume لا تتأثر إطلاقاً.

## استخدام كرت GPU (اختياري، لتسريع Ollama بشكل كبير)

لو كان السيرفر يملك كرت NVIDIA:
1. ثبّت [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) على المضيف
2. فعّل السطور المُعلّقة تحت `deploy.resources.reservations` في `docker-compose.yml`
3. أعد التشغيل: `docker compose up -d --build`

## ملاحظات أمنية
- الحاوية تعمل بمستخدم غير جذري (`appuser`) لكل من التطبيق و Ollama
- `security_opt: no-new-privileges` مُفعّل افتراضياً
- عدّل `deploy.resources.limits.memory` في `docker-compose.yml` حسب موارد سيرفرك الفعلية لمنع استهلاك كل الذاكرة
