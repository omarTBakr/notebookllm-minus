// UI labels and text direction. Model-facing prompts live server-side in
// templates/locales/<lang>/ — these are only the words on the controls.

export const SUPPORTED = ["en", "ar"];

const STRINGS = {
  en: {
    // top bar
    untitled: "Untitled notebook",
    createNotebook: "Create notebook",
    notebooks: "Notebooks",
    copy: "Copy",
    share: "Share",
    appearance: "Appearance",
    chatSettings: "Chat settings",
    profiles: "Profiles",
    newProfile: "New profile",
    deleteProfile: "Delete profile",
    confirmDeleteProfile:
      "Delete “{name}” and everything in it? Its notebooks, documents and answers all go, and this cannot be undone.",
    profileDeleted: "Deleted “{name}” and everything in it.",
    rename: "Rename",
    cancel: "Cancel",
    confirm: "Confirm",
    save: "Save",
    renamePrompt: "Name this profile:",
    renameNotebook: "Rename this notebook:",
    noNotebooks: "No notebooks yet.",

    // panels
    hidePanel: "Hide",
    showPanel: "Show",
    panelLast: "At least one panel has to stay open.",

    // sources
    sources: "Sources",
    addSources: "Add sources",
    searchWeb: "Search the web for new sources",
    web: "Web",
    fastResearch: "Fast Research",
    selectAll: "Select all",
    sourcesEmptyTitle: "Saved sources will appear here",
    sourcesEmptyBody:
      "Add files, then ask questions and get answers grounded in them. Drop a PDF or a text file to begin.",
    indexing: "Indexing",
    loading: "Loading…",
    deleteSource: "Delete",
    downloadSource: "Download",
    confirmDeleteSource: "Delete “{name}”? Its text and embeddings go too.",
    sourceDeleted: "Deleted “{name}”.",
    // The stages of attaching a document, shown on its progress bar.
    stage_extracting: "Reading",
    stage_chunking: "Splitting",
    stage_storing: "Saving",
    stage_indexing: "Indexing",
    // Fallback only: the server normally supplies the failure's own message.
    uploadFailed: "Ingestion failed",

    // chat
    chat: "Chat",
    customize: "Customize",
    startTyping: "Start typing…",
    source: "source",
    sourcesCount: "sources",
    you: "You",
    assistant: "Assistant",
    thinking: "Thinking…",
    thoughtFor: "Thought for",
    chars: "characters",
    citations: "Sources",
    // Abbreviated because it sits inline in a citation, after the filename.
    page: "p.",
    openAtPage: "Open the source at this page",
    openSource: "Open the source",
    // answer actions
    saveToSources: "📌 Save to sources",
    copyAnswer: "⧉ Copy",
    download: "⤓ Download",
    helpful: "👍",
    notHelpful: "👎",
    nothingToSave: "There is nothing to save yet.",
    copyUnavailable: "Copying needs a secure connection (https or localhost).",
    stop: "Stop generating",
    stopped: "Stopped.",
    sourceGone: "That source is no longer in this notebook.",
    ungrounded: "No sources attached — answered from general knowledge.",
    noNotebookYet: "Create a notebook to begin.",
    disclaimer: "Answers can be inaccurate; check them against the sources.",

    // studio
    studio: "Studio",
    audioOverview: "Audio Overview",
    slideDeck: "Slide Deck",
    videoOverview: "Video Overview",
    mindMap: "Mind Map",
    reports: "Reports",
    flashcards: "Flashcards",
    quiz: "Quiz",
    infographic: "Infographic",
    dataTable: "Data Table",
    studioEmptyTitle: "Studio output will be saved here.",
    studioEmptyBody:
      "After adding sources, generate an overview, a mind map, flashcards and more.",
    addNote: "Add note",
    saveNote: "Save note",
    discard: "Discard",
    noteTitle: "Note title",
    notePlaceholder: "Type or paste anything. Saving turns it into a source.",
    noteEmpty: "Write something first.",
    noteSaved: "Note saved as a source.",

    // settings
    chatModel: "Chat model",
    embedModel: "Embedding model",
    embedHint: "Changing this re-indexes this notebook's sources.",
    ollama: "Ollama",
    modelLocal: "Local",
    modelWeb: "Web",
    modelNvidia: "NVIDIA",
    modelAnthropic: "Anthropic",
    modelGoogle: "Google",
    capabilityText: "Text",
    capabilityEmbedding: "Embedding",
    capabilityImage: "Image",
    capabilityTools: "Tools",
    capabilityThinking: "Thinking",
    sizeSmall: "under 8B",
    sizeMedium: "8B – 30B",
    sizeLarge: "over 30B",
    sizeUnknown: "size unknown",
    modelMissing: "Missing",
    modelLoading: "Loading…",
    // Fallback only: the backend normally supplies the specific reason
    // ("No API credit", "Retired by the vendor") as the badge text.
    modelUnavailable: "Unavailable",
    temperature: "Temperature",
    temperatureHint: "Lower is more literal, higher more inventive.",
    outputLength: "Output length",
    webSearch: "Ground with web search",
    highlightColor: "Highlight color",
    highlightColorHint: "The color used to highlight a cited passage in the source.",
    advanced: "Advanced",
    chunkSize: "Chunk size",
    overlapSize: "Overlap",
    advancedHint: "Chunk settings apply to sources added after the change.",
    theme: "Appearance",
    themeLight: "Light",
    themeDark: "Dark",
    themeSystem: "System",
    language: "Language",
    saved: "Saved",
    reindexed: "Re-indexed",
    chunks: "chunks",

    copied: "Copied",

    comingSoon: "coming soon",
    soon: "Soon",
  },

  ar: {
    untitled: "دفتر بلا عنوان",
    createNotebook: "دفتر جديد",
    notebooks: "الدفاتر",
    copy: "نسخ",
    share: "مشاركة",
    appearance: "المظهر",
    chatSettings: "إعدادات المحادثة",
    profiles: "الملفات الشخصية",
    newProfile: "ملف جديد",
    deleteProfile: "حذف الملف",
    confirmDeleteProfile:
      "حذف «{name}» وكل ما فيه؟ ستُحذف دفاتره ومستنداته وإجاباته، ولا يمكن التراجع.",
    profileDeleted: "حُذف «{name}» وكل ما فيه.",
    rename: "إعادة تسمية",
    cancel: "إلغاء",
    confirm: "تأكيد",
    save: "حفظ",
    renamePrompt: "اسم هذا الملف:",
    renameNotebook: "اسم هذا الدفتر:",
    noNotebooks: "لا توجد دفاتر بعد.",

    hidePanel: "إخفاء",
    showPanel: "إظهار",
    panelLast: "يجب أن تبقى لوحة واحدة على الأقل مفتوحة.",

    sources: "المصادر",
    addSources: "إضافة مصادر",
    searchWeb: "ابحث في الويب عن مصادر جديدة",
    web: "الويب",
    fastResearch: "بحث سريع",
    selectAll: "تحديد الكل",
    sourcesEmptyTitle: "ستظهر المصادر المحفوظة هنا",
    sourcesEmptyBody:
      "أضف ملفات، ثم اطرح أسئلة واحصل على إجابات مستندة إليها. أفلت ملف PDF أو نصًا للبدء.",
    indexing: "جارٍ الفهرسة",
    loading: "جارٍ التحميل…",
    deleteSource: "حذف",
    downloadSource: "تنزيل",
    confirmDeleteSource: "حذف «{name}»؟ سيُحذف نصه وتضميناته أيضًا.",
    sourceDeleted: "حُذف «{name}».",
    stage_extracting: "جارٍ القراءة",
    stage_chunking: "جارٍ التقطيع",
    stage_storing: "جارٍ الحفظ",
    stage_indexing: "جارٍ الفهرسة",
    uploadFailed: "فشلت المعالجة",

    chat: "المحادثة",
    customize: "تخصيص",
    startTyping: "اكتب هنا…",
    source: "مصدر",
    sourcesCount: "مصادر",
    you: "أنت",
    assistant: "المساعد",
    thinking: "جارٍ التفكير…",
    thoughtFor: "فكّر بمقدار",
    chars: "حرفًا",
    citations: "المصادر",
    page: "ص.",
    openAtPage: "افتح المصدر عند هذه الصفحة",
    openSource: "افتح المصدر",
    saveToSources: "📌 حفظ في المصادر",
    copyAnswer: "⧉ نسخ",
    download: "⤓ تنزيل",
    helpful: "👍",
    notHelpful: "👎",
    nothingToSave: "لا يوجد ما يُحفظ بعد.",
    copyUnavailable: "النسخ يحتاج اتصالًا آمنًا (https أو localhost).",
    stop: "إيقاف التوليد",
    stopped: "تم الإيقاف.",
    sourceGone: "لم يعد هذا المصدر موجودًا في هذا الدفتر.",
    ungrounded: "لا توجد مصادر مرفقة — الإجابة من المعرفة العامة.",
    noNotebookYet: "أنشئ دفترًا للبدء.",
    disclaimer: "قد تكون الإجابات غير دقيقة؛ راجعها مقابل المصادر.",

    studio: "الاستوديو",
    audioOverview: "ملخص صوتي",
    slideDeck: "عرض تقديمي",
    videoOverview: "ملخص مرئي",
    mindMap: "خريطة ذهنية",
    reports: "تقارير",
    flashcards: "بطاقات",
    quiz: "اختبار",
    infographic: "إنفوجرافيك",
    dataTable: "جدول بيانات",
    studioEmptyTitle: "ستُحفظ مخرجات الاستوديو هنا.",
    studioEmptyBody: "بعد إضافة المصادر، أنشئ ملخصًا وخريطة ذهنية وبطاقات وغيرها.",
    addNote: "إضافة ملاحظة",
    saveNote: "حفظ الملاحظة",
    discard: "تجاهل",
    noteTitle: "عنوان الملاحظة",
    notePlaceholder: "اكتب أو ألصق أي شيء. الحفظ يحوّله إلى مصدر.",
    noteEmpty: "اكتب شيئًا أولًا.",
    noteSaved: "حُفظت الملاحظة كمصدر.",

    chatModel: "نموذج المحادثة",
    embedModel: "نموذج التضمين",
    embedHint: "تغييره يعيد فهرسة مصادر هذا الدفتر.",
    ollama: "أولاما",
    modelLocal: "محلي",
    modelWeb: "الويب",
    modelNvidia: "إنفيديا",
    modelAnthropic: "أنثروبيك",
    modelGoogle: "جوجل",
    capabilityText: "نص",
    capabilityEmbedding: "تضمين",
    capabilityImage: "صورة",
    capabilityTools: "أدوات",
    capabilityThinking: "تفكير",
    sizeSmall: "أقل من 8B",
    sizeMedium: "‏8B – 30B",
    sizeLarge: "أكثر من 30B",
    sizeUnknown: "حجم غير معروف",
    modelMissing: "غير متوفر",
    modelLoading: "جارٍ التحميل…",
    modelUnavailable: "غير قابل للاستخدام",
    temperature: "درجة الإبداع",
    temperatureHint: "القيمة الأقل أكثر التزامًا، والأعلى أكثر ابتكارًا.",
    outputLength: "طول الإخراج",
    webSearch: "الاستناد إلى بحث الويب",
    highlightColor: "لون التظليل",
    highlightColorHint: "اللون المستخدم لتظليل المقطع المُقتبس داخل المصدر.",
    advanced: "إعدادات متقدمة",
    chunkSize: "حجم المقطع",
    overlapSize: "التداخل",
    advancedHint: "تُطبَّق إعدادات التقطيع على المصادر المضافة بعد التغيير.",
    theme: "المظهر",
    themeLight: "فاتح",
    themeDark: "داكن",
    themeSystem: "النظام",
    language: "اللغة",
    saved: "تم الحفظ",
    reindexed: "أُعيدت الفهرسة",
    chunks: "مقطعًا",

    copied: "تم النسخ",

    comingSoon: "قريبًا",
    soon: "قريبًا",
  },
};

let current = "en";

export const t = (key) => STRINGS[current]?.[key] ?? STRINGS.en[key] ?? key;
export const currentLang = () => current;

export function applyLang(lang) {
  current = SUPPORTED.includes(lang) ? lang : "en";

  const root = document.documentElement;
  root.lang = current;
  // The one line that flips the text. The layout does not move: every panel
  // sits in a grid pinned with direction:ltr (see layout.css).
  root.dir = current === "ar" ? "rtl" : "ltr";

  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });

  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });

  document.querySelectorAll(".btn--lang").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.lang === current);
  });

  localStorage.setItem("lang", current);
}
