let culetScanner = null;
let culetScannerInput = null;
let culetScannerAutoSubmit = false;
let culetScannerAppendMode = false;
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

document.addEventListener("click", async function (e) {
    const scanButton = e.target.closest(".js-culet-scanner-btn");
    if (!scanButton) return;

    culetScannerInput = document.getElementById(scanButton.dataset.target);
    culetScannerAutoSubmit = scanButton.dataset.autoSubmit === "true";
    culetScannerAppendMode = scanButton.dataset.appendMode === "true";

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

                if (culetScannerAppendMode) {
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

                culetScannerInput.value = normalizedBarcode;
                culetScannerInput.dispatchEvent(
                    new Event("input", { bubbles: true })
                );

                await stopCuletScanner();

                if (culetScannerAutoSubmit && culetScannerInput.form) {
                    culetScannerInput.form.requestSubmit();
                }
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

async function stopCuletScanner() {
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

    if (culetScannerInput) {
        culetScannerInput.focus();
        if (typeof culetScannerInput.setSelectionRange === "function") {
            const end = culetScannerInput.value.length;
            culetScannerInput.setSelectionRange(end, end);
        }
    }
}
