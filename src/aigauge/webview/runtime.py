"""Lazy, guarded access to QtWebEngine.

QtWebEngine is Chromium, and it wants a GL context the moment it initialises.
On a machine with no GLX/EGL — a headless X11 display, an XRDP session, a VM
with no GPU — that initialisation fails hard and takes the process with it
(issue #7).

The gauge itself is plain QtWidgets drawn with QPainter and needs no GPU at
all, and providers backed by an API key (Copilot, OpenRouter) never open a
browser. So WebEngine is imported on first use rather than at startup, and
only after a cheap GL probe says a context is actually obtainable. On a box
without one the app still starts, still refreshes its API-key providers, and
reports a clear reason on the providers that do need a browser.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("aigauge.webview.runtime")

_ENV_FORCE = "AIGAUGE_FORCE_WEBENGINE"

# Cached (available, reason) — the probe cost is small but the answer cannot
# change within a process, and a failed import must not be retried per refresh.
_probe: tuple[bool, str] | None = None


class WebEngineUnavailable(RuntimeError):
    """QtWebEngine cannot be used in this session."""


def _gui_application_ready() -> bool:
    """Whether a QGuiApplication exists yet.

    QOpenGLContext.create() segfaults outright without one, so this must be
    checked before probing — a hard crash here would defeat the entire point
    of the probe.
    """
    try:
        from PyQt6.QtGui import QGuiApplication
    except ImportError:  # pragma: no cover - QtGui is always present
        return False
    return QGuiApplication.instance() is not None


def _gl_context_available() -> tuple[bool, str]:
    """Whether a GL context can be created, without touching Chromium.

    Probing with Qt's own GL classes is safe: they report failure by return
    value, whereas letting WebEngine discover the same thing aborts the
    process.
    """
    try:
        from PyQt6.QtGui import QOpenGLContext
    except ImportError as exc:  # pragma: no cover - QtGui is always present
        return False, f"QtGui unavailable: {exc}"
    try:
        context = QOpenGLContext()
        if context.create() and context.isValid():
            return True, ""
    except Exception as exc:  # noqa: BLE001 - probe must never raise onward
        return False, f"OpenGL probe failed: {exc}"
    return False, (
        "no OpenGL context is available in this session (no GLX/EGL). "
        "Providers that sign in through a browser need one; API-key "
        "providers do not."
    )


def probe() -> tuple[bool, str]:
    """Return ``(available, reason)`` for QtWebEngine, caching the result."""
    global _probe
    if _probe is not None:
        return _probe

    if os.environ.get(_ENV_FORCE) == "1":
        log.info("webengine probe bypassed via %s=1", _ENV_FORCE)
        _probe = (True, "")
        return _probe

    if not _gui_application_ready():
        # Deliberately not cached: the app asks this only after QApplication
        # exists, and caching "unavailable" here would poison every later
        # call for the life of the process.
        return False, "QGuiApplication has not been created yet"

    available, reason = _gl_context_available()
    if not available:
        log.warning("webengine unavailable: %s", reason)
        _probe = (False, reason)
        return _probe

    try:
        import PyQt6.QtWebEngineCore  # noqa: F401
        import PyQt6.QtWebEngineWidgets  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - a missing/broken build is fatal
        reason = f"QtWebEngine could not be loaded: {exc}"
        log.warning("webengine unavailable: %s", reason)
        _probe = (False, reason)
        return _probe

    _probe = (True, "")
    return _probe


def is_available() -> bool:
    return probe()[0]


def unavailable_reason() -> str:
    return probe()[1]


def require() -> None:
    """Raise :class:`WebEngineUnavailable` unless WebEngine can be used."""
    available, reason = probe()
    if not available:
        raise WebEngineUnavailable(reason)


def reset_probe_for_tests() -> None:
    global _probe
    _probe = None
