from django.contrib import admin

from apps.workflows.models import AuditLog, Task, Workflow


class TaskInline(admin.TabularInline):
    """Show tasks inline when viewing a workflow."""

    model = Task
    extra = 0
    fields = ["title", "status", "assigned_to", "created_at"]
    readonly_fields = ["created_at"]
    show_change_link = True


@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ["name", "created_by", "task_count", "created_at"]
    search_fields = ["name", "description"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "modified_at"]
    inlines = [TaskInline]

    def task_count(self, obj):
        return obj.tasks.count()

    task_count.short_description = "Tasks"


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "workflow",
        "status",
        "assigned_to",
        "created_by",
        "created_at",
    ]
    list_filter = ["status", "workflow", "created_at"]
    search_fields = ["title", "description"]
    list_editable = ["status", "assigned_to"]
    readonly_fields = ["created_at", "modified_at"]
    raw_id_fields = ["assigned_to", "created_by", "workflow"]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["entity_type", "entity_id", "action", "performed_by", "timestamp"]
    list_filter = ["entity_type", "action", "timestamp"]
    search_fields = ["action", "entity_type"]
    readonly_fields = [
        "entity_type",
        "entity_id",
        "action",
        "details",
        "performed_by",
        "timestamp",
    ]
    date_hierarchy = "timestamp"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
