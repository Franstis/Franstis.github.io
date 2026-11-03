// Small interactive touches. The site works without this file: everything it
// controls starts hidden (`data-js hidden`) and is shown here.
(function () {
  var root = document.documentElement;
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  document.querySelectorAll("[data-js]").forEach(function (el) { el.hidden = false; });

  // --- Light/dark switch -----------------------------------------------------
  var toggle = document.querySelector(".theme-toggle");
  var systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  function theme() { return root.getAttribute("data-theme") || (systemDark.matches ? "dark" : "light"); }
  function labelToggle() {
    toggle.setAttribute("aria-label", theme() === "dark" ? "Switch to light theme" : "Switch to dark theme");
  }
  if (toggle) {
    labelToggle();
    toggle.addEventListener("click", function () {
      var next = theme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("theme", next); } catch (e) {}
      labelToggle();
    });
  }

  // --- Header, back-to-top button and banner parallax ------------------------
  var header = document.querySelector(".site-header");
  var toTop = document.querySelector(".to-top");
  var bannerBg = document.querySelector(".banner-home .banner-bg");
  var ticking = false;
  function onScroll() {
    var y = window.scrollY;
    header.classList.toggle("is-scrolled", y > 24);
    if (toTop) toTop.classList.toggle("is-visible", y > 700);
    if (bannerBg && !reduceMotion && y < window.innerHeight) {
      bannerBg.style.setProperty("--parallax", (y * 0.3).toFixed(1) + "px");
    }
    ticking = false;
  }
  // Sticky elements (Talks years) sit just below the header, whatever its height
  function measureHeader() { root.style.setProperty("--header-h", header.offsetHeight + "px"); }
  measureHeader();
  window.addEventListener("resize", measureHeader);

  window.addEventListener("scroll", function () {
    if (!ticking) { ticking = true; requestAnimationFrame(onScroll); }
  }, { passive: true });
  onScroll();
  if (toTop) {
    toTop.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
    });
  }

  // --- Toast and copy buttons ------------------------------------------------
  var toastEl = document.querySelector(".toast");
  var toastTimer;
  function toast(message) {
    if (!toastEl) return;
    toastEl.textContent = message;
    toastEl.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove("is-visible"); }, 2200);
  }

  function copyText(text, message) {
    // Copy straight away inside the click, so there is always feedback;
    // the Clipboard API is the backup for browsers that refuse execCommand.
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(area);
    if (ok) { toast(message); return; }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { toast(message); }, function () {
        toast("Could not copy: please select the text instead");
      });
    } else {
      toast("Could not copy: please select the text instead");
    }
  }

  function citation(item) {
    function text(selector) {
      var el = item.querySelector(selector);
      return el ? el.textContent.replace(/\s+/g, " ").trim() : "";
    }
    var parts = [text(".pub-authors") + " (" + item.dataset.year + ").", text(".pub-title") + "."];
    var venue = text(".pub-venue");
    if (venue) parts.push(venue + ".");
    var doi = item.querySelector('a[href^="https://doi.org/"]');
    if (doi) parts.push(doi.href);
    else if (item.dataset.arxiv) parts.push("arXiv:" + item.dataset.arxiv);
    return parts.join(" ");
  }

  document.addEventListener("click", function (event) {
    var copy = event.target.closest("[data-copy]");
    if (copy) copyText(copy.dataset.copy, copy.dataset.copyMessage || "Copied");
    var cite = event.target.closest(".pub-cite");
    if (cite) copyText(citation(cite.closest("li")), "Citation copied");
  });

  // --- Search and filter buttons (Publications, Talks) -----------------------
  document.querySelectorAll(".filter-bar").forEach(function (bar) {
    var scope = document.querySelector(bar.dataset.scope) || document;
    var items = Array.prototype.slice.call(scope.querySelectorAll("[data-filter-item]"));
    var sections = scope.querySelectorAll("[data-filter-section]");
    var buttons = bar.querySelectorAll("[data-filter]");
    var input = bar.querySelector("input[type=search]");
    var counter = bar.querySelector("[data-filter-count]");
    var empty = scope.querySelector("[data-filter-empty]");
    var params = new URLSearchParams(location.search);
    var active = "all";
    if (bar.querySelector('[data-filter="' + params.get("filter") + '"]')) active = params.get("filter");
    if (input && params.get("q")) input.value = params.get("q");

    items.forEach(function (item) { item.dataset.text = item.textContent.toLowerCase(); });

    function apply() {
      var terms = input ? input.value.toLowerCase().split(/\s+/).filter(Boolean) : [];
      var shown = 0;
      items.forEach(function (item) {
        var inGroup = active === "all" || (item.dataset.group || "").split(" ").indexOf(active) >= 0;
        var matches = terms.every(function (t) { return item.dataset.text.indexOf(t) >= 0; });
        item.hidden = !(inGroup && matches);
        if (!item.hidden) shown++;
      });
      sections.forEach(function (s) {
        var visible = s.querySelectorAll("[data-filter-item]:not([hidden])").length;
        var badge = s.querySelector(".count");
        if (badge) badge.textContent = visible;
        s.hidden = visible === 0;
      });
      buttons.forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.filter === active)); });
      if (counter) counter.textContent = shown === items.length ? "" : "Showing " + shown + " of " + items.length;
      if (empty) empty.hidden = shown > 0;

      var next = new URLSearchParams(location.search);
      if (terms.length) next.set("q", input.value.trim()); else next.delete("q");
      if (active !== "all") next.set("filter", active); else next.delete("filter");
      var query = next.toString();
      history.replaceState(null, "", location.pathname + (query ? "?" + query : "") + location.hash);
    }

    buttons.forEach(function (b) {
      b.addEventListener("click", function () { active = b.dataset.filter; apply(); });
    });
    if (input) {
      input.addEventListener("input", apply);
      input.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { input.value = ""; apply(); }
      });
      // "/" jumps to the search box, as on many sites
      document.addEventListener("keydown", function (e) {
        var tag = (document.activeElement || {}).tagName;
        if (e.key === "/" && tag !== "INPUT" && tag !== "TEXTAREA") { e.preventDefault(); input.focus(); }
      });
    }
    scope.querySelectorAll("[data-filter-reset]").forEach(function (b) {
      b.addEventListener("click", function () { if (input) input.value = ""; active = "all"; apply(); });
    });
    apply();
  });

  // --- Reveal on scroll and counting numbers ---------------------------------
  if (reduceMotion || !("IntersectionObserver" in window)) return;

  function countUp(el) {
    var target = parseInt(el.dataset.count, 10);
    var start = performance.now();
    function frame(now) {
      var t = Math.min((now - start) / 900, 1);
      el.textContent = Math.round(target * (1 - Math.pow(1 - t, 3)));
      if (t < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("in");
      entry.target.querySelectorAll("[data-count]").forEach(countUp);
      observer.unobserve(entry.target);
    });
  }, { rootMargin: "0px 0px -6% 0px" });

  document.querySelectorAll(
    "main > h2, main > section > h2, .lead, .stat, .card, .chart, .pub-list > li, .latest-list > li, .timeline-item, .contact-card, main > table"
  ).forEach(function (el) {
    // Stagger siblings that come into view together
    var index = Array.prototype.indexOf.call(el.parentNode.children, el);
    el.style.setProperty("--reveal-delay", (index % 6) * 60 + "ms");
    el.classList.add("reveal");
    observer.observe(el);
  });
})();
