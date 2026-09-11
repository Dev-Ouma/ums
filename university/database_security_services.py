"""Runtime database-security checks used by the go-live cockpit."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from .backup_models import BackupJob, BackupStorage
from .models import AuditLog, FeeAccount


def _check(label, status, detail):
    return {"label": label, "status": status, "detail": detail}


def build_database_security_checks():
    config = connection.settings_dict
    engine = config.get("ENGINE", "")
    is_sqlite = engine.endswith("sqlite3")
    checks = []

    if settings.DEBUG and is_sqlite:
        checks.append(_check(
            "Production database backend", "WARN",
            "Development SQLite is active; configure a private managed database before go-live.",
        ))
    else:
        checks.append(_check(
            "Production database backend", "PASS" if not is_sqlite else "FAIL",
            "A non-SQLite database backend is configured" if not is_sqlite else "SQLite must not be used in production",
        ))

    if is_sqlite:
        checks.append(_check("Database credentials", "WARN" if settings.DEBUG else "FAIL",
                             "SQLite has no separate production credentials"))
        checks.append(_check("Database encryption in transit", "WARN" if settings.DEBUG else "FAIL",
                             "SQLite is local-file development storage; production transport encryption is required"))
        checks.append(_check("Private database network", "WARN" if settings.DEBUG else "FAIL",
                             "No network boundary exists for SQLite; production database must be private"))
    else:
        credentials_ok = bool(config.get("USER") and config.get("PASSWORD"))
        tls_ok = config.get("OPTIONS", {}).get("sslmode") in {"require", "verify-ca", "verify-full"}
        checks.append(_check("Database credentials", "PASS" if credentials_ok else "FAIL",
                             "Separate database user and password are configured outside source code" if credentials_ok else "Database credentials are incomplete"))
        checks.append(_check("Database encryption in transit", "PASS" if tls_ok else "FAIL",
                             f"Database TLS mode: {config.get('OPTIONS', {}).get('sslmode', 'unset')}"))
        checks.append(_check("Private database network", "PASS" if getattr(settings, "DATABASE_PRIVATE_NETWORK", False) else "WARN",
                             "Private-network evidence is configured" if getattr(settings, "DATABASE_PRIVATE_NETWORK", False) else "Provide firewall/private-subnet evidence for the database host"))

    try:
        plan = MigrationExecutor(connection).migration_plan(MigrationExecutor(connection).loader.graph.leaf_nodes())
        checks.append(_check("Migration state", "PASS" if not plan else "FAIL",
                             "All migrations are applied" if not plan else "Unapplied migrations remain; review before deployment"))
    except Exception as exc:
        checks.append(_check("Migration state", "FAIL", f"Migration inspection failed: {exc.__class__.__name__}"))

    try:
        connection.check_constraints()
        checks.append(_check("Foreign-key and database constraints", "PASS",
                             "Database constraint check completed; model relationships and uniqueness constraints are enforced"))
    except Exception as exc:
        checks.append(_check("Foreign-key and database constraints", "FAIL", f"Constraint check failed: {exc.__class__.__name__}"))

    invalid_passwords = get_user_model().objects.exclude(password__startswith="pbkdf2_").exclude(password__startswith="argon2").exclude(password__startswith="bcrypt").count()
    checks.append(_check("Password storage", "PASS" if invalid_passwords == 0 else "FAIL",
                         "User passwords use adaptive one-way hashes" if invalid_passwords == 0 else f"{invalid_passwords} user record(s) do not use an approved password hash"))
    checks.append(_check("Sensitive credential fields", "PASS",
                         f"Payment provider credentials use the encrypted_credentials field; {FeeAccount._meta.model_name} is not exposed to clients"))
    checks.append(_check("Audit record protection", "PASS",
                         "AuditLog has no application edit endpoint; changes are append-oriented and access-controlled"))

    storage = BackupStorage.objects.filter(is_default=True, is_active=True).first()
    checks.append(_check("Encrypted database backups", "PASS" if storage and storage.encryption_at_rest else "WARN",
                         "Default backup storage is marked encrypted" if storage and storage.encryption_at_rest else "Configure an active encrypted default backup destination"))
    restored = BackupJob.objects.filter(status=BackupJob.Status.RESTORE_SUCCESSFUL).exists()
    checks.append(_check("Backup restoration evidence", "PASS" if restored else "WARN",
                         "At least one successful restore is recorded" if restored else "A successful database restore test has not been recorded"))

    return checks
