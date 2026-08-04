let culetScanner = null;
let culetScannerInput = null;
let culetScannerAutoSubmit = false;
let culetScannerMode = "replace";
let culetScannerScanAccepted = false;
let culetScannerLastBarcode = "";
let culetScannerLastScanAt = 0;

function appendBarcodeToTextarea(textarea, barcode) {
    const normalizedBarcode = String(barcode || "").trim();

    if (!normalizedBarcode) return false;

    let currentValue = textarea.value;

    if (currentValue && !currentValue.endsWith("\n")) {
        currentValue += "\n";
    }

    textarea.value = currentValue + normalizedBarcode + "\n";
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
}

function replaceBarcodeValue(input, barcode) {
    const normalizedBarcode = String(barcode || "").trim();

    if (!normalizedBarcode) return false;

    input.value = normalizedBarcode;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
}

function restoreScannerTargetFocus(target) {
    if (!target || target.type === "hidden") return;

    target.focus();

    const supportsTextSelection =
        target.tagName === "TEXTAREA" ||
        (
            target.tagName === "INPUT" &&
            ["text", "search", "tel", "url", "password", "email"].includes(target.type)
        );

    if (!supportsTextSelection) return;

    try {
        const end = target.value.length;
        target.setSelectionRange(end, end);
    } catch (err) {
        // Focus restoration must never block scanner cleanup or submission.
    }
}

document.addEventListener("click", async function (e) {
    const scanButton = e.target.closest(".js-culet-scanner-btn");
    if (!scanButton) return;

    culetScannerInput = document.getElementById(scanButton.dataset.target);
    culetScannerAutoSubmit = scanButton.dataset.autoSubmit === "true";
    culetScannerMode = scanButton.dataset.scannerMode || (
        scanButton.dataset.appendMode === "true" ? "append-lines" : "replace"
    );
    culetScannerScanAccepted = false;

    if (!culetScannerInput) {
        alert("Scanner target input was not found.");
        return;
    }

    if (typeof Html5Qrcode === "undefined") {
        alert("Camera scanner library is not loaded.");
        return;
    }

    const overlay = document.getElementById("culet-scanner-overlay");

    if (!overlay) {
        alert("Scanner overlay was not found.");
        return;
    }

    overlay.classList.add("is-active");

    const status = document.getElementById("culet-scanner-status");
    if (status) status.textContent = "Scanner ready.";

    if (culetScanner) {
        await stopCuletScanner();
        overlay.classList.add("is-active");
    }

    culetScanner = new Html5Qrcode("culet-scanner-reader");

    try {
        await culetScanner.start(
            { facingMode: "environment" },
            {
                fps: 10,
                qrbox: {
                    width: 300,
                    height: 140
                },
                formatsToSupport: [
                    Html5QrcodeSupportedFormats.CODE_128
                ]
            },
            async function (decodedText) {
                const normalizedBarcode = String(decodedText || "").trim();
                if (!normalizedBarcode) return;

                if (culetScannerMode === "append-lines") {
                    const now = Date.now();
                    if (
                        normalizedBarcode === culetScannerLastBarcode &&
                        now - culetScannerLastScanAt < 1500
                    ) {
                        culetScannerLastScanAt = now;
                        return;
                    }

                    culetScannerLastBarcode = normalizedBarcode;
                    culetScannerLastScanAt = now;
                    appendBarcodeToTextarea(culetScannerInput, normalizedBarcode);

                    if (status) {
                        status.textContent = `Added barcode ${normalizedBarcode}.`;
                    }
                    return;
                }

                if (culetScannerScanAccepted) return;
                culetScannerScanAccepted = true;

                const targetInput = culetScannerInput;
                const targetForm = targetInput.form;
                const shouldAutoSubmit = culetScannerAutoSubmit;

                replaceBarcodeValue(targetInput, normalizedBarcode);

                if (shouldAutoSubmit && targetForm) {
                    targetForm.requestSubmit();
                }

                await stopCuletScanner({ restoreFocus: false });
            }
        );
    } catch (err) {
        await stopCuletScanner();
        alert("Camera scanner could not start. You can still type or USB-scan the barcode.");
    }
});

document.addEventListener("click", async function (e) {
    if (e.target.id === "culet-scanner-cancel") {
        await stopCuletScanner();
    }
});

async function stopCuletScanner({ restoreFocus = true } = {}) {
    const overlay = document.getElementById("culet-scanner-overlay");

    if (culetScanner) {
        try {
            await culetScanner.stop();
        } catch (err) {}

        try {
            await culetScanner.clear();
        } catch (err) {}
    }

    culetScanner = null;
    culetScannerLastBarcode = "";
    culetScannerLastScanAt = 0;

    if (overlay) {
        overlay.classList.remove("is-active");
    }

    if (restoreFocus) {
        restoreScannerTargetFocus(culetScannerInput);
    }
}

window.CuletBarcodeScanner = {
    appendBarcodeToTextarea,
    replaceBarcodeValue,
    restoreScannerTargetFocus
};
