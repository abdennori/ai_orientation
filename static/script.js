/* =====================================================================
   AI_Orientation — منطق الواجهة الأمامية
   يتعامل مع: إرسال الرسائل عبر Fetch API (بدون إعادة تحميل الصفحة)،
   عرض الرسائل، مؤشر التحميل، مسح المحادثة، وتبديل الوضع الليلي/الفاتح.
   ===================================================================== */

(function () {
  "use strict";

  const chatWindow = document.getElementById("chat-window");
  const welcomeScreen = document.getElementById("welcome-screen");
  const messageInput = document.getElementById("message-input");
  const sendBtn = document.getElementById("send-btn");
  const typingIndicator = document.getElementById("typing-indicator");
  const themeToggle = document.getElementById("theme-toggle");
  const clearChatBtn = document.getElementById("clear-chat");
  const chips = document.querySelectorAll(".chip");

  const STORAGE_THEME_KEY = "ai_orientation_theme";
  let conversationStarted = false;

  // -------------------- الوضع الليلي / الفاتح --------------------
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(STORAGE_THEME_KEY, theme);
  }

  (function initTheme() {
    const saved = localStorage.getItem(STORAGE_THEME_KEY);
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(saved || (prefersDark ? "dark" : "light"));
  })();

  themeToggle.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    applyTheme(current === "dark" ? "light" : "dark");
  });

  // -------------------- أدوات مساعدة --------------------
  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function renderMarkdown(text) {
    if (window.marked) {
      return window.marked.parse(text);
    }
    // نسخة احتياطية بسيطة إن تعذر تحميل مكتبة marked
    return escapeHtml(text).replace(/\n/g, "<br>");
  }

  function scrollToBottom() {
    chatWindow.scrollTop = chatWindow.scrollHeight;
  }

  function autoResizeTextarea() {
    messageInput.style.height = "auto";
    messageInput.style.height = Math.min(messageInput.scrollHeight, 140) + "px";
  }

  // -------------------- إضافة رسالة إلى الواجهة --------------------
  function appendMessage(role, content, isMarkdown) {
    if (!conversationStarted) {
      welcomeScreen.classList.add("hidden");
      conversationStarted = true;
    }

    const row = document.createElement("div");
    row.className = "msg-row " + role;

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = role === "user" ? "أنت" : "◈";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.innerHTML = isMarkdown ? renderMarkdown(content) : escapeHtml(content);

    row.appendChild(avatar);
    row.appendChild(bubble);
    chatWindow.appendChild(row);
    scrollToBottom();
  }

  function setLoading(isLoading) {
    typingIndicator.classList.toggle("hidden", !isLoading);
    sendBtn.disabled = isLoading;
    if (isLoading) scrollToBottom();
  }

  // -------------------- إرسال رسالة إلى الخادم --------------------
  async function sendMessage(text) {
    const message = text.trim();
    if (!message) return;

    appendMessage("user", message, false);
    messageInput.value = "";
    autoResizeTextarea();
    setLoading(true);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });

      const data = await response.json();

      if (!response.ok) {
        appendMessage("assistant", data.reply || "حدث خطأ غير متوقع، حاول مجددًا.", false);
      } else {
        appendMessage("assistant", data.reply, true);
      }
    } catch (err) {
      appendMessage(
        "assistant",
        "تعذر الاتصال بالخادم. تأكد من تشغيل التطبيق عبر uvicorn وحاول مرة أخرى.",
        false
      );
    } finally {
      setLoading(false);
    }
  }

  // -------------------- أحداث الواجهة --------------------
  sendBtn.addEventListener("click", () => sendMessage(messageInput.value));

  messageInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(messageInput.value);
    }
  });

  messageInput.addEventListener("input", autoResizeTextarea);

  chips.forEach((chip) => {
    chip.addEventListener("click", () => sendMessage(chip.dataset.msg));
  });

  clearChatBtn.addEventListener("click", () => {
    chatWindow.innerHTML = "";
    conversationStarted = false;
    welcomeScreen.classList.remove("hidden");
  });
})();
