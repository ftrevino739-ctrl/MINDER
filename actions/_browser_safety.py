"""Pure safety helpers for MINDER's browser controller."""
from __future__ import annotations
import re
from urllib.parse import urlsplit

_HOST_PORT = re.compile(
    r"^(?:localhost|(?:[A-Za-z0-9-]+\.)*[A-Za-z0-9-]+|\d{1,3}(?:\.\d{1,3}){3}|\[[0-9A-Fa-f:]+\]):\d+(?:/|$)",
    re.IGNORECASE,
)

_SENSITIVE_ACTION = re.compile(
    r"(?<![\w])(?:send|submit|post|publish|upload|buy|purchase|pay|payment|"
    r"transfer|delete|remove|cancel|book|reserve|subscribe|unsubscribe|"
    r"place\s+order|checkout|confirm|approve|authorize|allow|install|"
    r"reset|revoke|share|connect|disconnect|save\s+changes|"
    r"enviar|mandar|publicar|subir|comprar|pagar|transferir|eliminar|borrar|"
    r"cancelar|reservar|suscribir|ordenar|confirmar|autorizar|permitir|"
    r"instalar|restablecer|revocar|compartir)(?![\w])",
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




_SAFE_SEARCH_SUBMITTER = re.compile(
    r"^(?:search|search now|find|find now|go|buscar|buscar ahora)$",
    re.IGNORECASE,
)


def requires_form_submission_approval(form_label: str, submitters: list[str]) -> bool:
    """Require approval for form submissions unless every submitter is a plain search."""
    labels = [label.strip() for label in (submitters or []) if label.strip()]
    if not labels:
        return True
    return not all(_SAFE_SEARCH_SUBMITTER.fullmatch(label) for label in labels)


_SAFE_SCREEN_TARGET = re.compile(
    r"^(?:home|back|forward|next|previous|menu|open menu|close menu|"
    r"search|new tab|play|pause|resume|volume up|volume down|mute|"
    r"full ?screen|fullscreen|minimi[sz]e|maximi[sz]e|task manager|"
    r"settings|open settings|show desktop|"
    r"inicio|atr[aá]s|adelante|siguiente|anterior|men[uú]|buscar|nueva pesta[nñ]a|"
    r"reproducir|pausar|reanudar|subir volumen|bajar volumen|silenciar|"
    r"pantalla completa|minimizar|maximizar|configuraci[oó]n|mostrar escritorio)$",
    re.IGNORECASE,
)


def requires_screen_click_approval(description: str) -> bool:
    """Unknown screen targets require approval; obvious navigation/media controls are exempt."""
    value = (description or "").strip()
    if not value or requires_human_approval(value):
        return True
    if _SAFE_SCREEN_TARGET.fullmatch(value):
        return False
    if re.fullmatch(r"(?:open|launch|start|abrir|iniciar)\s+[\w .+-]{2,80}", value, re.IGNORECASE):
        return False
    return True


def requires_human_approval(*descriptions: str) -> bool:
    """Identify browser controls that may submit external or destructive actions."""
    return any(
        _SENSITIVE_ACTION.search(value or "")
        for value in descriptions
        if isinstance(value, str)
    )
