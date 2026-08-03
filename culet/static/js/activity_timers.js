(function () {
    "use strict";

    function formatElapsedSeconds(seconds) {
        var safeSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
        var hours = Math.floor(safeSeconds / 3600);
        var minutes = Math.floor((safeSeconds % 3600) / 60);
        var remainingSeconds = safeSeconds % 60;
        var minuteText = String(minutes).padStart(2, "0");
        var secondText = String(remainingSeconds).padStart(2, "0");

        if (hours > 0) {
            return String(hours).padStart(2, "0") + ":" + minuteText + ":" + secondText;
        }

        return minuteText + ":" + secondText;
    }

    function renderTimers() {
        var now = Date.now();

        document.querySelectorAll(".activity-timer").forEach(function (timer) {
            var startMilliseconds = Date.parse(timer.dataset.startTime || "");

            if (!Number.isFinite(startMilliseconds)) {
                timer.textContent = "--:--";
                return;
            }

            timer.textContent = formatElapsedSeconds(
                Math.floor((now - startMilliseconds) / 1000)
            );
        });
    }

    window.CuletActivityTimers = {
        formatElapsedSeconds: formatElapsedSeconds,
        render: renderTimers
    };

    renderTimers();
    window.setInterval(renderTimers, 1000);
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) {
            renderTimers();
        }
    });
}());
