from django.conf import settings
from django.db import models


class SearchHistory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    query = models.TextField()
    summary = models.TextField(blank=True)
    trials_data = models.JSONField(default=list)
    papers_data = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Search: {self.query[:50]}"
