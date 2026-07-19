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