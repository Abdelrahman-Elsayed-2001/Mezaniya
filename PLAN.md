# خطة تحويل «ميزانيتي الشهرية» (mezaniya.xlsx) إلى تطبيق ويب

- **Backend:** Python 3.14 + Flask (WSGI) شغال على **Waitress** ومتاح على الشبكة
- **DB:** SQLite (مكتبة `sqlite3` المدمجة، WAL)
- **Frontend:** صفحات ويب عربية RTL (Jinja + Alpine.js + Chart.js) من غير build step
- **أكتر من مستخدم:** كل مستخدم بياناته وإعداداته منفصلة تماماً

---

## 1) ملخص الملف الحالي

| الشيت | الدور |
|---|---|
| **Dashboard** | إدخال (الدخل، المصروفات، الطوارئ، الادخار، الأهداف) + نتائج (الملخص، الحالة، الاقتراحات، التحذيرات) + رسمين |
| **Backend** | أنواع المصروفات، معايير التقييم، خطة توزيع الصافي، توزيع المدخر على الأهداف، بيانات الرسوم |

حدود الشيت (6 دخل / 35 مصروف / 10 أهداف) مش هتبقى موجودة في التطبيق.

---

## 2) المدخلات

### إعدادات كل مستخدم (Backend سابقاً + جديد)
| الإعداد | الأصل | الافتراضي |
|---|---|---|
| ادخار ممتاز إذا ≥ | D4 | 20% |
| ادخار جيد إذا ≥ | D5 | 10% |
| الحد الآمن للأقساط ≤ | D6 | 33% |
| الحد المقترح للكماليات ≤ | D7 | 30% |
| نسبة الاستقطاع للطوارئ من الصافي | D8 | 30% |
| % ادخار من الباقي (الطوارئ غير مكتمل) | D11 | 20% |
| % ادخار من الباقي (الطوارئ مكتمل) | D12 | 50% |
| **عدد أشهر مرجع الطوارئ** | J26 | 3 |
| **معامل زيادة مرجع الطوارئ** | J26 | 1.25 |
| العملة | نص ثابت | ر.س |

بالإضافة لقوائم كل مستخدم: **أنواع المصروفات**، و**أنواع الأهداف**، و**البنود المتكررة** (القسم 5).

### أنواع المصروفات
كل نوع له اسم و`role`. الحسابات بتعتمد على الـ role مش على الاسم، فتغيير الاسم مش هيبوظ حاجة:

| الاسم الافتراضي | role | ملاحظات |
|---|---|---|
| أساسي | `essential` | نظامي، ما يتمسحش |
| كمالي | `luxury` | نظامي |
| دين/أقساط | `debt` | نظامي |
| ادخار | `savings` | نظامي |
| طوارئ | `emergency` | نظامي |
| العطاء والمساندة، تحويل خارجي، الاستثمار + أي نوع جديد | `other` | |

### بيانات كل شهر
- **الدخل:** المصدر، المبلغ، «سحب من المدخرات» (checkbox بدل ما نعتمد على مطابقة النص)، ملاحظات.
- **المصروفات:** البند، النوع، كاش، **رصيد التقسيط المتبقي**، **عدد الدفعات المتبقية**، مرحّل من الشهر السابق (للقراءة بس)، حالة الدفع، مبلغ الترحيل، تاريخ الدفع، ملاحظات.
- **الطوارئ:** المبلغ الحالي (J22)، الحد الأدنى (J25).
- **الادخار:** المدخر الحالي (B56).
- **الأهداف:** الهدف، **النوع**، المبلغ المطلوب، الأولوية % (اختياري)، توزيع يدوي (اختياري)، ملاحظات.

---

## 3) المنطق والمعادلات (مواصفة الـ engine)

> `null` = خلية فاضية، `n(x)` = القيمة الرقمية أو 0، `safe_div(a,b)` = `a/b` أو 0 لو b=0.
> المواضع اللي **اتغيرت عن الإكسيل** معلَّمة بـ 🆕.

### 3.1 المصروف الشهري لكل بند 🆕
```
base      = cash + (inst_balance / inst_count if inst_count > 0 else inst_balance)   # F في الإكسيل
due       = base + carried_in                                   # 🆕 المطلوب الشهر ده
deferred  = deferred_amount if status == 'deferred' else 0      # 🆕 0 < deferred ≤ due
effective = due - deferred                                      # 🆕 ده اللي بيتحسب في مصروفات الشهر
paid_part = due if status=='paid' else (due - deferred if status=='deferred' else 0)
```
- الترحيل بينقل المبلغ من الشهر ده للشهر اللي بعده، فـ`effective` بيقل هنا ويزيد هناك من غير ما يتحسب مرتين.
- حالة «ترحيل» معناها إن الجزء اللي مش مرحّل اتدفع. مثال: قسط 500، ترحيل 100، يبقى المدفوع 400. ولو البند كله اترحّل يبقى المدفوع 0.
- لو مفيش ترحيل خالص، الأرقام بتطابق الإكسيل بالظبط.

### 3.2 الملخص
```
income     = Σ incomes.amount                      # J5 (يشمل السحب)
withdrawal = Σ incomes where is_savings_withdrawal  # B58
expenses   = Σ effective                            # J6
debt / luxury / essential = Σ effective by role     # J7 / J8 / J9
net        = income - expenses                      # J10
pct(x)     = safe_div(x, income)
```

### 3.3 الحالة العامة (أول شرط يتحقق)
| # | الشرط | النص |
|---|---|---|
| 1 | `net < 0` | 🔴 خطر - عجز |
| 2 | `net - withdrawal < 0` | 🟠 مزنوق - السحب من المدخرات غطّى العجز |
| 3 | `pct(debt) > max_debt and pct(luxury) > max_luxury` | 🟠 انتبه - أقساط وكماليات مرتفعة |
| 4 | `pct(debt) > max_debt` | 🟠 انتبه - أقساط مرتفعة |
| 5 | `pct(luxury) > max_luxury` | 🟠 انتبه - كماليات مرتفعة |
| 6 | `em_remaining > 0` | 🟡 جيد - كمّل صندوق الطوارئ |
| 7 | `pct(net) >= excellent` | 🟢 ممتاز |
| 8 | `pct(net) >= good` | 🟡 جيد |
| 9 | غير ذلك | 🟠 مقبول - فائضك قليل |

### 3.4 صندوق الطوارئ
```
em_from_expenses = Σ effective where role=emergency                 # J23
em_after         = em_current + em_from_expenses                    # J24
em_reference     = ref_months × (essential + debt) × ref_factor     # J26 🆕 الاتنين من الإعدادات
em_remaining     = max(0, em_min - em_after)                        # J27
em_status        = "✅ مكتمل" | "⚠️ غير مكتمل"                       # J28
em_months        = 0 if em_remaining == 0
                   else ceil(max(0, em_min - em_current) / (suggest_emergency + em_from_expenses))  # "-" لو القسمة على صفر
```

### 3.5 توزيع الصافي الشهري
```
available         = max(0, net - withdrawal)
base              = max(0, net - withdrawal + em_from_expenses + sav_from_expenses)
em_gap_before     = max(0, em_min - em_current)
em_target         = min(em_gap_before, base × emergency_cut_rate)
suggest_emergency = min(max(0, em_target - em_from_expenses), available)
sav_rate          = rate_em_complete if em_gap_before == 0 else rate_em_incomplete
suggest_saving    = min(max(0, base × sav_rate - sav_from_expenses), available - suggest_emergency)
safe_spend        = available - suggest_emergency - suggest_saving
days              = calendar.monthrange(year, month)[1]           # 🆕 28/29/30/31 بدل 30
safe_daily        = safe_spend / days
safe_weekly       = safe_spend / days × 7
return_to_savings = min(withdrawal, max(0, net))
```

### 3.6 الادخار
```
sav_from_expenses = Σ effective where role=savings      # B57
sav_total         = sav_current + sav_from_expenses - withdrawal
allocated         = Σ goals.allocated
unallocated       = sav_total - allocated
over_requested    = max(0, Σ requested - max(0, sav_total))      # 🆕
```
حالة التوزيع: ❌ السحب أكبر من المدخر المتاح ← ❌ الموزع أكبر من المتوفر (`over_requested > 0`) ← ✅ يكفي كل الأهداف ← ✅ سليم.

### 3.7 توزيع المدخر على الأهداف 🆕 (تكراري / water-filling)
**قرار:** النسبة والمبلغ اليدوي وجهين لنفس الحاجة. لو كتبت النسبة، المبلغ بيتحسب، ولو كتبت المبلغ، النسبة بتتحسب (`المبلغ ÷ المدخر المتاح`). الهدف اليدوي بيتعامل كأنه أولوية عادية في التوزيع وفي المقترح الشهري، لكن **المبلغ اليدوي سقف ثابت**. يعني إعادة التوزيع عمرها ما تديه أكتر من المبلغ اللي كتبته، والسقف الفعلي هو الأقل بين المبلغ اليدوي والمبلغ المطلوب. أي هدف ملوش مبلغ مطلوب بيتجاهل تماماً.
```
pool  = max(0, sav_total)                     # المدخر المتاح (مثال: 200)
valid = target is not None and target > 0     # غير كده الهدف متجاهل

p = manual / pool   (safe_div)   if manual is not None     # 100 يدوي ← 50%
    priority                     if priority is not None   # 50% ← 100
    auto                         otherwise
explicit_sum = Σ p (valid explicit goals)
auto_share   = safe_div(max(0, 1 - explicit_sum), count(valid auto goals))
eff = p | auto_share  (0 لو مش valid)
S = Σ eff

# الفائض بيتوزع تاني لحد ما الفلوس تخلص أو كل الأهداف تتملى
money = pool × min(1, S)
loop:
    cap    = min(target, manual) for manual goals, else target
    active = valid goals with eff > 0 and alloc < cap
    stop if money ≈ 0 or no active
    for g in active: x = min(cap - alloc, money × eff / Σeff[active]); alloc += x
    money -= Σx
```
- اللوب بيخلص في N لفة بالكتير.
- لو مجموع النسب أقل من 100% ومفيش أهداف تلقائي، الجزء اللي فاضل بيفضل «غير موزع» عن قصد.
- `requested` = المبلغ اللي اتطلب صراحةً (اليدوي، أو النسبة × pool). لو `Σ requested > pool` → ❌ الموزع أكبر من المتوفر.

لكل هدف:
```
allocated = alloc                               # التوزيع الفعلي
remaining = max(0, target - allocated)  (0 لو ملوش target)
progress  = safe_div(allocated, target)
monthly_pool = suggest_saving + sav_from_expenses
share     = eff / Σeff (للأهداف اللي ليها متبقي) | بالتساوي لو Σeff = 0
monthly   = min(remaining, monthly_pool × share)
months_text = "✅ المدخر يكفي" | f"{ceil(remaining/monthly)} شهر" | "-"
```
الإجماليات زي صف 75 في الإكسيل.

### 3.8 تتبع الدفع 🆕
```
paid        = Σ paid_part                         # المدفوع فعلاً
deferred    = Σ deferred                          # 🆕 مرحّل للشهر الجاي
unpaid      = expenses - paid                     # مخطط ولسه ما اندفعش
cash_now    = income - paid                       # الرصيد الفعلي
paid_count  = "N مدفوع" + "M مرحّل"
```

### 3.9 التحذيرات
التسعة اللي في الإكسيل زي ما هي، والحدود والنسب جاية من إعدادات المستخدم:
عجز، مزنوق، السحب أكبر من المتاح، أقساط مرتفعة، كماليات مرتفعة، طوارئ ناقص، فائض قليل، موزع أكبر من المتوفر، لازم ترجّع للمدخرات.

**🆕 تحذيرين جداد (اختياري):**
- «بنود محتاجة سعر»: بنود متكررة مبلغها متغير ولسه ما اتكتبش مبلغها.
- «فيه مبلغ مرحّل من الشهر اللي فات»: للعلم بس.

### 3.10 قواعد التحقق (في الـ API)
- النوع لازم يكون من أنواع **نفس المستخدم**.
- `status ∈ {unpaid, paid, deferred}`، ولو `deferred` لازم `0 < deferred_amount ≤ due`.
- `Σ manual ≤ sav_total`.
- الأولوية ≥ 0، و`Σ priority ≤ 1`.
- المبالغ ≥ 0، وعدد الدفعات عدد صحيح ≥ 0.
- `paid_at` تاريخ صحيح أو فاضي.

### 3.11 الرسوم البيانية
> اتنفذت في المرحلة 7 بـ HTML/CSS من غير Chart.js، كلها بلون واحد ومعاها tooltip وجدول بديل.
> الـ doughnut بتاع الإكسيل اتبدل بأعمدة أفقية مرتبة، لأن 9 أجزاء في دايرة صعب تتقري.

1. **أعمدة أفقية:** المصروفات حسب النوع + الفائض، والمبلغ ونسبته من الدخل مكتوبين على كل عمود.
2. **Bar التقدم 🆕 (متصلح):** الطوارئ (المتوفر `min(em_after, em_min)` / المتبقي `em_remaining`)، والأهداف (المتوفر `allocated` / **المتبقي `Σ remaining`** بدل `D75` اللي كان غلط).
3. **🆕 الصرف حسب اليوم:** عمود لكل يوم في الشهر (1 → عدد أيام الشهر) بمجموع `paid_part` حسب `paid_at`. بجانبه عمود منفصل اسمه **«بدون تاريخ»** للمدفوع اللي ملوش تاريخ. عمود «خارج الشهر» بيظهر بس لو فيه تاريخ برة أيام الشهر.
4. **🆕 (اختياري):** الأهداف حسب النوع.

### 3.12 التأكد من الأرقام
لو مفيش ترحيل والشهر 30 يوم، لازم الـ engine يطلع نفس أرقام الملف الحالي: دخل 5,600، صافي 41.67، أقساط 40.2%، كماليات 53.3%، حالة «🟠 مزنوق»، رجّع 41.67، مدخر −10، غير موزع −20، **8 تحذيرات**. ده الـ golden test.

---

## 4) الشهور والترحيل 🆕

### 4.1 فكرة السلسلة
كل بند في شهر ممكن يكون مربوط بالبند اللي قبله في الشهر السابق عن طريق `prev_expense_id`. الربط ده هو اللي بيخلّي التقسيط يكمّل، والبنود المتكررة تكمّل، والمبالغ المرحّلة تقع على البند الصح.

### 4.2 إنشاء شهر جديد (Wizard)
لما تضغط «شهر جديد» والمصدر هو آخر شهر موجود قبله:

| الخطوة | اللي بيحصل |
|---|---|
| 1. الأرصدة | الطوارئ = `em_after` بتاع الشهر السابق، والمدخر = `sav_total`، والحد الأدنى للطوارئ بيتنسخ. فيه checkbox: «رجّعت X للمدخرات؟» يضيف `return_to_savings` على المدخر. الأرقام دي قابلة للتعديل. |
| 2. الدخل المتكرر | من جدول البنود المتكررة (راتب...) |
| 3. المصروفات المتكررة | من الجدول: لو السعر ثابت بيتكتب، ولو متغير بيتكتب 0 مع علامة «محتاج سعر» 🟡 |
| 4. التقسيط | كل بند `inst_count > 1` بينزل بند جديد فيه `inst_balance' = inst_balance - inst_balance/inst_count` و`inst_count' = inst_count - 1`، والكاش = 0. القسط الشهري بيفضل زي ما هو. لو `inst_count = 1` يبقى ده آخر قسط، ومفيش بند جديد. |
| 5. المرحّل | كل بند حالته «ترحيل» بيروح مبلغه في `carried_in` للبند اللي مكمّل منه (من خطوة 3 أو 4). لو مفيش بند مكمّل، بيتعمل بند جديد بنفس الاسم والنوع وفيه ملاحظة «مرحّل من 10/2026». |
| 6. اللي ما اندفعش | البنود اللي لسه «لم يُدفع» بتظهر في قائمة، وجنب كل واحد checkbox «رحّله». لو اخترته، الشهر القديم بيتحول عنده لـ `deferred` بالمبلغ كله وبعدها بيتطبق عليه خطوة 5. |
| 7. الأهداف | بتتنسخ كلها (الهدف، النوع، المبلغ، الأولوية، اليدوي)، وفيه اختيار تشيل الأهداف اللي خلصت. |

**أمثلة:**
- **قسط 500 دفعت منه 400:** تختار حالة «ترحيل» ومبلغ 100. الشهر الجاي القسط يبقى 500 + 100 مرحّل = **600**، وعدد الدفعات بيقل 1.
- **فاتورة كهرباء مأجلة بالكامل:** تختار «ترحيل» بالمبلغ كله. الشهر الجاي البند المتكرر بيتعمل بسعر 0 «محتاج سعر» + المرحّل. لما تكتب سعر الشهر الجديد يبقى المطلوب = الجديد + القديم.

**قواعد إضافية (اتنفذت في المرحلة 5):**
- الشهر الجديد بالنسخ لازم يكون **بعد آخر شهر موجود**، وبيتنسخ منه. ممكن تتخطى شهور (مثلاً من 10 لـ 12).
- رصيد المدخر لو طلع بالسالب بيبدأ الشهر الجديد من صفر، وتقدر تغيّر أي رصيد من الـ wizard.
- البند اللي جاي من بند متكرر **متوقف** ما بيتنسخش كبند متكرر. لو كان تقسيط بيكمّل كتقسيط.

### 4.3 المزامنة الحية للترحيل
لو الشهر اللي بعده **موجود بالفعل**، الـ `carried_in` في البند المكمّل بيفضل دايماً مساوي للمبلغ المرحّل في الشهر الحالي، وده بيتحدث في نفس الـ transaction:
- **ترحيل جديد:** لو مفيش بند مكمّل، بيتعمل بند ترحيل جديد.
- **إلغاء الترحيل أو مسح البند:** البند المكمّل `carried_in` بتاعه بيرجع صفر. ولو كان بند ترحيل بس وما اتدفعش، بيتمسح.
- **تقليل الترحيل:** لو البند المكمّل نفسه كان مترحّل للشهر اللي بعده، مبلغ ترحيله بيتقص لحد المطلوب الجديد، والتغيير ده بيكمّل على باقي السلسلة.
- **التقسيط والبنود المتكررة:** بيتنسخوا مرة واحدة وقت إنشاء الشهر بس، ومش بيتزامنوا بعد كده.

### 4.4 الواجهة
قائمة الشهور مع اختيار الشهر، وزر «شهر جديد» (Wizard)، وحذف آخر شهر بس عشان السلسلة ما تتكسرش.

---

## 5) البنود المتكررة (الإعدادات) 🆕

| الحقل | الوصف |
|---|---|
| النوع | دخل / مصروف |
| الاسم | مثلاً: راتب، انترنت، فاتورة الكهرباء |
| نوع المصروف | للمصروف بس |
| المبلغ | **اختياري**: لو مكتوب يبقى سعر ثابت (انترنت 120)، ولو فاضي يبقى متغير (كهرباء) وبيتكتب كل شهر |
| سحب من المدخرات | للدخل بس |
| نشط | تقدر توقفه من غير ما تمسحه |
| ملاحظات | |

زر «طبّق على الشهر الحالي» بيضيف البنود الناقصة من غير تكرار (بيتعرف عليها بـ `template_id`).

---

## 6) المستخدمين وتسجيل الدخول 🆕

### 6.1 الشاشات
- **تسجيل الدخول:** اسم المستخدم + كلمة السر + زر **«تسجيل مستخدم جديد»**.
- **تسجيل مستخدم جديد:** اسم المستخدم + كلمة السر + تأكيد كلمة السر. بعد التسجيل بيدخل على طول، ويتعمله seed: إعدادات افتراضية، وأنواع مصروفات، وأنواع أهداف، وأول شهر فاضي.
- **الإعدادات ← الحساب:** تغيير كلمة السر (القديمة + الجديدة + التأكيد) + تسجيل خروج.

### 6.2 تخزين كلمة السر
- العادي: hash بـ `werkzeug.security.generate_password_hash` (scrypt). القيمة المخزنة بتبدأ بـ `scrypt:`، فعمرها ما تبدأ بـ `#`.
- **الاستعادة اليدوية:** لو حد نسي كلمة السر، تفتح الـ db وتكتب في `users.password_hash` قيمة زي `#1234#`.
- عند الدخول: لو القيمة المخزنة بتبدأ وتنتهي بـ `#`، المستخدم بيكتب القيمة **بالعلامات** (`#1234#`) والمقارنة بتبقى نص صريح (`hmac.compare_digest`). أي قيمة تانية بتتعامل على إنها hash. لو الدخول نجح بكلمة يدوية، المستخدم بيتحوّل **إجبارياً** لشاشة تغيير كلمة السر قبل أي حاجة تانية، والكلمة الجديدة بتتخزن hash.
- (اختياري) أمر سطر: `python manage.py reset-password <username>` بيعمل نفس الحكاية من غير ما تفتح الـ db.

### 6.3 الأمان على الشبكة
- Session: Flask signed cookie (`HttpOnly`, `SameSite=Lax`, مدة 30 يوم). الـ `SECRET_KEY` بيتولد مرة واحدة ويتحفظ في `data/secret_key`.
- CSRF: كل طلبات الكتابة لازم تكون JSON وفيها header مخصوص `X-Requested-With`، ده مع `SameSite` بيكفي.
- حماية من التخمين: بعد 5 محاولات غلط لنفس المستخدم/IP، قفل 5 دقايق.
- شروط اسم المستخدم: 3–32 حرف، ومش case-sensitive (`COLLATE NOCASE`). كلمة السر: 6 أحرف على الأقل.
- `ALLOW_REGISTRATION` في ملف الإعدادات (افتراضياً `true`) عشان تقفل التسجيل لو حبيت.
- Waitress بيشتغل على `0.0.0.0:8080` (من ملف config/env) + قاعدة في Windows Firewall. الشبكة داخلية فقط، فـ HTTP كفاية دلوقتي، ولو اتنقل online هنضيف HTTPS بعدين.

---

## 7) فصل بيانات المستخدمين 🆕

1. **`user_id` في كل جدول** (حتى الجداول الفرعية)، بدل ما نعتمد على join.
2. **Composite foreign keys** بتضمن على مستوى الـ DB إن البند ما يقدرش يشاور على نوع أو شهر أو قالب بتاع مستخدم تاني:
   `FOREIGN KEY (type_id, user_id) REFERENCES expense_types(id, user_id)`.
3. **`UNIQUE` بيشمل `user_id`**، مثلاً `UNIQUE(user_id, name)`، عشان كل مستخدم يقدر يسمي أنواعه زي ما هو عايز.
4. **Repository layer:** كل function لازم تاخد `user_id` كأول parameter، وكل query فيها `WHERE ... AND user_id = ?`. الـ `user_id` بيتاخد من الـ session **بس**، عمره ما بييجي من الـ request.
5. أي id بتاع مستخدم تاني بيرجّع **404** (مش 403) عشان ما نكشفش إن السجل موجود.
6. **اختبارات العزل:** مستخدمين A وB، وparametrized test على **كل endpoint** (قراءة، تعديل، حذف) بـ ids بتاعة B وA داخل، والمتوقع 404 ومفيش أي تغيير في بيانات B.

---

## 8) المعمارية

```
Browser (RTL, Alpine.js, Chart.js)
   │ JSON + session cookie
   ▼
Waitress (0.0.0.0:8080) → Flask
   ├── auth/      login/register/logout/change-password, login_required
   ├── api/       REST + validation
   ├── services/  rollover.py (الشهر الجديد), carry.py (مزامنة الترحيل)
   ├── engine/    حسابات نقية — بدون DB
   └── db/        sqlite3 repo (scoped by user_id) + migrations
           ▼
       data/mezaniya.db (+ نسخ احتياطية يومية)
```
- الـ DB بتخزن المدخلات بس، والنتائج بتتحسب في كل طلب.
- كل كتابة بترجّع الـ dashboard كامل محدّث. الواجهة بتعمل debounce ‎~300ms.
- الفلوس بـ `Decimal` جوه الـ engine.
- SQLite: `journal_mode=WAL`، `busy_timeout=5000`، `foreign_keys=ON`، وconnection لكل request.

### هيكل المشروع
```
mezaniya/
├── app/
│   ├── __init__.py            # create_app()
│   ├── config.py              # HOST, PORT, ALLOW_REGISTRATION, DB_PATH
│   ├── auth/  routes.py  passwords.py  ratelimit.py
│   ├── engine/  models.py  calc.py  goals.py  rules.py  fmt.py
│   ├── services/  rollover.py  carry.py  seed_user.py
│   ├── db/  schema.sql  migrations/  connection.py  repo/*.py
│   ├── api/  budgets.py incomes.py expenses.py goals.py settings.py
│   │         expense_types.py goal_categories.py templates.py validation.py
│   ├── templates/  login.html register.html change_password.html app.html settings.html
│   └── static/  css/ js/ vendor/ (alpine, chart.js, خط Cairo/Tajawal — كلهم local)
├── tests/
│   ├── test_engine_golden.py  test_engine_edges.py  test_goals_waterfill.py
│   ├── test_rollover.py  test_carry.py
│   ├── test_auth.py  test_isolation.py  test_api.py
├── manage.py                  # reset-password, backup (اختياري)
├── run.py                     # waitress.serve(...)
└── requirements.txt           # flask, waitress, pytest (+ openpyxl للاستيراد)
```

---

## 9) قاعدة البيانات

```sql
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,          -- scrypt:... أو #plain# للاستعادة اليدوية
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at TEXT
);

CREATE TABLE user_settings (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  excellent_rate REAL NOT NULL DEFAULT 0.20,
  good_rate REAL NOT NULL DEFAULT 0.10,
  max_debt_rate REAL NOT NULL DEFAULT 0.33,
  max_luxury_rate REAL NOT NULL DEFAULT 0.30,
  emergency_cut_rate REAL NOT NULL DEFAULT 0.30,
  saving_rate_em_incomplete REAL NOT NULL DEFAULT 0.20,
  saving_rate_em_complete REAL NOT NULL DEFAULT 0.50,
  emergency_ref_months REAL NOT NULL DEFAULT 3,
  emergency_ref_factor REAL NOT NULL DEFAULT 1.25,
  currency TEXT NOT NULL DEFAULT 'ر.س'
);

CREATE TABLE expense_types (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'other'
       CHECK (role IN ('essential','luxury','debt','savings','emergency','other')),
  is_system INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  UNIQUE (user_id, name), UNIQUE (id, user_id)
);

CREATE TABLE goal_categories (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  UNIQUE (user_id, name), UNIQUE (id, user_id)
);

CREATE TABLE recurring_templates (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('income','expense')),
  name TEXT NOT NULL,
  type_id INTEGER,                       -- للمصروف فقط
  amount REAL CHECK (amount IS NULL OR amount >= 0),   -- NULL = سعر متغير
  is_savings_withdrawal INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  notes TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  UNIQUE (id, user_id),
  FOREIGN KEY (type_id, user_id) REFERENCES expense_types(id, user_id)
);

CREATE TABLE budgets (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  year INTEGER NOT NULL, month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
  emergency_current REAL NOT NULL DEFAULT 0,
  emergency_min REAL NOT NULL DEFAULT 0,
  savings_current REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (user_id, year, month), UNIQUE (id, user_id)
);

CREATE TABLE incomes (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  budget_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  amount REAL NOT NULL DEFAULT 0 CHECK (amount >= 0),
  is_savings_withdrawal INTEGER NOT NULL DEFAULT 0,
  template_id INTEGER,
  needs_amount INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (budget_id, user_id) REFERENCES budgets(id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (template_id, user_id) REFERENCES recurring_templates(id, user_id) ON DELETE SET NULL
);

CREATE TABLE expenses (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  budget_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  type_id INTEGER NOT NULL,
  cash_amount REAL NOT NULL DEFAULT 0 CHECK (cash_amount >= 0),
  inst_balance REAL NOT NULL DEFAULT 0 CHECK (inst_balance >= 0),   -- رصيد التقسيط المتبقي
  inst_count INTEGER NOT NULL DEFAULT 0 CHECK (inst_count >= 0),     -- الدفعات المتبقية
  inst_original_count INTEGER,                                       -- للعرض: "الدفعة 3 من 5"
  carried_in REAL NOT NULL DEFAULT 0 CHECK (carried_in >= 0),        -- مرحّل من الشهر السابق
  status TEXT NOT NULL DEFAULT 'unpaid' CHECK (status IN ('unpaid','paid','deferred')),
  deferred_amount REAL NOT NULL DEFAULT 0 CHECK (deferred_amount >= 0),
  paid_at TEXT,                                                      -- YYYY-MM-DD
  notes TEXT,
  template_id INTEGER,
  prev_expense_id INTEGER REFERENCES expenses(id) ON DELETE SET NULL,
  needs_amount INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  UNIQUE (id, user_id),
  FOREIGN KEY (budget_id, user_id) REFERENCES budgets(id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (type_id, user_id) REFERENCES expense_types(id, user_id),
  FOREIGN KEY (template_id, user_id) REFERENCES recurring_templates(id, user_id) ON DELETE SET NULL
);

CREATE TABLE goals (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  budget_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  category_id INTEGER,
  target_amount REAL,
  priority REAL CHECK (priority IS NULL OR priority >= 0),          -- NULL = تلقائي ≠ 0
  manual_allocation REAL CHECK (manual_allocation IS NULL OR manual_allocation >= 0),
  notes TEXT,
  CHECK (priority IS NULL OR manual_allocation IS NULL),          -- واحد بس منهم (النسبة أو المبلغ)
  sort_order INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (budget_id, user_id) REFERENCES budgets(id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (category_id, user_id) REFERENCES goal_categories(id, user_id) ON DELETE SET NULL
);

CREATE INDEX ix_expenses_budget ON expenses(user_id, budget_id);
CREATE INDEX ix_expenses_prev ON expenses(prev_expense_id);
CREATE TABLE schema_version (version INTEGER NOT NULL);
```
**Seed لكل مستخدم جديد:** صف `user_settings`، والأنواع الثمانية بالـ roles بتاعتها، وأنواع أهداف (أساسي، كمالي، استثمار، أخرى)، وأول شهر هو الشهر الحالي.

---

## 10) الـ API (كله محتاج login، ومقفول على المستخدم الحالي)

| Method | Path | الوظيفة |
|---|---|---|
| POST | `/auth/register` `/auth/login` `/auth/logout` | الحساب |
| POST | `/auth/change-password` | تغيير كلمة السر (وإجباري بعد `#plain#`) |
| GET | `/api/budgets` | قائمة الشهور |
| POST | `/api/budgets/rollover-preview` | 🆕 معاينة الـ wizard من غير ما يكتب حاجة: الأرصدة، والمتكرر، والتقسيط، والمرحّل، واللي ما اندفعش. `{year?, month?, carry_unpaid_ids?}`، ولو من غير شهر بيبقى الشهر اللي بعد آخر شهر |
| POST | `/api/budgets` | شهر جديد `{year, month, copy_from_previous?, carry_unpaid_ids?, add_return_to_savings?, drop_completed_goals?, emergency_current?, emergency_min?, savings_current?}` |
| DELETE | `/api/budgets/{id}` | آخر شهر بس |
| GET | `/api/budgets/{id}/dashboard` | المدخلات + كل النتائج |
| PATCH | `/api/budgets/{id}` | الأرصدة والحد الأدنى |
| CRUD | `/api/budgets/{id}/incomes`, `/expenses`, `/goals` | |
| POST | `/api/budgets/{id}/apply-templates` | 🆕 يضيف البنود المتكررة الناقصة |
| GET/PATCH | `/api/settings` | المعايير |
| CRUD | `/api/expense-types`, `/api/goal-categories`, `/api/templates` | 🆕 مع منع حذف حاجة مستخدمة أو نوع نظامي |

---

## 11) الواجهة

1. **Login / Register / Change password:** صفحات بسيطة RTL.
2. **Header:** اختيار الشهر، وزر «شهر جديد»، واسم المستخدم، والإعدادات، والخروج.
3. **الدخل:** جدول بتعديل inline، فيه checkbox «سحب من المدخرات» وعمود ملاحظات.
4. **المصروفات:**
   - الأعمدة: البند، النوع، كاش، رصيد التقسيط، الدفعات المتبقية (مع «3 من 5»)، **مرحّل** (badge)، **الشهري/المطلوب** (محسوب)، **حالة الدفع** (⏳ / ✅ / ↪️ ترحيل)، **مبلغ الترحيل** (بيظهر بس مع ترحيل وافتراضياً المبلغ كله)، **تاريخ الدفع** (بيتملى بتاريخ النهارده من جهاز المستخدم لما تختار مدفوع أو ترحيل، وتقدر تعدله أو تمسحه)، وملاحظات (أيقونة 📝 لو فيه ملاحظة).
   - البنود اللي «محتاج سعر» بتظهر مميزة بالأصفر.
   - صف الإجماليات: المدفوع، والمرحّل، والمخطط، والرصيد الفعلي.
5. الملخص، والحالة، والاقتراحات الشهرية (والصرف اليومي حسب أيام الشهر الفعلية)، والطوارئ، والادخار والأهداف (مع عمود نوع الهدف وفلتر بالنوع)، والتحذيرات.
6. **الرسوم:** doughnut، والتقدم (متصلح)، و**الصرف حسب اليوم** + «بدون تاريخ».
7. **Wizard الشهر الجديد:** خطوات القسم 4.2 مع معاينة قبل التأكيد.
8. **الإعدادات** (تابات): المعايير · أنواع المصروفات · أنواع الأهداف · البنود المتكررة · الحساب (كلمة السر).
9. قسم قابل للطي «تفاصيل الحساب» عشان تشوف الحسابات بالتفصيل.

---

## 12) مراحل التنفيذ

> **الحالة:** المراحل 0–8 و10 خلصت (308 اختبار ناجحين). التشغيل والنسخ الاحتياطي واسترجاع كلمة السر مشروحين في [README.md](README.md). الـ wizard اتعمل كشاشة مراجعة واحدة بدل خطوات منفصلة، وفيها اختيار الشهر، والأرصدة، والبنود اللي ما اندفعتش، والمصروفات اللي هتتنقل، والدخل المتكرر، والأهداف. الباقي: المرحلة 9 (اختياري).

| # | المرحلة | معيار الإنجاز |
|---|---|---|
| 0 | تجهيز (venv، requirements، الهيكل، `run.py`) | Waitress شغال |
| 1 | **Engine** (القسم 3 كامل، بما فيه الترحيل وأيام الشهر والـ water-filling) | golden test يطابق الإكسيل + edge tests + اختبارات water-filling (حالات الإكسيل كان بيسيب فيها فلوس من غير توزيع) |
| 2 | DB + repo مقفول على `user_id` + migrations | اختبارات CRUD + composite FK بترفض الربط بين مستخدمين |
| 3 | Auth (تسجيل، دخول، خروج، تغيير كلمة السر، `#plain#`، rate limit) | `test_auth.py` |
| 4 | API + validation | `test_api.py` + **`test_isolation.py` على كل endpoint** |
| 5 | Rollover + مزامنة الترحيل | `test_rollover.py`: تقسيط بينتهي، قسط 500 مرحّل منه 100 يبقى 600، كهرباء مرحّلة بالكامل، شهر بعد ديسمبر بيروح يناير السنة الجاية، تعديل ترحيل بعد ما الشهر الجاي اتعمل |
| 6 | واجهة: login/register + الـ dashboard الأساسي | إدخال بيانات الشيت يطلع نفس الأرقام |
| 7 | واجهة: الدفع والترحيل والتاريخ والملاحظات + الرسوم الثلاثة | |
| 8 | واجهة: الإعدادات (5 تابات) + Wizard الشهر الجديد | |
| 9 | (اختياري) استيراد `mezaniya.xlsx` كأول شهر لمستخدم | الاستيراد يطلع الـ golden numbers |
| 10 | تشغيل على الشبكة: config، firewall، نسخ احتياطي يومي لـ `data/`، `manage.py` | الدخول من جهاز تاني على الشبكة |

---

## 13) القرارات النهائية

1. **كلمة الاستعادة:** المستخدم بيكتبها بالعلامات `#1234#`، وبعد الدخول **لازم** يغيّرها.
2. **توزيع الأهداف:**
   - أي هدف ملوش مبلغ مطلوب بيتجاهل تماماً.
   - النسبة والمبلغ اليدوي مربوطين ببعض. المبلغ اليدوي بيتحول لنسبة ويتعامل كأولوية عادية، لكنه **سقف ثابت** (القسم 3.7).
3. **أنواع الأهداف:** قائمة منفصلة عن أنواع المصروفات، للتصنيف والفلتر والرسم بس.
4. **الشبكة:** داخلية بـ HTTP، والتسجيل مفتوح.
5. **الكاش في بند التقسيط:** بيتدفع مرة واحدة وما يتنقلش للشهر الجاي.
6. **الترحيل:** بيقلل مصروفات الشهر الحالي ويزوّد اللي بعده.
