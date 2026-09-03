/* 策划页交互:板块过滤 + 勾选 + 排序 + 导出 curated_YYYY-MM-DD.json
   纯前端,无后端。页面结构约定见 render_curated_html():
   - #curate-item  全部条目卡片(checkbox + ↑↓/置顶/置底按钮, data-link/data-index/data-category)
   - #curate-filter 板块过滤按钮(.cf-btn, data-cat;__all 表示全部)
   - #curate-stats  已勾选计数
   - #curate-export  导出按钮
   过滤可点击板块只显示对应新闻;勾选后用 ↑↓/置顶/置底 调整顺序;
   导出时按当前 DOM 显示顺序收集 .picked 条目。
*/
(function () {
  "use strict";
  var date = document.body.getAttribute("data-curate-date");
  var items = Array.prototype.slice.call(document.querySelectorAll("#curate-item[data-link]"));
  var stats = document.getElementById("curate-stats");
  var exportBtn = document.getElementById("curate-export");
  var list = document.getElementById("curate-list") || document;
  var filterWrap = document.getElementById("curate-filter");
  var activeCat = "__all";

  // 当前过滤下可见的条目(按 DOM 顺序)
  function visibleItems() {
    return list.querySelectorAll("#curate-item[data-link]:not(.hide)");
  }
  function lastVisibleBefore(el) {
    var prev = el.previousElementSibling;
    while (prev && prev.classList.contains("hide")) { prev = prev.previousElementSibling; }
    return prev && prev.classList.contains("picker-item") ? prev : null;
  }
  function nextVisibleAfter(el) {
    var next = el.nextElementSibling;
    while (next && next.classList.contains("hide")) { next = next.nextElementSibling; }
    return next && next.classList.contains("picker-item") ? next : null;
  }
  function firstVisible() { var v = visibleItems(); return v[0] || null; }
  function lastVisible() { var v = visibleItems(); return v[v.length - 1] || null; }

  function applyFilter(cat) {
    activeCat = cat;
    items.forEach(function (el) {
      var match = cat === "__all" || el.getAttribute("data-category") === cat;
      el.classList.toggle("hide", !match);
    });
    if (filterWrap) {
      Array.prototype.forEach.call(filterWrap.querySelectorAll(".cf-btn"), function (b) {
        b.classList.toggle("on", b.getAttribute("data-cat") === cat);
      });
    }
  }

  function picked() {
    return list.querySelectorAll("#curate-item.picked");
  }

  function refresh() {
    var n = picked().length;
    if (stats) { stats.textContent = "已勾选 " + n + " 条"; }
    if (exportBtn) { exportBtn.disabled = n === 0; }
  }

  function swap(a, b) {
    if (!a || !b) { return; }
    a.parentNode.insertBefore(b, a);
  }

  items.forEach(function (el) {
    var box = el.querySelector(".c-check");
    var up = el.querySelector(".c-up");
    var down = el.querySelector(".c-down");
    var topBtn = el.querySelector(".c-top");
    var bottomBtn = el.querySelector(".c-bottom");

    if (box && el.classList.contains("picked")) { box.checked = true; }

    function setPicked(on) {
      el.classList.toggle("picked", on);
      if (box) { box.checked = on; }
      refresh();
    }

    if (box) {
      box.addEventListener("change", function () { setPicked(box.checked); });
    }
    el.addEventListener("click", function (e) {
      if (e.target.closest("button") || e.target.closest("a")) { return; }
      setPicked(!el.classList.contains("picked"));
    });
    if (up) {
      up.addEventListener("click", function (e) {
        e.stopPropagation();
        swap(lastVisibleBefore(el), el);
      });
    }
    if (down) {
      down.addEventListener("click", function (e) {
        e.stopPropagation();
        swap(el, nextVisibleAfter(el));
      });
    }
    if (topBtn) {
      topBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        swap(firstVisible(), el);
      });
    }
    if (bottomBtn) {
      bottomBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        swap(lastVisible(), el);
      });
    }
  });

  if (filterWrap) {
    filterWrap.addEventListener("click", function (e) {
      var btn = e.target.closest(".cf-btn");
      if (btn) { applyFilter(btn.getAttribute("data-cat")); }
    });
  }

  if (exportBtn) {
    exportBtn.addEventListener("click", function () {
      // 按当前 DOM 顺序收集 .picked 条目(跨板块,排序以 DOM 为准)
      var out = Array.prototype.slice.call(list.querySelectorAll("#curate-item.picked"))
        .map(function (el) {
          return { link: el.getAttribute("data-link"),
                   index: parseInt(el.getAttribute("data-index"), 10) };
        });
      var blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = "curated_" + date + ".json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    });
  }

  applyFilter(activeCat);
  refresh();
})();
