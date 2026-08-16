"""Provider verification targets.

Kept free of any QtWebEngine import so callers can ask *whether* a provider
has a verification target without pulling Chromium — and its GL requirement —
into the process (issue #7).
"""

from __future__ import annotations

# Load the provider's actual usage page and check for text that only renders for
# a signed-in user. If the cookie is good the page renders inline; if not it
# either redirects to /login or shows an interstitial.
VERIFY_TARGETS = {
    "claude": (
        "https://claude.ai/new#settings/usage",
        "(() => document.body && document.body.innerText.includes('Plan usage limits'))()",
    ),
    "codex": (
        "https://chatgpt.com/codex/cloud/settings/analytics#personal-usage",
        r"""(() => {
          const visibleText = el => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
          const text = visibleText(document.body);
          if (/Weekly usage limit/i.test(text) && /\d+(?:\.\d+)?\s*%/.test(text)) {
            return true;
          }
          const labels = Array.from(document.querySelectorAll('button,a,[role="tab"],[role="button"]'));
          const label = labels.find(el => visibleText(el).toLowerCase() === 'personal usage');
          const target = label && (label.closest('button,a,[role="tab"],[role="button"]') || label);
          if (target) target.click();
          return false;
        })()""",
    ),
    "opencode_go": (
        "https://opencode.ai/workspace/wrk_01KX3HT8MFWCMHR2289KGPZ1RD/go",
        r"""(() => {
          const visibleText = el => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
          const text = visibleText(document.body).toLowerCase();
          const workspacePath = /^\/workspace\/[^/]+(?:\/|$)/.test(location.pathname);
          const shellMarkers = ['usage', 'api keys', 'members', 'billing', 'settings'];
          return location.hostname === 'opencode.ai' && workspacePath &&
            shellMarkers.every(marker => text.includes(marker));
        })()""",
    ),
}
