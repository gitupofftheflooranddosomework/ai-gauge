#!/usr/bin/env python
"""Strip files PyInstaller collects but AI Gauge never uses.

`--collect-all PyQt6.QtWebEngineCore` rakes in everything shipped alongside
Qt's Chromium build: debug variants of every resource pack, the DevTools
front-end, Qt's UI translations for every language, and Chromium's locale
packs. Issue #7 asked, reasonably, why a small gauge is a 200 MB download.

Two tiers, because they are not equally free:

* **unreachable** (~89 MB) — debug resource packs and the DevTools front-end.
  The release variants sit right next to the debug ones and are the files
  actually loaded, and the app never opens an inspector. Nothing can observe
  their absence.
* **localisation** (~54 MB) — Qt's `.qm` files and Chromium's locale packs.
  Removing these does have an effect: on a non-English system the embedded
  sign-in browser's context menus and error pages, and Qt's stock dialog
  buttons, fall back to English. AI Gauge's own UI is English-only, so this is
  consistent rather than broken — but it is a real change, so
  `--keep-localisation` opts out.

Run after PyInstaller, before archiving:

    python tools/prune_bundle.py dist/ai-gauge
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# en-US is always kept: Chromium needs a loadable locale pak to fall back to,
# and removing every one of them stops the embedded browser from starting.
KEEP_LOCALES = {"en-US", "en-GB"}
KEEP_TRANSLATION_SUFFIXES = ("_en",)


def _iter_qt_dirs(root: Path, name: str):
    """Yield every Qt subdirectory called *name*.

    Globbed rather than hard-coded because the layout differs per platform:
    `_internal/PyQt6/Qt6/...` on Windows and Linux,
    `Contents/Frameworks/PyQt6/Qt6/...` inside a macOS .app.
    """
    yield from (p for p in root.rglob(name) if p.is_dir())


def _delete(path: Path, report: list[tuple[Path, int]]) -> None:
    try:
        if path.is_dir():
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            shutil.rmtree(path)
        else:
            size = path.stat().st_size
            path.unlink()
    except OSError as exc:
        print(f"  ! could not remove {path}: {exc}", file=sys.stderr)
        return
    report.append((path, size))


def prune_unreachable(root: Path, removed: list[tuple[Path, int]]) -> None:
    """Debug resource packs and the DevTools front-end — never loaded."""
    for resources in _iter_qt_dirs(root, "resources"):
        for pattern in ("*.debug.pak", "*.debug.bin"):
            for path in resources.glob(pattern):
                _delete(path, removed)
        for path in resources.glob("qtwebengine_devtools_resources*.pak"):
            _delete(path, removed)


def prune_localisation(root: Path, removed: list[tuple[Path, int]]) -> None:
    """Non-English Qt translations and Chromium locale packs."""
    for translations in _iter_qt_dirs(root, "translations"):
        for path in translations.glob("*.qm"):
            if not path.stem.endswith(KEEP_TRANSLATION_SUFFIXES):
                _delete(path, removed)

    for locales in _iter_qt_dirs(root, "qtwebengine_locales"):
        kept = 0
        for path in sorted(locales.glob("*.pak")):
            if path.stem in KEEP_LOCALES:
                kept += 1
                continue
            _delete(path, removed)
        if not kept:
            print(
                f"  ! {locales} has no en-US.pak; the embedded browser may "
                "fail to start",
                file=sys.stderr,
            )


def _report(removed: list[tuple[Path, int]], label: str) -> int:
    total = sum(size for _, size in removed)
    for path, size in sorted(removed, key=lambda item: -item[1])[:6]:
        print(f"    {size / 1_048_576:8.1f} MB  {path.name}")
    if len(removed) > 6:
        print(f"    ...and {len(removed) - 6} smaller files")
    print(f"  {label}: {len(removed)} files, {total / 1_048_576:.1f} MB")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="PyInstaller output directory")
    parser.add_argument(
        "--keep-localisation",
        action="store_true",
        help="keep Qt translations and Chromium locale packs",
    )
    args = parser.parse_args()

    root: Path = args.bundle
    if not root.exists():
        print(f"Bundle not found: {root}", file=sys.stderr)
        return 1

    before = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())

    unreachable: list[tuple[Path, int]] = []
    prune_unreachable(root, unreachable)
    _report(unreachable, "unreachable")

    if not args.keep_localisation:
        localisation: list[tuple[Path, int]] = []
        prune_localisation(root, localisation)
        _report(localisation, "localisation")

    after = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    print(
        f"Bundle {before / 1_048_576:.0f} MB -> {after / 1_048_576:.0f} MB "
        f"({(before - after) / 1_048_576:.0f} MB smaller)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
