# ============================================================
#  infrastructure/logging/setup.py
# ============================================================

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
import re
from pathlib import Path
from typing import Optional

from infrastructure.paths import get_user_log_dir


class SecretRedactingFilter(logging.Filter):
    """
    Security filter for log records to automatically redact tokens, passwords, and API keys.
    """
    def __init__(self, secrets_to_mask: list[str] = None):
        super().__init__()
        self.secrets = set()
        if secrets_to_mask:
            for s in secrets_to_mask:
                self.register_secret(s)

        # Common API key and token regex patterns with replacement values
        self.pattern_rules: list[tuple[re.Pattern, str]] = [
            (re.compile(r'(\d{8,12}:[A-Za-z0-9_-]{35})'), "[REDACTED_API_KEY]"),
            (re.compile(r'(AIza[A-Za-z0-9_-]{30,})'), "[REDACTED_API_KEY]"),
            (re.compile(r'(sk-ant-[A-Za-z0-9_-]{20,})'), "[REDACTED_API_KEY]"),
            (re.compile(r'(sk-[A-Za-z0-9_-]{20,})'), "[REDACTED_API_KEY]"),
            (re.compile(r'(Bearer\s+)[A-Za-z0-9._~+/-]+=*', re.IGNORECASE), r"\g<1>[REDACTED]"),
            # Double-quoted assignments containing whitespace
            (re.compile(r'((?:api[_-]?key|secret|token|password|auth)[\'"]?\s*[:=]\s*")([^"]+)(")', re.IGNORECASE), r"\g<1>[REDACTED]\g<3>"),
            # Single-quoted assignments containing whitespace
            (re.compile(r"((?:api[_-]?key|secret|token|password|auth)[\'\"]?\s*[:=]\s*')([^']+)(')", re.IGNORECASE), r"\g<1>[REDACTED]\g<3>"),
            # Unquoted assignments
            (re.compile(r'((?:api[_-]?key|secret|token|password|auth)[\'"]?\s*[:=]\s*)([^\s\'",]+)', re.IGNORECASE), r"\g<1>[REDACTED]"),
        ]

    def register_secret(self, secret: str) -> None:
        """Register an arbitrary secret string to be explicitly redacted."""
        if secret and isinstance(secret, str) and len(secret.strip()) > 3:
            self.secrets.add(secret.strip())

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.args and isinstance(record.msg, str):
            record.msg = self._sanitize(record.msg)
        elif record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._sanitize(v) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._sanitize(v) if isinstance(v, str) else v for v in record.args)
        return True

    def _sanitize(self, text: str) -> str:
        if not isinstance(text, str):
            return text
        result = text
        for secret in self.secrets:
            if secret in result:
                result = result.replace(secret, "[REDACTED]")
        for pattern, replacement in self.pattern_rules:
            result = pattern.sub(replacement, result)
        return result


class RedactingFormatter(logging.Formatter):
    """Security-focused logging Formatter that guarantees rendered messages and

    exception tracebacks (exc_info / exc_text) are thoroughly redacted before being
    written to destination streams or files.
    """

    def __init__(self, redactor: SecretRedactingFilter, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return self.redactor._sanitize(formatted)

    def formatException(self, ei) -> str:
        formatted = super().formatException(ei)
        return self.redactor._sanitize(formatted)


def setup_logging(
    log_level: int = logging.INFO,
    log_dir: Optional[str | Path] = None,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """
    Initializes structured logging with rotating file and console handlers.
    Places rotating log files in the cross-platform user data directory by default.
    """
    if log_dir is None:
        log_dir = get_user_log_dir()
    else:
        log_dir = Path(log_dir)

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "polpot.log"

    # Collect sensitive credentials for the redaction filter
    known_secrets = []
    try:
        import config
        for attr in ["BOT_TOKEN", "DB_PASS", "DB_USER"]:
            val = getattr(config, attr, None)
            if val and isinstance(val, str):
                known_secrets.append(val)
    except Exception:
        pass

    redacting_filter = SecretRedactingFilter(known_secrets)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    formatter = RedactingFormatter(
        redacting_filter,
        fmt="%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Attach or reconfigure rotating file handler for target log_file
    existing_file_handlers = [h for h in root_logger.handlers if isinstance(h, RotatingFileHandler)]
    has_target_handler = False
    for h in existing_file_handlers:
        if getattr(h, "baseFilename", None) == str(log_file.resolve()):
            has_target_handler = True
        else:
            root_logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    if not has_target_handler:
        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redacting_filter)
        root_logger.addHandler(file_handler)

    # Attach console handler if not present
    has_console = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        for h in root_logger.handlers
    )
    if not has_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(redacting_filter)
        root_logger.addHandler(console_handler)

    logger = logging.getLogger("polpot")
    logger.info("Logging infrastructure initialized. Log file: %s", log_file)
    return logger


def get_logger(name: str = "polpot") -> logging.Logger:
    """Retrieves a named logger instance."""
    return logging.getLogger(name)
