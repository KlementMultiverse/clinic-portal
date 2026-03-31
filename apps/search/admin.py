from django.contrib import admin

from apps.search.models import ChatMessage, ChatThread, SearchHistory


@admin.register(SearchHistory)
class SearchHistoryAdmin(admin.ModelAdmin):
    list_display = ["query_short", "user", "trials_count", "papers_count", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["query"]
    readonly_fields = ["created_at"]

    def query_short(self, obj):
        return obj.query[:60]

    query_short.short_description = "Query"

    def trials_count(self, obj):
        return len(obj.trials_data) if obj.trials_data else 0

    trials_count.short_description = "Trials"

    def papers_count(self, obj):
        return len(obj.papers_data) if obj.papers_data else 0

    papers_count.short_description = "Papers"


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ["role", "content", "created_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ChatThread)
class ChatThreadAdmin(admin.ModelAdmin):
    list_display = ["title", "user", "message_count", "updated_at"]
    list_filter = ["updated_at"]
    search_fields = ["title"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [ChatMessageInline]

    def message_count(self, obj):
        return obj.messages.count()

    message_count.short_description = "Messages"
