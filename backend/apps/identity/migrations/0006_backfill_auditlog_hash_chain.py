import hashlib
import json

from django.db import migrations


def backfill_audit_hashes(apps, schema_editor):
    AuditLog = apps.get_model("identity", "AuditLog")
    prev_hash = ""
    for log in AuditLog.objects.order_by("created_at", "id"):
        log.prev_hash = prev_hash
        payload = (
            f"{prev_hash}:"
            f"{log.id}:"
            f"{log.created_at.isoformat() if log.created_at else ''}:"
            f"{log.actor_id or ''}:"
            f"{log.action}:{log.entity_type}:{log.entity_id}:"
            f"{json.dumps(log.metadata or {}, sort_keys=True)}"
        )
        log.hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        log.save(update_fields=["prev_hash", "hash"])
        prev_hash = log.hash


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0005_auditlog_prev_hash"),
    ]

    operations = [
        migrations.RunPython(backfill_audit_hashes, noop),
    ]
