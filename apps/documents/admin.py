from django.contrib import admin

from apps.documents.models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "content_type",
        "size_display",
        "uploaded_by",
        "workflow",
        "created_at",
    ]
    list_filter = ["content_type", "created_at"]
    search_fields = ["name", "s3_key"]
    readonly_fields = ["s3_key", "created_at"]
    raw_id_fields = ["uploaded_by", "workflow", "task"]

    def size_display(self, obj):
        if obj.size_bytes < 1024:
            return f"{obj.size_bytes} B"
        elif obj.size_bytes < 1024 * 1024:
            return f"{obj.size_bytes / 1024:.1f} KB"
        return f"{obj.size_bytes / (1024 * 1024):.1f} MB"

    size_display.short_description = "Size"
