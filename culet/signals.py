from django.db import transaction
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from .models import Style


def delete_file_after_commit(storage, file_name):
    """
    Delete a stored file only after the current database transaction
    successfully commits.
    """
    if not file_name:
        return

    transaction.on_commit(
        lambda: storage.delete(file_name)
    )


def file_is_used_by_another_style(field_name, file_name, excluded_pk=None):
    """
    Avoid deleting a file if another Style record references the same path.
    """
    if not file_name:
        return False

    queryset = Style.objects.filter(
        **{field_name: file_name}
    )

    if excluded_pk is not None:
        queryset = queryset.exclude(pk=excluded_pk)

    return queryset.exists()


@receiver(pre_save, sender=Style)
def delete_replaced_style_files(sender, instance, **kwargs):
    """
    Delete the previous photo or spec sheet when it is replaced or cleared.
    """
    if not instance.pk:
        return

    try:
        previous_style = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return

    file_fields = ("photo", "spec_sheet")

    for field_name in file_fields:
        previous_file = getattr(previous_style, field_name)
        new_file = getattr(instance, field_name)

        previous_name = previous_file.name if previous_file else ""
        new_name = new_file.name if new_file else ""

        if not previous_name or previous_name == new_name:
            continue

        if file_is_used_by_another_style(
            field_name=field_name,
            file_name=previous_name,
            excluded_pk=instance.pk,
        ):
            continue

        delete_file_after_commit(
            storage=previous_file.storage,
            file_name=previous_name,
        )


@receiver(post_delete, sender=Style)
def delete_style_files_when_style_is_deleted(sender, instance, **kwargs):
    """
    Delete the style's photo and spec sheet when the Style itself is deleted.
    """
    for field_name in ("photo", "spec_sheet"):
        stored_file = getattr(instance, field_name)

        if not stored_file or not stored_file.name:
            continue

        if file_is_used_by_another_style(
            field_name=field_name,
            file_name=stored_file.name,
        ):
            continue

        delete_file_after_commit(
            storage=stored_file.storage,
            file_name=stored_file.name,
        )