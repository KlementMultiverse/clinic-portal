from django.conf import settings
from django.db import models


class Document(models.Model):
    """Document model for S3-backed file storage.

    Per CLAUDE.md Rule #6: S3 keys namespaced by tenant schema name.
    Per CLAUDE.md Rule #7: Presigned URLs expire after 15 minutes.
    Per CLAUDE.md Rule #12: AuditLog tracks every state mutation.
    """

    name = models.CharField(max_length=300)
    s3_key = models.CharField(max_length=500)
    content_type = models.CharField(max_length=100)
    size_bytes = models.IntegerField()
    summary = models.TextField(blank=True)
    workflow = models.ForeignKey(
        "workflows.Workflow",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="documents",
    )
    task = models.ForeignKey(
        "workflows.Task",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="documents",
    )
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
