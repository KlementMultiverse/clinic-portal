from datetime import datetime


def track_action(request, action_type, entity_type, entity_name, entity_id=None):
    """Track user action in session memory. Keeps last 5 actions."""
    if not hasattr(request, "session"):
        return
    actions = request.session.get("recent_actions", [])
    actions.append(
        {
            "action": action_type,
            "entity_type": entity_type,
            "entity_name": str(entity_name),
            "entity_id": entity_id,
            "timestamp": datetime.now().isoformat(),
        }
    )
    request.session["recent_actions"] = actions[-5:]  # Keep last 5
