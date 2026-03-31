import logging

from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)


class AuditLog(models.Model):
    """Tracks every state mutation per CLAUDE.md Rule #12."""

    entity_type = models.CharField(max_length=50)  # "task", "workflow", "document"
    entity_id = models.IntegerField()
    action = models.CharField(
        max_length=200
    )  # "created", "status_change:created->assigned"
    details = models.JSONField(default=dict, blank=True)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AuditLog entries are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog entries are immutable and cannot be deleted.")

    def __str__(self):
        return f"{self.entity_type}:{self.entity_id} - {self.action}"


class Workflow(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Task(models.Model):
    STATUS_CHOICES = [
        ("created", "Created"),
        ("assigned", "Assigned"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]
    # Per CLAUDE.md Rule #13: Task state transitions enforced by VALID_TRANSITIONS
    VALID_TRANSITIONS = {
        "created": ["assigned", "cancelled"],
        "assigned": ["in_progress", "cancelled"],
        "in_progress": ["completed", "cancelled"],
        "completed": [],
        "cancelled": [],
    }

    workflow = models.ForeignKey(
        Workflow, on_delete=models.CASCADE, related_name="tasks"
    )
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="created")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_tasks",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_tasks",
    )
    due_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    def transition_to(self, new_status, user):
        """Transition task status with validation and audit logging.

        Per CLAUDE.md Rule #13: VALID_TRANSITIONS enforced, never skip validation.
        Per CLAUDE.md Rule #12: AuditLog tracks every state mutation.
        """
        valid = self.VALID_TRANSITIONS.get(self.status, [])
        if new_status not in valid:
            logger.warning(
                "Invalid transition: task=%d, %s → %s", self.id, self.status, new_status
            )
            raise ValueError(f"Cannot transition from {self.status} to {new_status}")
        old_status = self.status
        self.status = new_status
        self.save()
        logger.info(
            "Task %d transitioned: %s → %s by %s",
            self.id,
            old_status,
            new_status,
            user.email,
        )
        AuditLog.objects.create(
            entity_type="task",
            entity_id=self.id,
            action=f"status_change:{old_status}\u2192{new_status}",
            performed_by=user,
        )
