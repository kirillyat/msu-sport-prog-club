/* Обратный отсчёт до старта соревнования.
   Элемент: <span data-countdown data-start="ISO" data-end="ISO">.
   Время приходит в UTC с сервером — Date.parse разберёт его с учётом зоны. */
(function () {
  "use strict";

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function pluralRu(count, one, few, many) {
    var tail = Math.abs(count) % 100;
    if (tail >= 11 && tail <= 14) return many;
    tail %= 10;
    if (tail === 1) return one;
    if (tail >= 2 && tail <= 4) return few;
    return many;
  }

  function setState(el, state, text) {
    el.textContent = text;
    if (el.dataset.state !== state) el.dataset.state = state;
  }

  function render(el) {
    var start = el.dataset.start ? Date.parse(el.dataset.start) : null;
    var end = el.dataset.end ? Date.parse(el.dataset.end) : null;
    var now = Date.now();
    var target, prefix, state;

    if (start && now < start) {
      target = start; prefix = "до старта"; state = "upcoming";
    } else if (end && now < end) {
      target = end; prefix = "до конца"; state = "live";
    } else if (start && !end && now - start < 86400000) {
      setState(el, "live", "идёт сейчас");
      return;
    } else {
      setState(el, "past", "завершено");
      return;
    }

    var left = Math.max(0, Math.floor((target - now) / 1000));
    var days = Math.floor(left / 86400);
    left -= days * 86400;
    var hours = Math.floor(left / 3600);
    var minutes = Math.floor((left % 3600) / 60);
    var seconds = left % 60;

    var daysPart = days ? days + " " + pluralRu(days, "день", "дня", "дней") + " " : "";
    setState(el, state, prefix + ": " + daysPart + pad(hours) + ":" + pad(minutes) + ":" + pad(seconds));
  }

  function tick() {
    var nodes = document.querySelectorAll("[data-countdown]");
    for (var i = 0; i < nodes.length; i++) render(nodes[i]);
  }

  tick();
  setInterval(tick, 1000);
})();
