/* Вход и привязка через Telegram: опрос подтверждения и автозакрытие вкладки t.me.

   Ссылку открываем сами через window.open, чтобы сохранить ссылку на вкладку
   и закрыть её, когда бот подтвердит. Без скрипта ссылка работает обычным
   образом (target="_blank" rel="noopener"), только вкладка останется висеть. */
(function () {
  "use strict";

  var root = document.querySelector("[data-tg-code]");
  if (!root) return;

  var code = root.getAttribute("data-tg-code");
  var statusEl = document.getElementById("tg-status");
  var expired = root.getAttribute("data-tg-expired") || "Код истёк — обнови страницу.";
  var opened = null;
  var tries = 0;

  var link = document.querySelector("[data-tg-link]");
  if (link) {
    link.addEventListener("click", function (event) {
      // Без noopener, иначе window.open вернёт null и закрывать будет нечего.
      var win = window.open(link.href, "_blank");
      if (win) {
        opened = win;
        event.preventDefault();
      }
    });
  }

  function closeOpened() {
    try {
      if (opened && !opened.closed) opened.close();
    } catch (e) {
      /* вкладку мог закрыть сам пользователь */
    }
  }

  function stop(timer, message) {
    clearInterval(timer);
    if (statusEl) statusEl.textContent = message;
  }

  var timer = setInterval(async function () {
    if (++tries > 150) {
      stop(timer, expired);
      return;
    }
    try {
      var response = await fetch("/login/status/" + encodeURIComponent(code));
      var data = await response.json();
      if (data.state === "confirmed") {
        clearInterval(timer);
        closeOpened();
        window.location = data.next;
      } else if (data.state === "expired" || data.state === "unknown") {
        stop(timer, expired);
      }
    } catch (e) {
      /* сеть моргнула — попробуем на следующем тике */
    }
  }, 2000);
})();
