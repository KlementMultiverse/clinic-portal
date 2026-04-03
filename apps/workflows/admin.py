from django.contrib import admin
from django.utils.html import format_html

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
    list_display = ["name", "description_short", "created_by", "task_summary", "created_at"]
    search_fields = ["name", "description"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "modified_at"]
    inlines = [TaskInline]

    def description_short(self, obj):
        if len(obj.description) > 60:
            return obj.description[:60] + "..."
        return obj.description

    description_short.short_description = "Description"

    def task_summary(self, obj):
        tasks = obj.tasks.all()
        total = tasks.count()
        if total == 0:
            return "No tasks"
        completed = tasks.filter(status="completed").count()
        in_progress = tasks.filter(status="in_progress").count()
        return format_html(
            '<span title="completed/in-progress/total">'
            '<span style="color:#28a745;">{}</span> / '
            '<span style="color:#fd7e14;">{}</span> / '
            '{}</span>',
            completed,
            in_progress,
            total,
        )

    task_summary.short_description = "Done / Active / Total"


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "workflow",
        "status_badge",
        "assigned_to",
        "created_by",
        "created_at",
    ]
    list_filter = ["status", "workflow", "created_at"]
    search_fields = ["title", "description"]
    list_editable = ["assigned_to"]
    readonly_fields = ["created_at", "modified_at"]
    raw_id_fields = ["assigned_to", "created_by", "workflow"]

    def status_badge(self, obj):
        colors = {
            "created": "#6c757d",
            "assigned": "#17a2b8",
            "in_progress": "#fd7e14",
            "completed": "#28a745",
            "cancelled": "#dc3545",
        }
        color = colors.get(obj.status, "#6c757d")
        label = obj.status.replace("_", " ").upper()
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; '
            'border-radius:3px; font-size:11px; white-space:nowrap;">{}</span>',
            color,
            label,
        )

    status_badge.short_description = "Status"


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["timestamp", "entity_type", "entity_id", "action_display", "performed_by"]
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

    def action_display(self, obj):
        if "status_change" in obj.action:
            return format_html(
                '<span style="color:#007bff;">{}</span>',
                obj.action,
            )
        return obj.action

    action_display.short_description = "Action"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
