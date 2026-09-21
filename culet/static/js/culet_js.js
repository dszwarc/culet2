function initializeComboBoxes(container = document) {
    $(container)
        .find(".combo-box:not(.select2-hidden-accessible)")
        .select2({
            width: "100%",
            allowClear: true,
            placeholder: function () {
                return $(this).data("placeholder") || "";
            }
        });
}

$(function () {
    $("a").each(function () {
        if ($(this).prop("href") === window.location.href) {
            $(this).addClass("active_link");
            $(this).parents("li").addClass("active");
        }
    });

    $(".datepicker").datepicker();

    initializeComboBoxes();
});

document.body.addEventListener("htmx:afterSwap", function (event) {
    initializeComboBoxes(event.detail.target);
});

// Keep inline forms in place when Django returns validation errors.
document.body.addEventListener("htmx:beforeSwap", function (event) {
    if (event.detail.xhr.status === 422) {
        event.detail.shouldSwap = true;
        event.detail.isError = false;
    }
});

// Follow the edited payroll row even when saving changes the table's layout.
(() => {
    const storageKey = "payroll.editedTimeClockId";

    function restorePayrollRow() {
        if (!document.getElementById("payroll-results")) return;
        let id;
        try {
            id = sessionStorage.getItem(storageKey);
            sessionStorage.removeItem(storageKey);
        } catch (_) {
            return; // Storage may be disabled in the browser.
        }
        if (!id) return;
        // Run after the browser's page-load scroll restoration.
        requestAnimationFrame(() => {
            const row = document.getElementById(`timeclock-row-${id}`);
            if (row) row.scrollIntoView({ block: "center", behavior: "instant" });
        });
    }

    document.addEventListener("submit", function (event) {
        const id = event.target.dataset.timeclockId;
        if (!id) return;
        try {
            sessionStorage.setItem(storageKey, id);
        } catch (_) {
            // Saving the form must still work when storage is unavailable.
        }
    }, true);

    window.addEventListener("pageshow", restorePayrollRow);
    // Support the existing inline swaps without changing how forms are submitted.
    document.body.addEventListener("htmx:afterSettle", function (event) {
        if (event.detail.target.id === "payroll-results") restorePayrollRow();
    });
    document.body.addEventListener("htmx:afterRequest", function (event) {
        if (event.detail.failed && event.detail.elt.matches("form[data-timeclock-id]")) {
            try {
                sessionStorage.removeItem(storageKey);
            } catch (_) {}
        }
    });
})();

/* =========================================================
   Reusable Django formset add/remove controls
   ========================================================= */

/*
 * Add a blank formset row from Django's empty_form template.
 *
 * Required button attributes:
 * data-formset-add
 * data-table-body-id
 * data-total-forms-id
 * data-empty-template-id
 */
document.addEventListener("click", function (event) {
    const addButton = event.target.closest("[data-formset-add]");

    if (!addButton) {
        return;
    }

    const tableBody = document.getElementById(
        addButton.dataset.tableBodyId
    );

    const totalForms = document.getElementById(
        addButton.dataset.totalFormsId
    );

    const emptyTemplate = document.getElementById(
        addButton.dataset.emptyTemplateId
    );

    if (!tableBody || !totalForms || !emptyTemplate) {
        console.warn("Unable to add formset row.", {
            tableBodyId: addButton.dataset.tableBodyId,
            totalFormsId: addButton.dataset.totalFormsId,
            emptyTemplateId: addButton.dataset.emptyTemplateId
        });

        return;
    }

    const formIndex = Number.parseInt(totalForms.value, 10);

    if (Number.isNaN(formIndex)) {
        console.error(
            "Invalid TOTAL_FORMS value:",
            totalForms.value
        );

        return;
    }

    const rowHtml = emptyTemplate.innerHTML.replaceAll(
        "__prefix__",
        String(formIndex)
    );

    tableBody.insertAdjacentHTML("beforeend", rowHtml);
    totalForms.value = formIndex + 1;

    /*
     * Initialize Select2 fields inside the newly added row,
     * if that row contains any combo-box fields.
     */
    const newRow = tableBody.lastElementChild;

    if (
        newRow &&
        typeof initializeComboBoxes === "function"
    ) {
        initializeComboBoxes(newRow);
    }
});


/*
 * Remove a formset row using Django's DELETE field.
 *
 * The row remains in the submitted formset, but it is hidden
 * and Django ignores or deletes it when the form is saved.
 */
document.addEventListener("click", function (event) {
    const removeButton = event.target.closest(
        ".formset-remove-row"
    );

    if (!removeButton) {
        return;
    }

    const row = removeButton.closest("tr");

    if (!row) {
        return;
    }

    const deleteField = row.querySelector(
        'input[name$="-DELETE"]'
    );

    if (deleteField) {
        deleteField.checked = true;
        row.classList.add("formset-row-is-deleted");
        return;
    }

    /*
     * Fallback for any formset accidentally created without
     * can_delete=True.
     */
    row.remove();
});

/*
|--------------------------------------------------------------------------
| Desktop-only autofocus
|--------------------------------------------------------------------------
*/

window.CuletDevice = {
    isTouchDevice() {
        return (
            window.matchMedia("(pointer: coarse)").matches ||
            navigator.maxTouchPoints > 0 ||
            "ontouchstart" in window
        );
    },

    autofocusDesktopInput(selector) {
        if (this.isTouchDevice()) {
            return;
        }

        const input = document.querySelector(selector);

        if (input) {
            input.focus();
        }
    }
};

document.addEventListener("DOMContentLoaded", function () {
    window.CuletDevice.autofocusDesktopInput(
        "[data-autofocus-desktop='true']"
    );
});

/*
|--------------------------------------------------------------------------
| Reusable mobile filter panels
|--------------------------------------------------------------------------
*/

function initializeFilterPanels() {
    const panels = document.querySelectorAll(
        "[data-filter-panel]"
    );

    panels.forEach(function (panel) {
        if (panel.dataset.filterInitialized === "true") {
            return;
        }

        panel.dataset.filterInitialized = "true";

        const details = panel.querySelector(
            "[data-filter-details]"
        );

        if (details) {
            const mobileQuery = window.matchMedia(
                "(max-width: 767px)"
            );

            function setDetailsState(event) {
                details.open = !event.matches;
            }

            setDetailsState(mobileQuery);
            mobileQuery.addEventListener(
                "change",
                setDetailsState
            );
            return;
        }

        const toggle = panel.querySelector(
            "[data-filter-toggle]"
        );

        const countElement = panel.querySelector(
            "[data-filter-active-count]"
        );

        if (!toggle) {
            return;
        }

        const ignoredParameters = new Set([
            "page",
            "sort",
            "direction",
        ]);

        const urlParameters = new URLSearchParams(
            window.location.search
        );

        let activeFilterCount = 0;

        urlParameters.forEach(function (value, key) {
            if (
                value.trim() !== "" &&
                !ignoredParameters.has(key)
            ) {
                activeFilterCount += 1;
            }
        });

        if (countElement && activeFilterCount > 0) {
            countElement.textContent = (
                activeFilterCount === 1
                    ? "1 active"
                    : `${activeFilterCount} active`
            );

            countElement.hidden = false;
        }

        const shouldOpenInitially = (
            panel.dataset.mobileOpen === "true" ||
            activeFilterCount > 0
        );

        function setPanelState(isOpen) {
            panel.classList.toggle(
                "is-open",
                isOpen
            );

            toggle.setAttribute(
                "aria-expanded",
                String(isOpen)
            );
        }

        setPanelState(shouldOpenInitially);

        toggle.addEventListener(
            "click",
            function () {
                setPanelState(
                    !panel.classList.contains(
                        "is-open"
                    )
                );
            }
        );
    });
}

document.addEventListener(
    "DOMContentLoaded",
    initializeFilterPanels
);

/*
 * Reinitialize after an HTMX page fragment is inserted.
 */
document.addEventListener(
    "htmx:afterSwap",
    initializeFilterPanels
);
