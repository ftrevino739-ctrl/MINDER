"""Pure safety helpers for MINDER's browser controller."""
from __future__ import annotations
import re
from urllib.parse import urlsplit

_HOST_PORT = re.compile(
    r"^(?:localhost|(?:[A-Za-z0-9-]+\.)*[A-Za-z0-9-]+|\d{1,3}(?:\.\d{1,3}){3}|\[[0-9A-Fa-f:]+\]):\d+(?:/|$)",
    re.IGNORECASE,
)

_SENSITIVE_ACTION = re.compile(
    r"(?<![\w])(?:send|submit|post|publish|buy|purchase|pay|payment|transfer|"
    r"delete|remove|cancel|book|reserve|subscribe|unsubscribe|place\s+order|"
    r"confirm|enviar|mandar|publicar|comprar|pagar|transferir|eliminar|borrar|"
    r"cancelar|reservar|suscribir|ordenar)(?![\w])",
    re.IGNORECASE,
)


def normalize_browser_url(value: str) -> str:
    """Normalize a website address while blocking local and active URI schemes."""
    if not isinstance(value, str):
        raise ValueError("A web address must be text.")
    raw = value.strip()
    if not raw:
        return "about:blank"
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError("The web address contains control characters.")
    if raw.lower() == "about:blank":
        return "about:blank"

    if "://" not in raw:
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw) and not _HOST_PORT.match(raw):
            raise ValueError(
                "Only HTTP and HTTPS addresses are allowed; local files and "
                "special browser schemes are blocked."
            )
        host_part = re.split(r"[/\?#]", raw, maxsplit=1)[0]
        if "." not in host_part and ":" not in host_part and host_part.lower() != "localhost":
            raw += ".com"
        raw = "https://" + raw

    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid web address: {exc}") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError(
            "Only HTTP and HTTPS addresses are allowed; local files and "
            "special browser schemes are blocked."
        )
    if not hostname or any(char.isspace() for char in hostname):
        raise ValueError("The web address must include a valid website host.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Web addresses containing embedded credentials are blocked.")
    return raw


def requires_human_approval(*descriptions: str) -> bool:
    """Identify browser controls that may submit external or destructive actions."""
    return any(
        _SENSITIVE_ACTION.search(value or "")
        for value in descriptions
        if isinstance(value, str)
    )
