"""Pre-go-live security test catalog and safe local automation checks."""

import re
import subprocess
import sys
from pathlib import Path

from django.conf import settings


AUTOMATED_TESTS = (
    ("dependency", "Dependency vulnerability scan", "pip-audit or an approved SCA tool is required in CI."),
    ("static", "Static analysis", "Bandit/Semgrep or an approved SAST tool is required in CI."),
    ("secrets", "Secret scanning", "Repository and history must contain no production credentials."),
    ("api", "API security testing", "Authenticated and unauthorized API paths require evidence."),
    ("configuration", "Configuration scan", "Django checks and production settings must pass."),
    ("tls", "TLS scan", "Run an external TLS scanner against the production-like hostname."),
)

MANUAL_TESTS = (
    "Authentication testing", "Authorization testing", "IDOR testing", "Session testing",
    "Password reset testing", "Brute-force testing", "File upload testing", "Payment testing",
    "Integration testing", "Independent penetration test",
)


def _secret_scan():
    root = Path(settings.BASE_DIR)
    ignored = {".git", ".venv", "__pycache__", "staticfiles", "db.sqlite3"}
    pattern = re.compile(r"(?i)(password|secret|api[_-]?key|private[_-]?key|token)\s*[=:]\s*['\"]([^'\"]{8,})")
    findings = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        # Test fixtures intentionally use disposable passwords and tokens;
        # production and deployment files remain fully scanned.
        if path.name in {"test.py", "tests.py"} or path.name.startswith(("test_", "tests_")) or path.name.endswith("_test.py") or path.name == "seed_demo.py":
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            match = pattern.search(line)
            if match and any(marker in match.group(2) for marker in ("{{", "&#", "${")):
                continue
            if match and "os.environ" not in line and "getenv" not in line and "example" not in path.name.lower():
                findings.append(f"{path.relative_to(root)}:{line_no}")
    return findings


def run_automated_checks():
    results = []
    try:
        subprocess.run([sys.executable, "manage.py", "check"], cwd=settings.BASE_DIR, check=True, capture_output=True, text=True)
        results.append({"key": "configuration", "name": "Configuration scan", "status": "PASS", "detail": "Django system checks passed."})
    except (subprocess.CalledProcessError, OSError) as exc:
        results.append({"key": "configuration", "name": "Configuration scan", "status": "FAIL", "detail": str(exc)[:240]})

    findings = _secret_scan()
    results.append({"key": "secrets", "name": "Secret scanning", "status": "PASS" if not findings else "FAIL", "detail": "No hard-coded credential patterns found." if not findings else f"{len(findings)} possible finding(s) require review."})

    for key, name, detail in AUTOMATED_TESTS:
        if key not in {"configuration", "secrets"}:
            results.append({"key": key, "name": name, "status": "EVIDENCE REQUIRED", "detail": detail})
    return results
