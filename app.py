"""
AI_Orientation - المساعد الذكي للتوجيه الجامعي في الجزائر
================================================================
تطبيق FastAPI يقدم مساعدة ذكية لطلبة الجزائر لاختيار التخصص الجامعي المناسب.

منطق الخادم منظم على النحو التالي:
    - تحميل قاعدة بيانات التخصصات (database/majors.json) عند الإقلاع.
    - محرك قواعد محلي (Rule-Based Engine) يحلل رسالة المستخدم ويحدد النية
      (اقتراح / مقارنة / شرح / أسئلة عامة) ثم يبني ردًا منسقًا.
    - دالة generate_response() هي نقطة الدخول الموحدة: إن وُجد مفتاح
      OPENAI_API_KEY في متغيرات البيئة تُستخدم OpenAI API مع برومبت مخصص،
      وإلا يُستخدم المحرك المحلي القائم على majors.json.
    - نقطة نهاية REST واحدة (/api/chat) تستقبل رسالة المستخدم عبر Fetch API
      من الواجهة الأمامية، دون الحاجة لإعادة تحميل الصفحة.
"""

import json
import os
import re
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# الإعدادات الأساسية
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "database" / "majors.json"

# يحمّل متغيرات البيئة من ملف .env الموجود بجانب هذا الملف (إن وُجد).
# هذا يسمح بضبط المفتاح محليًا دون كتابته مباشرة داخل الكود المصدري.
load_dotenv(BASE_DIR / ".env")

app = FastAPI(title="AI_Orientation - المساعد الذكي للتوجيه الجامعي")

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# مفتاح OpenAI API (اختياري). يُقرأ من ملف .env أو من متغيرات البيئة، وليس مكتوبًا في الكود.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# نظام البرومبت المستخدم عند تفعيل OpenAI API
SYSTEM_PROMPT = (
    "أنت مساعد ذكاء اصطناعي متخصص في التوجيه الجامعي في الجزائر. "
    "مهمتك مساعدة طلبة البكالوريا على اختيار التخصص الجامعي المناسب، "
    "شرح نظام LMD، إجراءات التسجيل، المنح والإيواء، المدارس العليا والأقسام التحضيرية، "
    "التكوين المهني، الجامعات الخاصة، إعادة التوجيه، الدراسة والمنح بالخارج، وفرص العمل. "
    "أجب دائمًا باللغة العربية الفصحى المبسطة، وبأسلوب واضح ومنظم."
)


# ---------------------------------------------------------------------------
# تحميل قاعدة البيانات
# ---------------------------------------------------------------------------

def load_majors() -> List[dict]:
    """يحمّل بيانات التخصصات من ملف majors.json."""
    with open(DATABASE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


MAJORS: List[dict] = load_majors()

# خريطة الشعب المرادفة لتسهيل التعرف عليها من نص المستخدم
STREAM_ALIASES = {
    "رياضيات": ["رياضيات", "شعبة رياضيات", "رياضي"],
    "تقني رياضي": ["تقني رياضي", "تقني رياضي", "هندسة"],
    "علوم تجريبية": ["علوم تجريبية", "تجريبي", "علوم"],
    "تسيير واقتصاد": ["تسيير واقتصاد", "تسيير", "اقتصاد"],
    "آداب وفلسفة": ["آداب وفلسفة", "آداب", "فلسفة"],
    "لغات أجنبية": ["لغات أجنبية", "لغات"],
}


# ---------------------------------------------------------------------------
# أدوات مساعدة لتحليل رسالة المستخدم (Intent Detection بسيط قائم على القواعد)
# ---------------------------------------------------------------------------

def detect_stream(message: str) -> Optional[str]:
    """يحاول تحديد شعبة البكالوريا المذكورة في رسالة المستخدم."""
    for canonical, aliases in STREAM_ALIASES.items():
        for alias in aliases:
            if alias in message:
                return canonical
    return None


def detect_average(message: str) -> Optional[float]:
    """يستخرج المعدل الدراسي (رقم عشري أو صحيح) من رسالة المستخدم."""
    match = re.search(r"(\d{1,2}(?:[.,]\d{1,2})?)", message)
    if match:
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return None
    return None


def find_major_by_keyword(message: str) -> Optional[dict]:
    """يبحث عن تخصص مطابق للكلمات المفتاحية الموجودة في الرسالة."""
    best_match = None
    best_score = 0
    for major in MAJORS:
        score = 0
        if major["name"] in message:
            score += 5
        for kw in major["keywords"]:
            if kw in message:
                score += 1
        if score > best_score:
            best_score = score
            best_match = major
    return best_match if best_score > 0 else None


def find_two_majors(message: str) -> List[dict]:
    """يبحث عن تخصصين مذكورين في نفس الرسالة (لأجل المقارنة)."""
    found = []
    for major in MAJORS:
        if major["name"] in message or any(kw in message for kw in major["keywords"]):
            if major not in found:
                found.append(major)
        if len(found) == 2:
            break
    return found


# ---------------------------------------------------------------------------
# دوال بناء الردود (Response Builders)
# ---------------------------------------------------------------------------

def suggest_majors(stream: str, average: Optional[float]) -> str:
    """يقترح تخصصات مناسبة بناءً على الشعبة والمعدل مع ترتيب حسب القرب من المعدل."""
    # تصفية التخصصات التي تقبل الشعبة المذكورة
    candidates = [m for m in MAJORS if stream in m["streams"]]
    if not candidates:
        return f"لم أجد تخصصات مطابقة لشعبة **{stream}** حاليًا في قاعدة البيانات. جرّب ذكر شعبة أخرى."

    # إذا وُجد معدل، نرتب حسب الأقرب إلى min_average ثم نأخذ الأفضل (الأقل فرقًا)
    if average is not None:
        candidates = sorted(candidates, key=lambda m: abs(m["min_average"] - average))
        # نأخذ فقط التخصصات التي يكون معدل الطالب قريبًا من المطلوب (في حدود 1.5 درجة)
        eligible = [m for m in candidates if m["min_average"] <= average + 1.5]
        candidates = eligible if eligible else candidates[:5]  # إذا لم يوجد مؤهل، نأخذ أقرب 5
    else:
        candidates = candidates[:5]  # بدون معدل، نعرض أول 5

    header = f"### 🎓 تخصصات مقترحة لشعبة **{stream}**" + (f" بمعدل **{average}**" if average else "") + "\n"
    lines = [header]
    for idx, m in enumerate(candidates[:5], 1):
        lines.append(
            f"{idx}. **{m['name']}** — {m['description']}\n"
            f"   - المدة: {m['duration']}\n"
            f"   - الحد الأدنى للمعدل: {m['min_average']}\n"
            f"   - نظام الدراسة: {m.get('study_system', 'غير محدد')}\n"
            f"   - فرص العمل: {', '.join(m['career_opportunities'][:2])}"
        )
    lines.append("\nهل تريد شرحًا مفصلاً لأحد هذه التخصصات أو مقارنة بينها؟")
    return "\n".join(lines)

def compare_majors(major_a: dict, major_b: dict) -> str:
    """يبني جدول مقارنة Markdown بين تخصصين."""
    rows = [
        ("الوصف", major_a["description"], major_b["description"]),
        ("مدة الدراسة", major_a["duration"], major_b["duration"]),
        ("أهم المواد", "، ".join(major_a["core_subjects"][:4]), "، ".join(major_b["core_subjects"][:4])),
        ("المهارات المطلوبة", "، ".join(major_a["required_skills"][:3]), "، ".join(major_b["required_skills"][:3])),
        ("فرص العمل", "، ".join(major_a["career_opportunities"][:3]), "، ".join(major_b["career_opportunities"][:3])),
        ("الدراسات العليا", "، ".join(major_a["postgraduate_studies"]), "، ".join(major_b["postgraduate_studies"])),
    ]

    header = f"### ⚖️ مقارنة بين **{major_a['name']}** و **{major_b['name']}**\n"
    table = [
        f"| المعيار | {major_a['name']} | {major_b['name']} |",
        "|---|---|---|",
    ]
    for label, val_a, val_b in rows:
        table.append(f"| {label} | {val_a} | {val_b} |")

    return header + "\n" + "\n".join(table)

def explain_major(major: dict) -> str:
    """يبني تقريرًا كاملاً حول تخصص معيّن باستخدام كل البيانات المتوفرة."""
    lines = [
        f"### 📘 {major['name']}",
        "",
        major['description'],
        "",
        f"**⏳ مدة الدراسة:** {major['duration']}",
        f"**📋 نظام الدراسة:** {major.get('study_system', 'غير محدد')}",
        f"**🎯 الحد الأدنى التقريبي للمعدل:** {major['min_average']}",
        f"**🧭 الشعب المسموحة:** {', '.join(major['streams'])}",
        "",
        "**📚 أهم المواد الأساسية:**",
    ]
    lines.extend(f"- {s}" for s in major['core_subjects'])
    lines.extend([
        "",
        "**🧠 المهارات المطلوبة:**",
    ])
    lines.extend(f"- {s}" for s in major['required_skills'])
    lines.extend([
        "",
        "**💼 فرص العمل:**",
    ])
    lines.extend(f"- {s}" for s in major['career_opportunities'])
    lines.extend([
        "",
        "**🎓 الدراسات العليا الممكنة:**",
    ])
    lines.extend(f"- {s}" for s in major['postgraduate_studies'])
    lines.extend([
        "",
        f"**🏫 الجامعات المتوفرة:** {', '.join(major['universities'])}",
        "",
        f"**🔑 الكلمات المفتاحية:** {', '.join(major['keywords'])}",
    ])
    return "\n".join(lines)

def explain_lmd() -> str:
    return (
        "### 🧭 نظام LMD (ليسانس - ماستر - دكتوراه)\n\n"
        "نظام LMD هو النظام الجامعي المعتمد في الجزائر منذ سنة 2004، ويتكوّن من ثلاثة أطوار:\n\n"
        "- **الليسانس (Licence):** 3 سنوات (L1, L2, L3)، وتنقسم إلى جذع مشترك ثم تخصص دقيق.\n"
        "- **الماستر (Master):** سنتان (M1, M2) بعد الليسانس، ويكون إما أكاديميًا أو مهنيًا.\n"
        "- **الدكتوراه (Doctorat):** 3 سنوات على الأقل بعد الماستر، مخصصة للبحث العلمي.\n\n"
        "يعتمد النظام على وحدات تعليمية (UE) ورصيد نقاط (ECTS) قابلة للتحويل بين الجامعات."
    )

# أضف هذه الدالة الجديدة
def explain_engineer_state() -> str:
    return (
        "### 🎓 نظام مهندس دولة (Ingénieur d'État)\n\n"
        "نظام مهندس دولة هو نظام دراسي موازٍ لنظام LMD، يُطبَّق في المدارس الوطنية العليا "
        "والتي تُعد نخبوية وتتطلب مسابقة وطنية بعد البكالوريا (أو بعد الأقسام التحضيرية).\n\n"
        "**خصائص النظام:**\n"
        "- **المدة:** 5 سنوات دراسية (باستثناء بعض التخصصات كالطب التي قد تطول).\n"
        "- **الهيكل:** سنتان تحضيريتان (في الأقسام التحضيرية المدمجة أو المستقلة) + 3 سنوات تخصص.\n"
        "- **الشهادة:** تُمنح شهادة مهندس دولة، وهي معترف بها في سوق العمل المحلي والدولي.\n"
        "- **التخصصات:** تشمل الهندسة المدنية، الكهربائية، الميكانيكية، الإعلام الآلي، الاتصالات، وغيرها.\n"
        "- **المنافسة:** القبول يتم عبر مسابقة وطنية صارمة تعتمد على معدل البكالوريا ونتائج الاختبارات الكتابية والشفوية.\n\n"
        "**أبرز المدارس:**\n"
        "- المدرسة الوطنية المتعددة التقنيات (ENP الجزائر)\n"
        "- المدرسة الوطنية العليا للإعلام الآلي (ESI)\n"
        "- المدرسة العسكرية المتعددة التقنيات (EMP)\n"
        "- المدرسة الوطنية العليا للري (ENSH)\n"
        "- المدرسة الوطنية العليا للأشغال العمومية (ENSTP)\n\n"
        "**الفرق بينه وبين LMD:**\n"
        "- نظام LMD يُطبَّق في معظم الجامعات، ويمنح ليسانس (3 سنوات) + ماستر (سنتان).\n"
        "- نظام مهندس دولة يمنح شهادة معادلة للماستر في بعض الجوانب، لكنه أكثر تركيزًا على الجانب التطبيقي والهندسي.\n"
        "- المدارس العليا غالبًا ما توفر تكوينًا مكثفًا مع فرص تدريب ميدانية أفضل، ومعدلات توظيف مرتفعة.\n\n"
        "هل لديك سؤال عن مدرسة معينة أو تخصص هندسي دقيق؟"
    )
    
    return explain_engineer_state()
def explain_registration() -> str:
    return (
        "### 📝 التسجيل الجامعي في الجزائر\n\n"
        "1. **التسجيلات الأولية:** تتم إلكترونيًا عبر منصة الديوان الوطني للتعليم والتكوين عن بعد "
        "أو الموقع الرسمي لتوجيه البكالوريا مباشرة بعد صدور نتائج البكالوريا.\n"
        "2. **اختيار الرغبات:** يختار الطالب حتى عشرة رغبات مرتبة حسب الأولوية.\n"
        "3. **التوجيه الأولي:** يصدر تبعًا لمعدل الطالب ومعايير القبول الخاصة بكل تخصص.\n"
        "4. **الاستئناف:** يمكن للطالب تقديم طعن إذا لم يقتنع بالتوجيه الأول.\n"
        "5. **التسجيل النهائي:** يتم حضوريًا في الجامعة المعنية بعد التأكد من التوجيه، بإحضار الوثائق المطلوبة "
        "(شهادة البكالوريا، كشف النقاط، بطاقة التعريف، صور شمسية...).\n\n"
        "ينصح بمتابعة الموقع الرسمي للديوان الوطني للتعليم والتكوين عن بعد لمعرفة التواريخ الدقيقة كل سنة."
    )


def explain_grandes_ecoles() -> str:
    return (
        "### 🏛️ المدارس العليا والوطنية في الجزائر\n\n"
        "توجد إلى جانب الجامعات الكلاسيكية مدارس عليا وطنية تتطلب مسابقة دخول خاصة بعد الباكالوريا مباشرة، من أبرزها:\n"
        "- المدرسة الوطنية المتعددة التقنيات (ENP الجزائر)\n"
        "- المدرسة الوطنية العليا للإعلام الآلي (ESI - سابقًا INI)\n"
        "- المدرسة الوطنية العليا للتسيير والاقتصاد التطبيقي (ENSSEA)\n"
        "- المدرسة العليا للتجارة (ESC)\n"
        "- المدرسة الوطنية العليا للأشغال العمومية (ENSTP)\n\n"
        "تتميز هذه المدارس بتكوين مكثف، أطر تربوية متخصصة، وغالبًا فرص توظيف أفضل، لكنها تتطلب معدلات مرتفعة "
        "واجتياز مسابقة وطنية تُنظَّم بعد نتائج البكالوريا مباشرة."
    )


def explain_scholarship_housing() -> str:
    return (
        "### 🏠 المنحة الجامعية والإيواء\n\n"
        "**المنحة الجامعية:** تُمنح لجميع الطلبة المسجلين رسميًا في التعليم العالي، وتُدفع شهريًا عبر البريد "
        "أو حساب بنكي (CCP)، وتختلف قيمتها حسب الطور (ليسانس، ماستر، دكتوراه).\n\n"
        "**الإيواء الجامعي:** يُطلب عبر ديوان الخدمات الجامعية (ONOU) بعد التسجيل النهائي، ويُمنح بالأولوية "
        "للطلبة القادمين من ولايات بعيدة عن مقر الجامعة أو ذوي الوضعية الاجتماعية الخاصة.\n\n"
        "**الإطعام الجامعي:** يستفيد الطلبة من وجبات مدعمة في المطاعم الجامعية ببطاقة الإطعام."
    )


def explain_career_opportunities() -> str:
    return (
        "### 💼 فرص العمل بعد التخرج\n\n"
        "تختلف فرص العمل حسب التخصص، لكن بشكل عام يمكن للخريج الجزائري التوجه نحو:\n"
        "- **الوظيفة العمومية:** عبر مسابقات التوظيف في القطاعات الحكومية.\n"
        "- **القطاع الخاص:** الشركات المحلية والدولية العاملة في الجزائر.\n"
        "- **العمل الحر:** فتح مكتب أو عيادة أو شركة ناشئة (Startup) حسب التخصص.\n"
        "- **مواصلة الدراسات العليا:** ماستر ثم دكتوراه للعمل في التدريس والبحث العلمي.\n\n"
        "اذكر لي اسم تخصص محدد إن أردت تفاصيل دقيقة حول فرص العمل الخاصة به."
    )


def explain_vocational_training() -> str:
    return (
        "### 🛠️ التكوين المهني (كبديل أو تكميل للجامعة)\n\n"
        "يقدّم قطاع التكوين والتعليم المهنيين (عبر معاهد ومراكز CFPA/INSFP) تخصصات عملية "
        "في مدد قصيرة (من 6 أشهر إلى 3 سنوات)، من أبرزها:\n"
        "- تقني سامي في الإعلام الآلي، الكهرباء، الميكانيك، البناء، المحاسبة...\n"
        "- شهادة التكوين المتخصص (CFC)، شهادة التقني (CT)، شهادة التقني السامي (CTS)، وشهادة الكفاءة المهنية (CAP).\n\n"
        "**متى يكون خيارًا جيدًا؟** إذا كان المعدل لا يسمح بالولوج لتخصص جامعي مرغوب، أو إذا كنت تفضل "
        "دخول سوق العمل بسرعة بمهارة عملية مباشرة. كما يمكن لاحقًا مواصلة الدراسة الجامعية عبر جسور معينة."
    )


def explain_private_universities() -> str:
    return (
        "### 🏫 التعليم العالي الخاص في الجزائر\n\n"
        "توجد مؤسسات جامعية خاصة معتمدة من وزارة التعليم العالي، تقدّم تخصصات مثل الإعلام الآلي، "
        "التسيير، والعلوم المالية والمحاسبية، عادة بمرونة أكبر في التوقيت وأحجام أفواج أصغر، لكن بمقابل مالي (رسوم تسجيل سنوية).\n\n"
        "**قبل التسجيل تأكد من:**\n"
        "- أن المؤسسة معتمدة رسميًا (القائمة متوفرة في موقع وزارة التعليم العالي).\n"
        "- الشهادة المُسلَّمة معترف بها في نظام LMD.\n"
        "- تكلفة الدراسة الإجمالية طيلة السنوات ومدى توفر عروض عمل بعد التخرج."
    )


def explain_reorientation() -> str:
    return (
        "### 🔄 إعادة التوجيه الجامعي\n\n"
        "إذا لم يعجبك التخصص الذي وُجّهت إليه بعد البكالوريا، هناك عدة مسارات ممكنة:\n"
        "- **إعادة التوجيه في بداية السنة الجامعية:** بعض الجامعات تفتح نافذة لتغيير التخصص خلال الأسابيع الأولى.\n"
        "- **الانتقال بعد السنة الأولى المشتركة (جذع مشترك):** في تخصصات مثل العلوم والتكنولوجيا أو العلوم الاقتصادية.\n"
        "- **إعادة اجتياز التوجيه الأولي عبر منصة progres.mesrs.dz** في حالات استثنائية.\n\n"
        "ينصح دائمًا بالتواصل مع مصلحة التوجيه في كليتك لمعرفة الإجراء الدقيق المتاح في تخصصك."
    )


def explain_study_abroad() -> str:
    return (
        "### 🌍 الدراسة في الخارج والمنح الدولية\n\n"
        "بعض المسارات المتاحة للطلبة الجزائريين الراغبين في الدراسة خارج الجزائر:\n"
        "- **المنح الحكومية الجزائرية:** تُمنح سنويًا لبعض التخصصات النادرة (طب متخصص، هندسة دقيقة) عبر وزارة التعليم العالي.\n"
        "- **المنح الأجنبية:** مثل Erasmus+ (أوروبا)، والمنح الفرنسية (Campus France)، والمنح الكندية والتركية والصينية.\n"
        "- **التسجيل الذاتي:** عبر التقديم المباشر لجامعات أجنبية مع اجتياز اختبارات اللغة (TOEFL/IELTS/TCF/DELF).\n\n"
        "يُنصح بالتحضير المبكر (سنة واحدة على الأقل قبل التقديم) لتجهيز الملف واللغة والشهادات المطلوبة."
    )


def explain_cpge() -> str:
    return (
        "### 📐 الأقسام التحضيرية (CPGE / التحضيري المشترك للمدارس العليا)\n\n"
        "بديل عن الجامعة الكلاسيكية لمن يريد الالتحاق بالمدارس الوطنية العليا (ENP، ESI، ENSTP...). "
        "تدوم سنتين مكثفتين في الرياضيات والفيزياء (أو الاقتصاد لبعض المدارس)، تليها مسابقة وطنية "
        "للدخول إلى المدرسة العليا المرغوبة حسب الترتيب والرغبات.\n\n"
        "**يتطلب:** معدل بكالوريا مرتفع (غالبًا فوق 14) في شعبة رياضيات أو تقني رياضي، ومستوى عمل جدي جدًا "
        "لأن البرنامج مكثف جدًا (يُشبَّه أحيانًا بأصعب مرحلتين دراسيتين في المسار الجامعي)."
    )


def general_answer(message: str) -> str:
    """رد افتراضي عام حين لا يُكتشف قصد محدد."""
    return (
        "يمكنني مساعدتك في:\n"
        "- اقتراح تخصصات مناسبة (مثال: «أنا شعبة رياضيات ومعدلي 15»)\n"
        "- مقارنة بين تخصصين (مثال: «قارن بين الطب والصيدلة»)\n"
        "- شرح تخصص معيّن (مثال: «أخبرني عن الحقوق»)\n"
        "- شرح نظام LMD، التسجيل الجامعي، المدارس العليا، أو المنحة والإيواء\n"
        "- التكوين المهني، الجامعات الخاصة، إعادة التوجيه، الأقسام التحضيرية، أو الدراسة والمنح بالخارج\n\n"
        "أعد صياغة سؤالك بشكل أوضح وسأساعدك فورًا 😊"
    )


def greeting_answer() -> str:
    return (
        "أهلاً بك! 👋 أنا مساعدك الذكي للتوجيه الجامعي في الجزائر. "
        "أخبرني عن شعبتك ومعدلك، أو اسألني عن أي تخصص أو إجراء جامعي وسأساعدك فورًا 😊"
    )


def thanks_answer() -> str:
    return "على الرحب والسعة! 🌟 أتمنى لك التوفيق في مسارك الجامعي. لا تتردد في طرح أي سؤال آخر."


# ---------------------------------------------------------------------------
# محرك تحديد النية (Intent Router) والدالة الموحدة للردّ المحلي
# ---------------------------------------------------------------------------

LMD_KEYWORDS = ["lmd", "نظام lmd", "ليسانس ماستر دكتوراه", "نظام الليسانس"]
REGISTRATION_KEYWORDS = ["تسجيل", "نسجل", "التسجيلات", "رغبات", "توجيه أولي", "كيفاش نسجل"]
GRANDES_ECOLES_KEYWORDS = ["مدرسة عليا", "مدارس عليا", "المدارس العليا", "مدرسة وطنية", "esi", "enp", "ensea"]
SCHOLARSHIP_KEYWORDS = ["منحة", "الإيواء", "إيواء", "الإطعام", "المطعم الجامعي"]
CAREER_KEYWORDS = ["فرص العمل", "العمل بعد التخرج", "التوظيف", "سوق العمل"]
COMPARE_KEYWORDS = ["قارن", "مقارنة", "الفرق بين", "أيهما أفضل"]
VOCATIONAL_KEYWORDS = ["تكوين مهني", "التكوين المهني", "insfp", "cfpa", "تقني سامي"]
PRIVATE_UNI_KEYWORDS = ["جامعة خاصة", "جامعات خاصة", "مدرسة خاصة", "تعليم خاص"]
REORIENTATION_KEYWORDS = ["إعادة التوجيه", "تغيير التخصص", "تغيير تخصص", "ما عجبنيش", "نبدل التخصص", "نبدل تخصص", "نبدل التوجيه"]
STUDY_ABROAD_KEYWORDS = ["دراسة بالخارج", "الدراسة في الخارج", "منحة دراسية بالخارج", "erasmus", "campus france",
                          "دراسة في فرنسا", "دراسة في كندا", "ندرس في فرنسا", "ندرس في الخارج", "ندرس برا"]
CPGE_KEYWORDS = ["تحضيري", "الأقسام التحضيرية", "cpge", "قسم تحضيري"]
GREETING_KEYWORDS = ["سلام", "السلام عليكم", "أهلا", "مرحبا", "salut", "bonjour", "hello", "hi "]
THANKS_KEYWORDS = ["شكرا", "شكراً", "يعطيك الصحة", "merci", "thank you", "thanks"]


def local_generate_response(message: str) -> str:
    """
    المحرك المحلي القائم على القواعد (Rule-Based Engine).
    يحلل رسالة المستخدم ويحدد نيته، ثم يبني الرد المناسب اعتمادًا على majors.json.
    """
    text = message.strip()
    lower_text = text.lower()  # يُطبَّق فقط على الأحرف اللاتينية (مثل LMD)، ولا يؤثر على العربية

    # 0) تحية أو شكر (يُفحص أولاً وفقط إذا كانت الرسالة قصيرة لتجنب تعارضها مع أسئلة حقيقية)
    if len(text.split()) <= 4:
        if any(kw in lower_text for kw in THANKS_KEYWORDS):
            return thanks_answer()
        if any(kw in lower_text for kw in GREETING_KEYWORDS):
            return greeting_answer()

    # 1) طلب مقارنة صريح
    if any(kw in lower_text for kw in COMPARE_KEYWORDS):
        found = find_two_majors(text)
        if len(found) == 2:
            return compare_majors(found[0], found[1])
        return "حدد لي اسمي تخصصين بوضوح حتى أقارن بينهما، مثال: «قارن بين الإعلام الآلي والطب»."

    # 2) نظام LMD
    if any(kw in lower_text for kw in LMD_KEYWORDS):
        return explain_lmd()

    # 2.5) نظام مهندس دولة (يُفحص قبل LMD لأن بعض الكلمات قد تتداخل)
if any(kw in lower_text for kw in ENGINEER_STATE_KEYWORDS):
    
    # 3) الأقسام التحضيرية (يُفحص قبل التسجيل لأن بعض الكلمات قد تتشابه)
    if any(kw in lower_text for kw in CPGE_KEYWORDS):
        return explain_cpge()

    # 4) إعادة التوجيه
    if any(kw in lower_text for kw in REORIENTATION_KEYWORDS):
        return explain_reorientation()

    # 5) التسجيل الجامعي
    if any(kw in lower_text for kw in REGISTRATION_KEYWORDS):
        return explain_registration()

    # 6) المدارس العليا
    if any(kw in lower_text for kw in GRANDES_ECOLES_KEYWORDS):
        return explain_grandes_ecoles()

    # 7) التكوين المهني
    if any(kw in lower_text for kw in VOCATIONAL_KEYWORDS):
        return explain_vocational_training()

    # 8) الجامعات الخاصة
    if any(kw in lower_text for kw in PRIVATE_UNI_KEYWORDS):
        return explain_private_universities()

    # 9) الدراسة بالخارج والمنح الدولية
    if any(kw in lower_text for kw in STUDY_ABROAD_KEYWORDS):
        return explain_study_abroad()

    # 10) المنحة والإيواء
    if any(kw in lower_text for kw in SCHOLARSHIP_KEYWORDS):
        return explain_scholarship_housing()

    # 11) فرص العمل العامة (دون ذكر تخصص محدد)
    major_match = find_major_by_keyword(text)
    if any(kw in lower_text for kw in CAREER_KEYWORDS) and not major_match:
        return explain_career_opportunities()

    # 12) إذا ذُكر اسم تخصص بوضوح تام، نعطيه الأولوية على اقتراح الشعبة العامة
    #     (يتجنب هذا أن تُطغى كلمة عامة مثل «علوم» الموجودة في اسم شعبة على تخصص محدد ذُكر صراحة)
    if major_match and any(major_match["name"] in text for _ in [0]):
        return explain_major(major_match)

    # 13) اقتراح تخصصات حسب الشعبة والمعدل
    stream = detect_stream(text)
    if stream:
        average = detect_average(text)
        return suggest_majors(stream, average)

    # 14) شرح تخصص محدد (تم اكتشافه عبر الكلمات المفتاحية فقط)
    if major_match:
        return explain_major(major_match)

    # 15) رد افتراضي
    return general_answer(text)


def openai_generate_response(message: str) -> str:
    """
    يستدعي OpenAI API لتوليد رد ذكي، مع تزويد النموذج بسياق مختصر عن قاعدة
    التخصصات لتحسين دقة الإجابة. يُستخدم فقط إذا كان OPENAI_API_KEY متوفرًا.
    """
    try:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)
        majors_context = "\n".join(f"- {m['name']}: {m['description']}" for m in MAJORS)

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + "\n\nقاعدة بيانات التخصصات المتاحة:\n" + majors_context},
                {"role": "user", "content": message},
            ],
            temperature=0.4,
            max_tokens=800,
        )
        return completion.choices[0].message.content
    except Exception as exc:  # في حال فشل الاتصال بـ OpenAI نعود للمحرك المحلي
        return local_generate_response(message) + f"\n\n_(تنبيه: تعذر الاتصال بـ OpenAI API، تم استخدام المحرك المحلي. الخطأ: {exc})_"


def generate_response(message: str) -> str:
    """
    نقطة الدخول الموحدة لتوليد الرد.
    - إن وُجد OPENAI_API_KEY صالح: يُستخدم OpenAI API.
    - وإلا: يُستخدم المحرك المحلي القائم على majors.json.
    """
    if OPENAI_API_KEY:
        return openai_generate_response(message)
    return local_generate_response(message)


# ---------------------------------------------------------------------------
# نماذج البيانات (Pydantic Models)
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


# ---------------------------------------------------------------------------
# المسارات (Routes)
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """الصفحة الرئيسية للواجهة."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest):
    """نقطة نهاية المحادثة: تستقبل رسالة المستخدم وتعيد رد المساعد."""
    message = payload.message.strip()
    if not message:
        return JSONResponse({"reply": "الرجاء كتابة سؤال أولاً 🙂"}, status_code=400)
    reply = generate_response(message)
    return {"reply": reply}


@app.get("/api/majors")
async def get_majors():
    """يعيد قائمة كاملة بجميع التخصصات (يمكن استخدامها لاحقًا في الواجهة)."""
    return MAJORS


@app.get("/api/health")
async def health_check():
    """فحص سريع لحالة الخادم."""
    return {"status": "ok", "majors_loaded": len(MAJORS), "openai_enabled": bool(OPENAI_API_KEY)}
