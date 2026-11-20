(function () {
    "use strict";

    var interval = 60000;
    var retryInterval = 1000;
    var timer = null;
    var inFlight = false;
    var pending = false;
    var dirtyForms = new Set();

    function page() {
        return document.querySelector(".my-jobs-page");
    }

    function safeToRefresh() {
        var active = document.activeElement;
        var scanner = document.getElementById("culet-scanner-overlay");
        dirtyForms.forEach(function (form) {
            if (!form.isConnected) dirtyForms.delete(form);
        });
        return document.visibilityState === "visible" &&
            dirtyForms.size === 0 &&
            !(scanner && scanner.classList.contains("is-active")) &&
            !(active && active.closest("form, input, textarea, select, [contenteditable='true']")) &&
            !document.querySelector("dialog[open], .modal.show, [role='dialog'][aria-modal='true']:not([hidden]), .select2-container--open");
    }

    function schedule(delay) {
        window.clearTimeout(timer);
        timer = window.setTimeout(refresh, delay);
    }

    function refresh() {
        timer = null;
        var element = page();
        if (!element) return;
        if (!safeToRefresh() || inFlight) {
            pending = true;
            if (document.visibilityState === "visible" && !inFlight) schedule(retryInterval);
            return;
        }
        pending = false;
        window.htmx.trigger(element, "culet:refresh");
    }

    function isRefresh(event) {
        var detail = event.detail || {};
        var element = (detail.requestConfig && detail.requestConfig.elt) || detail.elt;
        return !!(element && element.matches(".my-jobs-page"));
    }

    function markDirty(event) {
        var form = event.target && event.target.closest(".my-jobs-page form");
        if (form) dirtyForms.add(form);
    }
    document.addEventListener("input", markDirty);
    document.addEventListener("change", markDirty);
    document.addEventListener("reset", function (event) {
        dirtyForms.delete(event.target);
    });

    document.addEventListener("htmx:beforeRequest", function (event) {
        if (isRefresh(event)) inFlight = true;
    });

    document.addEventListener("htmx:beforeSwap", function (event) {
        if (!isRefresh(event)) return;
        if (!safeToRefresh()) {
            // An interaction may have started after the request was sent.
            event.detail.shouldSwap = false;
            pending = true;
        } else {
            pending = false;
        }
    });

    document.addEventListener("htmx:afterRequest", function (event) {
        if (!isRefresh(event)) return;
        inFlight = false;
        if (document.visibilityState === "visible") {
            schedule(pending ? (safeToRefresh() ? 0 : retryInterval) : interval);
        }
    });

    document.addEventListener("visibilitychange", function () {
        window.clearTimeout(timer);
        timer = null;
        if (document.visibilityState === "visible") {
            pending = true;
            if (!inFlight) schedule(safeToRefresh() ? 0 : retryInterval);
        } else {
            pending = true;
        }
    });

    if (page() && document.visibilityState === "visible") schedule(interval);
}());
