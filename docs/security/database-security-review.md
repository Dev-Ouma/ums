# UMS Database Security Review

## Implemented controls

- Local SQLite is retained only as a development default. Production now fails closed unless `DJANGO_DB_ENGINE`, `DJANGO_DB_NAME`, `DJANGO_DB_USER`, `DJANGO_DB_PASSWORD`, and `DJANGO_DB_HOST` are supplied.
- Production database TLS is required by default through `DJANGO_DB_SSL_REQUIRE=true` and `verify-full` for PostgreSQL-compatible deployments.
- Database credentials are read from the environment and are not committed to source code.
- Runtime checks report database backend, credentials, TLS mode, private-network evidence, migration state, constraints, password hashing, encrypted backup storage, and restore evidence.
- Django foreign keys, one-to-one relations, unique fields, conditional uniqueness constraints, payment idempotency fields, and status validation are enforced at model/database boundaries.
- Passwords use Django adaptive password hashing; password-history and reset-token records store hashes/digests only.
- Payment provider credentials are stored in the `encrypted_credentials` field and are excluded from client-facing exports.
- Audit records are append-oriented through the application, have no application edit endpoint, and are restricted through the audit dashboard permissions.

## Required production evidence

Before go-live, the DBA must attach evidence to the Go-Live Database/Recovery domains for:

1. Database host is on a private subnet/firewall and has no public listener.
2. Application, migration, backup, and DBA credentials are separate and least-privileged.
3. Database TLS certificate verification and encryption-at-rest settings are enabled.
4. Migration review, backup-before-migration, rollback strategy, and post-migration validation were completed.
5. A database backup was restored into an isolated environment and the application smoke-tested against it.
6. Constraint/integrity checks and financial transaction atomicity were tested.
7. Audit storage access, retention, tamper protection, and break-glass access were reviewed.

The runtime cockpit can verify configuration and recorded evidence, but it cannot prove cloud firewall rules, disk encryption, credential rotation, or an honest restore without an attached test record.
