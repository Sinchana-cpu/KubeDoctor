"""Safety layer: a read-only command allowlist and secret redaction.

Every diagnostic command the agent runs passes through ``check_readonly``.
The only cluster mutation the program can ever perform is a single, explicit
``kubectl patch deployment`` that lives in backend.py and is gated by approval.
"""
import re

ALLOWED_VERBS = {"get", "describe", "logs", "top"}
ROLLOUT_OK = {"status", "history"}
CONFIG_OK = {"current-context", "get-contexts"}
FORBIDDEN_FLAGS = {"--as", "--as-group", "--token", "--kubeconfig", "--raw", "--server", "-s",
                   "--insecure-skip-tls-verify", "--username", "--password"}
SECRET_RESOURCE = re.compile(r"(^|[,/])secrets?($|[,/])", re.I)
DNS_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")


class UnsafeCommand(Exception):
    """Raised when a command is not on the read-only allowlist."""


def check_name(value, what="name"):
    if not isinstance(value, str) or len(value) > 253 or not DNS_NAME.match(value):
        raise UnsafeCommand(f"Refusing unusual {what}: {value!r}")
    return value


def check_readonly(args):
    """Raise UnsafeCommand unless ``args`` (kubectl arguments) is a safe read."""
    if not args or not all(isinstance(a, str) for a in args):
        raise UnsafeCommand("Empty or malformed command")
    verb = args[0]
    if verb == "rollout":
        if len(args) < 2 or args[1] not in ROLLOUT_OK:
            raise UnsafeCommand("Only 'rollout status' and 'rollout history' are allowed")
    elif verb == "config":
        if len(args) < 2 or args[1] not in CONFIG_OK:
            raise UnsafeCommand("Only 'config current-context' / 'get-contexts' are allowed")
    elif verb not in ALLOWED_VERBS:
        raise UnsafeCommand(f"'{verb}' is not a read-only command")
    for a in args[1:]:
        if a.split("=", 1)[0] in FORBIDDEN_FLAGS:
            raise UnsafeCommand(f"Flag {a!r} is not allowed")
        if a.startswith("--dry-run"):
            continue
    if verb in ("get", "describe", "top") and len(args) > 1 and not args[1].startswith("-"):
        if SECRET_RESOURCE.search(args[1]):
            raise UnsafeCommand("Reading Secrets is not allowed")
    return True


_SENSITIVE = r"[A-Za-z0-9_.-]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIAL)[A-Za-z0-9_.-]*"
_PATTERNS = [
    (re.compile(r'("name"\s*:\s*"' + _SENSITIVE + r'"\s*,\s*"value"\s*:\s*)"[^"]*"', re.I), r'\1"[REDACTED]"'),
    (re.compile(r"(\b" + _SENSITIVE + r"\b\s*[:=]\s*)(?!\[REDACTED\])\S+", re.I), r"\1[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)(authorization:\s*)\S+(\s+\S+)?"), r"\1[REDACTED]"),
    (re.compile(r"(://)[^/\s:@]+:[^/\s@]+@"), r"\1[REDACTED]@"),
]


def redact(text):
    """Mask obvious credentials before text is written to a report or log."""
    if not text:
        return text
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text
