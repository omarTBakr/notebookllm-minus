// UI labels and direction. Model-facing prompts live server-side in
// templates/locales/<lang>/ — these are only the words on the buttons.

export const SUPPORTED = ["en", "ar"];

const STRINGS = {
  en: {
    tagline: "Ask your documents",
    user: "User",
    newUser: "New user",
    forgetUser: "Forget",
    sessions: "Sessions",
    noSessions: "No sessions yet.",
    newSession: "Session",
    newChat: "New chat",
    noChat: "No chat selected",
    emptyTitle: "Start a conversation",
    emptyBody: "Ask anything. Attach a document and answers will be grounded in it, with sources.",
    askPlaceholder: "Ask a question…",
    send: "Send",
    hint: "Attach a .pdf or .txt to ground the answers. Enter to send, Shift+Enter for a new line.",
    grounded: "Grounded",
    sources: "Sources",
    thinking: "Thinking…",
    chatModel: "Chat model",
    embedModel: "Embedding model",
    reindexing: "Re-indexing documents…",
    reindexed: "Re-indexed",
    chunks: "chunks",
    modelSaved: "Saved",
    thoughtFor: "Thought for",
    chars: "characters",
    indexing: "Indexing…",
    you: "You",
    assistant: "Assistant",
    pickChatFirst: "Create or select a chat first.",
    ungrounded: "No documents attached — answered from general knowledge.",
  },
  ar: {
    tagline: "اسأل مستنداتك",
    user: "المستخدم",
    newUser: "مستخدم جديد",
    forgetUser: "نسيان",
    sessions: "الجلسات",
    noSessions: "لا توجد جلسات بعد.",
    newSession: "جلسة",
    newChat: "محادثة جديدة",
    noChat: "لم يتم اختيار محادثة",
    emptyTitle: "ابدأ محادثة",
    emptyBody: "اسأل عن أي شيء. أرفق مستندًا لتصبح الإجابات مستندة إليه مع ذكر المصادر.",
    askPlaceholder: "اطرح سؤالاً…",
    send: "إرسال",
    hint: "أرفق ملف ‎.pdf أو ‎.txt لتستند الإجابات إليه. Enter للإرسال، Shift+Enter لسطر جديد.",
    grounded: "مستند إلى المصادر",
    sources: "المصادر",
    thinking: "جارٍ التفكير…",
    chatModel: "نموذج المحادثة",
    embedModel: "نموذج التضمين",
    reindexing: "جارٍ إعادة فهرسة المستندات…",
    reindexed: "أُعيدت الفهرسة",
    chunks: "مقطعًا",
    modelSaved: "تم الحفظ",
    thoughtFor: "فكّر بمقدار",
    chars: "حرفًا",
    indexing: "جارٍ الفهرسة…",
    you: "أنت",
    assistant: "المساعد",
    pickChatFirst: "أنشئ محادثة أو اخترها أولاً.",
    ungrounded: "لا توجد مستندات مرفقة — الإجابة من المعرفة العامة.",
  },
};

let current = "en";

export const t = (key) => STRINGS[current]?.[key] ?? STRINGS.en[key] ?? key;

export const currentLang = () => current;

export function applyLang(lang) {
  current = SUPPORTED.includes(lang) ? lang : "en";

  const root = document.documentElement;
  root.lang = current;
  // The single line that mirrors the whole layout: every rule uses logical
  // properties, so nothing else has to change.
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
