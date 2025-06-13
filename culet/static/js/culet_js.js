$(function(){
    $('a').each(function(){
        if ($(this).prop('href') == window.location.href) {
            $(this).addClass('active_link'); $(this).parents('li').addClass('active');
        }
    });
});

$(function(){
    $(".datepicker").datepicker();
});