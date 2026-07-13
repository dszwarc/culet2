let culetScanner = null;
let culetScannerInput = null;
let culetScannerAutoSubmit = false;

document.addEventListener("click", async function (e) {
    const scanButton = e.target.closest(".js-culet-scanner-btn");
    if (!scanButton) return;

    culetScannerInput = document.getElementById(scanButton.dataset.target);
    culetScannerAutoSubmit = scanButton.dataset.autoSubmit === "true";

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
                culetScannerInput.value = decodedText;

                await stopCuletScanner();

                culetScannerInput.focus();

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

    if (overlay) {
        overlay.classList.remove("is-active");
    }
}