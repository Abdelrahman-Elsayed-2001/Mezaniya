# التوثيق

| الملف | المحتوى |
|---|---|
| [user_guide.md](user_guide.md) | دليل المستخدم الكامل للموقع ومدير الخادم، مع دورة شهر كاملة وصور حقيقية |
| [whats_new.md](whats_new.md) · [whats_new.en.md](whats_new.en.md) | ما الجديد في كل إصدار، بالعربية والإنجليزية (يُولَّدان من ملفات اللغات) |
| [images/](images) | صور الدليل: `web/` للموقع، و`manager/` لمدير الخادم |
| [tools/md_convert.py](tools/md_convert.py) | أداة تحويل أي ملف Markdown وصوره إلى Word وPDF |
| [tools/whats_new.py](tools/whats_new.py) | يكتب ملفي ما الجديد من نصوص `whats_new.*` في `app/i18n/languages` |

## تحويل الدليل إلى Word وPDF

```bash
pip install -r docs/tools/requirements.txt
python docs/tools/md_convert.py docs/user_guide.md
python docs/tools/md_convert.py docs/whats_new.md
```

يكتب `user_guide.docx` و`user_guide.pdf` بجانب الملف (أو في `--out-dir`). خيارات أخرى:

- `--format docx` أو `--format pdf` لملف واحد فقط.
- `--dir rtl` أو `--dir ltr` لتحديد الاتجاه (يُكتشف من النص افتراضيًا).
- `--html` للاحتفاظ بملف HTML الوسيط.
- `--browser "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"` إذا لم يجد الأداة المتصفح.

ملف PDF يطبعه متصفح Chromium: Playwright إن كان مثبتًا، وإلا Microsoft Edge (موجود في كل ويندوز 10 و11) أو Google Chrome. تثبيت Pillow اختياري، ويجعل الصور في Word بحجمها الحقيقي.

## ما الجديد عند كل إصدار

ملاحظات الإصدارات نصوص عادية في ملفات اللغات (`whats_new.v0_4_1` مثلًا، كل سطر نقطة)، وتعرضها صفحة «حول» في الموقع. عند إصدار جديد:

1. أضف الإصدار في أول `RELEASES` في [app/about.py](../app/about.py)، ونصه `whats_new.v<الإصدار>` في `ar.json` و`en.json`.
2. شغّل `python docs/tools/whats_new.py` ليكتب `whats_new.md` و`whats_new.en.md` (الاختبارات تفشل إن نسيت).
