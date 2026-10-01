"""Arabic prompts for repairing a page of extracted PDF text.

The rules are the same as ``prompts/en/ocr_correction.py`` -- read that file
first for why each prohibition exists. What differs here is that the faults are
*named and shown*, because Arabic breaks in ways English does not and the
abstract wording that works for English does not survive the trip.

Three faults, measured on a 222-page Arabic book whose text layer PyMuPDF reads
without losing a glyph:

  1. spaces appearing inside a word      لبنا ن       -> لبنان
  2. spaces vanishing between words      منقضا ء      -> من قضاء
  3. two letters swapping places         امل تن       -> المتن

The third is the one no amount of abstract instruction reaches. It comes from
the ligature decomposition: a joined pair is emitted in visual rather than
logical order, so ``لم`` arrives as ``مل``, ``ير`` as ``ري`` and ``ين`` as
``ني``. Told only that "text may be reversed", the model does not connect that
to ``كث ري`` and leaves it alone. Shown ``كث ري -> كثير``, it repairs the whole
class.

Measured on one page of that book, ten phrases whose correct form is not in
doubt, against a hosted model:

    raw extraction                    1/10 correct
    abstract rules, no examples       9/10 correct, 79.1s
    these examples                    9/10 correct, 12.0s

Same score, six times faster: the examples save the model from working the
pattern out for itself. Both figures are with the reasoning scratchpad turned
off -- see TextCorrectionService. With it on, this model answers in prose
and never emits JSON at all, which is worth more than any wording here.

The page markers stay in ASCII (``--- PAGE 3 ---``) on purpose: they are
delimiters, not prose, and keeping them identical across languages means one
parser and one numbering scheme serve both.
"""

correction_prompt = "\n".join(
    [
        "أنت ناسخ يُصلح نصًّا عربيًّا استُخرج آليًّا من ملف PDF.",
        "أعِد كتابة الصفحة كاملةً بعد إصلاح ثلاثة أعطال، لا رابع لها:",
        "",
        "(١) مسافات دخيلة داخل الكلمة الواحدة — احذفها:",
        "    «لبنا ن» ← «لبنان»",
        "    «ا لفحم ا لحجر ي» ← «الفحم الحجري»",
        "    «حمز ة» ← «حمزة»",
        "    «ا لأسو د» ← «الأسود»",
        "",
        "(٢) مسافات ناقصة بين كلمتين التصقتا — أضِفها:",
        "    «منقضا ء» ← «من قضاء»",
        "    «أ يض افينو ا حي» ← «أيضًا في نواحي»",
        "    «ا لرجمةمنقضا ء» ← «الرجمة من قضاء»",
        "    «لميطرقو ا» ← «لم يطرقوا»",
        "",
        "(٣) حرفان تبادلا موضعيهما عند فكّ الحروف المتّصلة — أعِدهما إلى ترتيبهما",
        "    الصحيح. النمط: «مل» تُقرأ «لم»، و«ري» تُقرأ «ير»، و«ني» تُقرأ «ين».",
        "    «امل تن» ← «المتن»",
        "    «امل عا دن» ← «المعادن»",
        "    «كث ري» ← «كثير»",
        "    «غ ريه» ← «غيره»",
        "    «ع ني» ← «عين»",
        "    «ا لخط رية» ← «الخطيرة»",
        "",
        "وهذه الأعطال وحدها. لا تفعل أيًّا ممّا يلي مهما بدت الصفحة داعية إليه:",
        "  - لا تُترجم. الصفحة العربيّة تعود عربيّة حرفًا بحرف.",
        "  - لا تُلخّص ولا تختصر ولا تحذف جملة. كلّ جملة في الصفحة ترد في إجابتك.",
        "  - لا تُجب عن النصّ ولا تشرحه ولا تعلّق عليه ولا تُكمله.",
        "  - لا تُضف ما ليس في الصفحة: لا عناوين ولا ملاحظات ولا تنسيق.",
        "  - لا تُصحّح الكاتب. اترك أخطاءه الإملائيّة وصيغه القديمة كما هي.",
        "    أنت تُزيل عطب الاستخراج لا خطأ المؤلّف.",
        "  - لا تحذف أرقام الصفحات ولا الترويسات ولا الحواشي ولا خلايا الجداول.",
        "",
        "إن عجزت عن قراءة كلمة بثقة فانسخها كما هي. الكلمة المشوَّهة المحفوظة",
        "يمكن إنقاذها لاحقًا، أمّا التخمين فلا.",
        "",
        "تلي الصفحات، كلٌّ تحت رقم. أعِد مدخلة واحدة لكلّ صفحة بالرقم نفسه الذي",
        "أُعطي لها هنا. هذا الرقم للطلب الحاليّ فقط، فتجاهل أيّ رقم صفحة مطبوع",
        "داخل النصّ.",
        "",
        "{pages}",
    ]
)


page_prompt = "\n".join(
    [
        "--- PAGE {num} ---",
        "{text}",
        "--- END PAGE {num} ---",
        "(أعِد ما سبق كاملًا بالعربيّة، مُصلحًا الأعطال الثلاثة وحدها.)",
    ]
)
