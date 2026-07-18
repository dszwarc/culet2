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