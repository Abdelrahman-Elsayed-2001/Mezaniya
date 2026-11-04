"""Write docs/whats_new.md (Arabic) and docs/whats_new.<code>.md (other languages) from the release
notes in the language files (whats_new.v<version> in app/i18n/languages/<code>.json), the same
texts the About page shows. Run it after editing the notes:

    python docs/tools/whats_new.py           # writes the files
    python docs/tools/whats_new.py --check   # exit 1 if a file is out of date (tests run this)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import about, i18n  # noqa: E402

DOCS = ROOT / "docs"
DEFAULT = "ar"


def file_for(code: str) -> Path:
    return DOCS / ("whats_new.md" if code == DEFAULT else f"whats_new.{code}.md")


def render(code: str, codes: list[str]) -> str:
    t = lambda key, **kw: i18n.t(key, code, **kw)        # noqa: E731
    others = " · ".join(f"[{i18n.language_name(c)}]({file_for(c).name})" for c in codes if c != code)
    out = [f"# {t('about.whats_new_in', name=t('app.title'))}", ""]
    if others:
        out += [others, ""]
    for r in about.release_notes(code):
        out += [f"## {t('about.release', version=r['label'])}", ""]
        out += [f"- {line}" for line in r["notes"]] + [""]
    return "\n".join(out)


def build() -> dict[Path, str]:
    i18n.use_database(None)                              # the language files, not someone's edits
    i18n.reload()
    codes = [c for c in i18n.languages()]
    return {file_for(c): render(c, codes) for c in codes}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    stale = []
    for path, text in build().items():
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                stale.append(path)
        else:
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")
    for path in stale:
        print(f"out of date: {path.relative_to(ROOT)} (run python docs/tools/whats_new.py)")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
