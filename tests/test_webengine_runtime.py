"""Regression tests for issue #7 — the app must start without a GPU.

QtWebEngine is Chromium and needs a GL context to initialise. Importing it on
a session that has none (headless X11, XRDP, GPU-less VM) aborts the process,
which is why the gauge — plain QtWidgets, no GL required — must not pull it in
at startup.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from aigauge.webview import runtime

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    runtime.reset_probe_for_tests()
    yield
    runtime.reset_probe_for_tests()


def test_importing_the_app_does_not_load_webengine():
    """The guard that actually protects GL-less machines.

    Run in a subprocess: this test session has almost certainly imported
    QtWebEngine already via the webview tests, so checking sys.modules in-process
    would prove nothing.
    """
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "import aigauge.app;"
        "print(sorted(m for m in sys.modules if 'WebEngine' in m))"
        % str(REPO_ROOT / "src")
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", (
        "importing aigauge.app pulled in QtWebEngine, which aborts on a "
        f"session with no GL context: {result.stdout}"
    )


def test_probe_reports_unavailable_without_a_gl_context(qapp, monkeypatch):
    monkeypatch.delenv("AIGAUGE_FORCE_WEBENGINE", raising=False)
    monkeypatch.setattr(
        runtime, "_gl_context_available", lambda: (False, "no GLX/EGL here")
    )

    assert runtime.is_available() is False
    assert "no GLX/EGL here" in runtime.unavailable_reason()
    with pytest.raises(runtime.WebEngineUnavailable, match="no GLX/EGL here"):
        runtime.require()


def test_probe_is_cached(qapp, monkeypatch):
    monkeypatch.delenv("AIGAUGE_FORCE_WEBENGINE", raising=False)
    calls = []

    def _probe():
        calls.append(1)
        return False, "nope"

    monkeypatch.setattr(runtime, "_gl_context_available", _probe)

    runtime.is_available()
    runtime.is_available()
    runtime.unavailable_reason()

    assert len(calls) == 1, "the GL probe must not run on every refresh"


def test_probe_refuses_to_run_before_a_qguiapplication_exists(monkeypatch):
    """QOpenGLContext.create() segfaults with no QGuiApplication.

    Found by running this on a real GL-less Linux session: asking the probe
    early took the whole process down, which is exactly the crash the probe is
    supposed to prevent.
    """
    monkeypatch.delenv("AIGAUGE_FORCE_WEBENGINE", raising=False)
    monkeypatch.setattr(runtime, "_gui_application_ready", lambda: False)
    monkeypatch.setattr(
        runtime,
        "_gl_context_available",
        lambda: pytest.fail("must not touch GL before QGuiApplication exists"),
    )

    assert runtime.is_available() is False
    assert "QGuiApplication" in runtime.unavailable_reason()


def test_missing_qguiapplication_answer_is_not_cached(qapp, monkeypatch):
    """A pre-startup 'no' must not poison the answer for the whole process."""
    monkeypatch.delenv("AIGAUGE_FORCE_WEBENGINE", raising=False)
    ready = [False]
    monkeypatch.setattr(runtime, "_gui_application_ready", lambda: ready[0])
    monkeypatch.setattr(runtime, "_gl_context_available", lambda: (True, ""))

    assert runtime.is_available() is False  # asked too early

    ready[0] = True
    assert runtime.is_available() is True  # re-probes once the app exists


def test_force_env_var_bypasses_the_probe(monkeypatch):
    monkeypatch.setenv("AIGAUGE_FORCE_WEBENGINE", "1")
    monkeypatch.setattr(
        runtime,
        "_gl_context_available",
        lambda: pytest.fail("probe should be bypassed"),
    )

    assert runtime.is_available() is True
    assert runtime.unavailable_reason() == ""
