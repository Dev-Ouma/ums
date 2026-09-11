# UMS Pre-Go-Live Infrastructure Security

This checklist is an operator gate for the production host. The Django
application cannot verify or configure the operating system, firewall, SSH
daemon, database bind address, or cloud network policy from a browser.

## Production host checks

Run these checks from an approved administrator session on the production
Linux host and attach the output to the Go-Live Readiness evidence record.

```sh
# OS and listening services
cat /etc/os-release
sudo ss -lntup
sudo systemctl --type=service --state=running

# Patching and firewall, using the host's package/firewall tooling
sudo unattended-upgrade --dry-run 2>/dev/null || true
sudo ufw status verbose 2>/dev/null || sudo firewall-cmd --list-all 2>/dev/null

# SSH effective configuration, without exposing private keys
sudo sshd -T | egrep 'permitrootlogin|passwordauthentication|pubkeyauthentication|maxauthtries|x11forwarding|allowusers|allowgroups'

# File ownership and writable application paths
namei -l /srv/ums
find /srv/ums -type f -perm /022 -print
find /srv/ums/media -type f \( -perm /111 -o -name '*.cgi' -o -name '*.php' \) -print

# Search tracked history and the working tree for credential-like material.
# Review every result; rotate any real credential that was ever committed.
git grep -n -I -E '(password|secret|api[_-]?key|private[_-]?key|authorization)' HEAD
git log -S'DJANGO_SECRET_KEY' --all --oneline
```

## Required production decisions

- Expose only `443` at the public edge. Keep database, Redis, queues, debug
  ports, internal APIs, and admin services on private interfaces or networks.
- Disable direct root SSH login. Prefer keys or hardware-backed credentials,
  restrict administrator source networks, and monitor failed logins.
- Enable the host firewall and document the exact allowlist for the reverse
  proxy, application, database, backup, monitoring, and bastion paths.
- Run the application and workers as dedicated non-root accounts. Keep source,
  configuration, logs, backups, and uploads owned by the appropriate service
  accounts with no world-writable directories.
- Store production secrets in the deployment secret manager. Set
  `DJANGO_DEBUG=false`, an explicit `DJANGO_SECRET_KEY`, and exact
  `DJANGO_ALLOWED_HOSTS` values before deployment.
- Put uploads outside executable web roots and serve them only through an
  authorization-checked download view. Scan uploads where the institution's
  operating model supports it.

## Local audit result

The current development workstation is not production-ready: macOS Firewall
is disabled, SSH allows password authentication by default, and local MariaDB
is listening on `*:3306`. Django itself is bound to `127.0.0.1:8001`, which is
appropriate for local development. Do not expose this workstation as the
production host; remediate these findings on the approved production server
and record evidence in the UMS Go-Live Readiness module.
