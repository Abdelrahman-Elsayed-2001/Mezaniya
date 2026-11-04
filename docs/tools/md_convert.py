"""Turn a Markdown file and its images into a Word (.docx) and a PDF file.

    python docs/tools/md_convert.py docs/user_guide.md                 # user_guide.docx + user_guide.pdf
    python docs/tools/md_convert.py docs/whats_new.md --format docx
    python docs/tools/md_convert.py docs/user_guide.md --out-dir Output/docs --html

Needs: pip install -r docs/tools/requirements.txt  (markdown, python-docx).
The PDF is printed by a Chromium browser: Playwright's if it's installed, otherwise Microsoft
Edge or Google Chrome found on this computer (every Windows 10/11 has Edge), or --browser PATH.

Handles what the guides use: headings (with links to them), paragraphs, bold/italic/code,
links, images (also <img width=...>), bullet and numbered lists (nested), tables, code blocks,
block quotes and GitHub notes (> [!NOTE], [!TIP], [!WARNING], [!IMPORTANT], [!CAUTION]).
Right-to-left is detected from the text (Arabic, Hebrew, Persian…), or set with --dir.
"""
from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

try:
    import markdown
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor
except ImportError as exc:      # pragma: no cover - a message instead of a traceback
    sys.exit(f"Missing library ({exc.name}). Run: pip install -r docs/tools/requirements.txt")

RTL_CHARS = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")
LTR_CHARS = re.compile(r"[A-Za-z]")
ALERTS = {"NOTE": ("ملاحظة", "Note", "#1d4ed8", "EFF6FF"), "TIP": ("تلميح", "Tip", "#047857", "ECFDF5"),
          "IMPORTANT": ("مهم", "Important", "#7c3aed", "F5F3FF"), "WARNING": ("تنبيه", "Warning", "#b45309", "FFFBEB"),
          "CAUTION": ("تحذير", "Caution", "#b91c1c", "FEF2F2")}
FONT = "Segoe UI"                 # Latin text
FONT_CS = "Segoe UI"              # Arabic ("complex script") text; Tahoma/Arial also work
MONO = "Consolas"


# ---------------------------------------------------------------- Markdown → HTML

def github_slug(text: str, separator: str = "-") -> str:
    """Heading ids as GitHub makes them, so links like (#2-التثبيت-والتشغيل) work in every output."""
    text = unicodedata.normalize("NFC", html.unescape(re.sub(r"<[^>]+>", "", text))).strip().lower()
    text = "".join(ch for ch in text if ch in " -_" or unicodedata.category(ch)[0] in "LNM")
    return text.replace(" ", separator)


def to_html(md_text: str) -> str:
    # GitHub notes: "> [!NOTE]" first line of a quote → <blockquote class="alert note">
    md_text = re.sub(r"^(>\s*)\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$",
                     lambda m: f"{m.group(1)}ALERT::{m.group(2)}\n>", md_text, flags=re.M)
    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "toc", "md_in_html"],
                           extension_configs={"toc": {"slugify": github_slug}})
    body = md.convert(md_text)
    body = re.sub(r"<blockquote>\s*<p>ALERT::(\w+)\s*</p>", lambda m: f'<blockquote class="alert {m.group(1).lower()}">', body)
    # two notes in a row come out as one quote: split it again
    body = re.sub(r"<p>ALERT::(\w+)\s*</p>", lambda m: f'</blockquote>\n<blockquote class="alert {m.group(1).lower()}">', body)
    return body


def detect_dir(text: str) -> str:
    return "rtl" if len(RTL_CHARS.findall(text)) > len(LTR_CHARS.findall(text)) else "ltr"


# ---------------------------------------------------------------- a small HTML tree

class Node:
    def __init__(self, tag: str, attrs: dict | None = None, parent: "Node | None" = None):
        self.tag, self.attrs, self.parent, self.children = tag, attrs or {}, parent, []

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)


class TreeBuilder(HTMLParser):
    VOID = {"img", "br", "hr", "input", "meta", "link"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = Node("root")

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        if tag not in self.VOID:
            self.cur = node

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(body: str) -> Node:
    b = TreeBuilder()
    b.feed(body)
    return b.root


# ---------------------------------------------------------------- HTML tree → Word

def _set(el, name: str, **attrs):
    child = el.find(qn(name))
    if child is None:
        child = OxmlElement(name)
        el.append(child)
    for k, v in attrs.items():
        child.set(qn(k), str(v))
    return child


def _shade(el_pr, fill: str):
    _set(el_pr, "w:shd", **{"w:val": "clear", "w:color": "auto", "w:fill": fill})


class WordWriter:
    def __init__(self, base: Path, rtl: bool):
        self.base, self.rtl = base, rtl
        self.doc = Document()
        self.bookmark_id = 0
        sec = self.doc.sections[0]
        sec.page_width, sec.page_height = Cm(21), Cm(29.7)
        sec.left_margin = sec.right_margin = Cm(2)
        sec.top_margin = sec.bottom_margin = Cm(2)
        self.content_width = sec.page_width - sec.left_margin - sec.right_margin
        if rtl:
            _set(sec._sectPr, "w:bidi")
        for style in self.doc.styles:
            if style.type == 1 and style.font is not None:           # paragraph styles
                rpr = style.element.get_or_add_rPr()
                _set(rpr, "w:rFonts", **{"w:ascii": FONT, "w:hAnsi": FONT, "w:cs": FONT_CS})
                if rtl:
                    _set(style.element.get_or_add_pPr(), "w:bidi")
        normal = self.doc.styles["Normal"]
        normal.font.size = Pt(11)
        _set(normal.element.get_or_add_rPr(), "w:szCs", **{"w:val": 22})
        normal.paragraph_format.space_after = Pt(6)
        for level, size in ((1, 20), (2, 16), (3, 13), (4, 12)):
            st = self.doc.styles[f"Heading {level}"]
            st.font.size = Pt(size)
            st.font.color.rgb = RGBColor(0x0F, 0x3D, 0x3A)
            st.font.italic = False
            _set(st.element.get_or_add_rPr(), "w:szCs", **{"w:val": size * 2})

    # ---- paragraphs and runs

    def paragraph(self, style: str | None = None, ltr: bool = False):
        p = self.doc.add_paragraph(style=style)
        self.direction(p, rtl=self.rtl and not ltr)
        return p

    def direction(self, p, rtl: bool):
        ppr = p._p.get_or_add_pPr()
        bidi = ppr.find(qn("w:bidi"))
        if rtl and bidi is None:
            _set(ppr, "w:bidi")
        elif not rtl and bidi is not None:
            ppr.remove(bidi)
        if not rtl and self.rtl:                                     # code blocks inside an RTL guide
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    def run(self, p, text: str, bold=False, italic=False, code=False, color: str | None = None):
        if not text:
            return None
        r = p.add_run(text)
        r.bold, r.italic = bold or None, italic or None
        rpr = r._r.get_or_add_rPr()
        if bold:
            _set(rpr, "w:bCs")
        if code:
            r.font.name = MONO
            _set(rpr, "w:rFonts", **{"w:ascii": MONO, "w:hAnsi": MONO, "w:cs": MONO})
            _shade(rpr, "F1F5F9")
            r.font.size = Pt(9.5)
        if color:
            r.font.color.rgb = RGBColor.from_string(color.lstrip("#").upper())
        if RTL_CHARS.search(text):
            _set(rpr, "w:rtl")
        return r

    def inline(self, p, node: Node, bold=False, italic=False, code=False, link: str | None = None):
        for c in node.children:
            if isinstance(c, str):
                text = c if code else re.sub(r"\s+", " ", c)
                if link:
                    self.hyperlink(p, text, link, bold)
                else:
                    self.run(p, text, bold, italic, code)
                continue
            tag = c.tag
            if tag in ("strong", "b"):
                self.inline(p, c, True, italic, code, link)
            elif tag in ("em", "i"):
                self.inline(p, c, bold, True, code, link)
            elif tag == "code":
                self.inline(p, c, bold, italic, True, link)
            elif tag == "a":
                self.inline(p, c, bold, italic, code, c.attrs.get("href", ""))
            elif tag == "br":
                p.add_run().add_break()
            elif tag == "img":
                self.image(p, c)
            else:
                self.inline(p, c, bold, italic, code, link)

    def hyperlink(self, p, text: str, href: str, bold=False):
        link = OxmlElement("w:hyperlink")
        if href.startswith("#"):
            link.set(qn("w:anchor"), self.bookmark_name(href[1:]))
        else:
            rid = p.part.relate_to(href, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                                   is_external=True)
            link.set(qn("r:id"), rid)
        r = self.run(p, text, bold=bold, color="#0F766E")
        r.underline = True
        p._p.remove(r._r)
        link.append(r._r)
        p._p.append(link)

    def bookmark_name(self, slug: str) -> str:
        # Word bookmark names: ≤40 characters, start with a letter, no spaces
        return "h_" + re.sub(r"\W", "_", slug)[:38]

    def bookmark(self, p, slug: str):
        self.bookmark_id += 1
        start = OxmlElement("w:bookmarkStart")
        start.set(qn("w:id"), str(self.bookmark_id))
        start.set(qn("w:name"), self.bookmark_name(slug))
        end = OxmlElement("w:bookmarkEnd")
        end.set(qn("w:id"), str(self.bookmark_id))
        p._p.insert(0, start)
        p._p.append(end)

    def image(self, p, node: Node, max_width=None):
        src = node.attrs.get("src", "")
        path = (self.base / src).resolve()
        if not path.is_file():
            self.run(p, f"[{node.attrs.get('alt') or src}]", italic=True)
            return
        max_width = max_width or self.content_width
        px = image_width(path)                          # optional (Pillow): the true pixel size
        width_px = px / 2 if px else None               # the screenshots are taken at 2× scale
        if node.attrs.get("width", "").isdigit():
            width_px = int(node.attrs["width"])
        width = min(max_width, Cm(width_px / 96 * 2.54)) if width_px else max_width
        p.add_run().add_picture(str(path), width=int(width))
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # ---- blocks

    def blocks(self, node: Node, level: int = 0, quote: str | None = None):
        for c in node.children:
            if isinstance(c, str):
                if c.strip():
                    p = self.paragraph()
                    self.run(p, c.strip())
                continue
            self.block(c, level, quote)

    def block(self, c: Node, level: int = 0, quote: str | None = None):
        tag = c.tag
        if re.fullmatch(r"h[1-6]", tag):
            n = min(int(tag[1]), 4)
            p = self.paragraph(f"Heading {n}")
            self.inline(p, c)
            if c.attrs.get("id"):
                self.bookmark(p, c.attrs["id"])
        elif tag == "p":
            only_images = all(isinstance(x, Node) and x.tag == "img" or (isinstance(x, str) and not x.strip())
                              for x in c.children)
            p = self.paragraph()
            if quote:
                self.quote_style(p, quote)
            self.inline(p, c)
            if only_images:
                p.paragraph_format.keep_with_next = False
        elif tag in ("ul", "ol"):
            self.list(c, tag, level, quote)
        elif tag == "pre":
            for line in c.text().rstrip("\n").split("\n"):
                p = self.paragraph(ltr=True)
                p.paragraph_format.space_after = Pt(0)
                _shade(p._p.get_or_add_pPr(), "F1F5F9")
                self.run(p, line or " ", code=True)
            self.paragraph().paragraph_format.space_after = Pt(0)
        elif tag == "table":
            self.table(c)
        elif tag == "blockquote":
            kind = (c.attrs.get("class", "") or "").replace("alert", "").strip().upper() or "QUOTE"
            if kind in ALERTS:
                label_ar, label_en, color, fill = ALERTS[kind]
                p = self.paragraph()
                self.quote_style(p, kind)
                self.run(p, label_ar if self.rtl else label_en, bold=True, color=color)
            self.blocks(c, level, kind)
        elif tag == "hr":
            p = self.paragraph()
            bdr = _set(p._p.get_or_add_pPr(), "w:pBdr")
            _set(bdr, "w:bottom", **{"w:val": "single", "w:sz": 6, "w:space": 1, "w:color": "CBD5E1"})
        elif tag == "img":
            p = self.paragraph()
            self.image(p, c)
        else:
            self.blocks(c, level, quote)

    def quote_style(self, p, kind: str):
        fill = ALERTS.get(kind, (None, None, "#64748b", "F8FAFC"))[3]
        color = ALERTS.get(kind, (None, None, "#64748b", None))[2].lstrip("#")
        ppr = p._p.get_or_add_pPr()
        _shade(ppr, fill)
        bdr = _set(ppr, "w:pBdr")
        _set(bdr, "w:right" if self.rtl else "w:left", **{"w:val": "single", "w:sz": 24, "w:space": 8, "w:color": color})
        p.paragraph_format.space_after = Pt(2)

    def list(self, node: Node, tag: str, level: int, quote: str | None):
        style = ("List Number" if tag == "ol" else "List Bullet") + (f" {level + 1}" if level else "")
        for li in node.children:
            if not isinstance(li, Node) or li.tag != "li":
                continue
            p = self.paragraph(style)
            if quote:
                self.quote_style(p, quote)
            inline = Node("span")
            for x in li.children:
                if isinstance(x, Node) and x.tag in ("ul", "ol"):
                    continue
                if isinstance(x, Node) and x.tag == "p":     # loose lists: <li><p>…</p></li>
                    inline.children.extend(x.children + [" "])
                else:
                    inline.children.append(x)
            self.inline(p, inline)
            for x in li.children:
                if isinstance(x, Node) and x.tag in ("ul", "ol"):
                    self.list(x, x.tag, min(level + 1, 2), quote)

    def table(self, node: Node):
        rows = [r for r in self._find(node, "tr")]
        if not rows:
            return
        cols = max(len([c for c in r.children if isinstance(c, Node) and c.tag in ("td", "th")]) for r in rows)
        table = self.doc.add_table(rows=0, cols=cols)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        if self.rtl:
            _set(table._tbl.tblPr, "w:bidiVisual")
        cell_width = self.content_width // cols
        for r_index, r in enumerate(rows):
            cells = [c for c in r.children if isinstance(c, Node) and c.tag in ("td", "th")]
            row = table.add_row()
            for i, cell_node in enumerate(cells):
                cell = row.cells[i]
                p = cell.paragraphs[0]
                self.direction(p, self.rtl)
                header = cell_node.tag == "th"
                if header:
                    _shade(cell._tc.get_or_add_tcPr(), "E6F4F1")
                for img in self._find(cell_node, "img"):
                    self.image(p, img, max_width=cell_width - Cm(0.4))
                    img.tag = "skip"
                self.inline(p, cell_node, bold=header)
            if r_index == 0 and any(c.tag == "th" for c in cells):
                _set(row._tr.get_or_add_trPr(), "w:tblHeader")
        self.paragraph().paragraph_format.space_after = Pt(2)

    def _find(self, node: Node, tag: str):
        for c in node.children:
            if isinstance(c, Node):
                if c.tag == tag:
                    yield c
                else:
                    yield from self._find(c, tag)

    def save(self, out: Path, title: str):
        self.doc.core_properties.title = title
        self.doc.save(str(out))


def write_docx(body: str, base: Path, rtl: bool, out: Path, title: str):
    w = WordWriter(base, rtl)
    w.blocks(parse(body))
    w.save(out, title)


# ---------------------------------------------------------------- HTML → PDF

CSS = """
@page { size: A4; margin: 18mm 16mm; }
html { font-family: "Segoe UI", Tahoma, "Noto Sans Arabic", Arial, sans-serif; font-size: 10.5pt;
       color: #1f2933; line-height: 1.65; }
body { margin: 0; }
h1 { font-size: 22pt; color: #0f3d3a; border-bottom: 2px solid #0f766e; padding-bottom: 6px; margin-top: 0; }
h2 { font-size: 16pt; color: #0f3d3a; margin-top: 26px; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px;
     break-after: avoid; }
h2:not(:first-of-type) { break-before: page; }
h3 { font-size: 12.5pt; color: #0f3d3a; margin-top: 18px; break-after: avoid; }
h4 { font-size: 11pt; break-after: avoid; }
a { color: #0f766e; text-decoration: none; }
img { max-width: 100%; border: 1px solid #e2e8f0; border-radius: 6px; break-inside: avoid; }
p:has(> img:only-child) { text-align: center; }
code { font-family: Consolas, "DejaVu Sans Mono", monospace; font-size: 9pt; background: #f1f5f9;
       padding: 1px 4px; border-radius: 4px; direction: ltr; unicode-bidi: embed; }
pre { background: #f1f5f9; padding: 10px 12px; border-radius: 6px; direction: ltr; text-align: left;
      white-space: pre-wrap; break-inside: avoid; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 14px; break-inside: auto; }
th, td { border: 1px solid #cbd5e1; padding: 5px 8px; vertical-align: top; text-align: start; }
th { background: #e6f4f1; }
tr { break-inside: avoid; }
td img { border: 1px solid #e2e8f0; }
blockquote { margin: 12px 0; padding: 8px 14px; background: #f8fafc; border-inline-start: 4px solid #94a3b8;
             border-radius: 4px; break-inside: avoid; }
blockquote p { margin: 4px 0; }
blockquote.alert::before { display: block; font-weight: bold; margin-bottom: 2px; }
"""


def alert_css(rtl: bool) -> str:
    out = []
    for kind, (ar, en, color, fill) in ALERTS.items():
        out.append(f"blockquote.{kind.lower()} {{ background: #{fill}; border-color: {color}; }}"
                   f"blockquote.{kind.lower()}::before {{ content: '{ar if rtl else en}'; color: {color}; }}")
    return "\n".join(out)


class _Match:
    def __init__(self, m):
        self.m = m

    def group(self, i):
        return self.m.group(0) if i == 0 else self.m.group(1)


def image_width(path: Path) -> int | None:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size[0]
    except Exception:
        return None


def write_html(body: str, base: Path, rtl: bool, out: Path, title: str):
    lang = "ar" if rtl else "en"
    # images by absolute file URL, so the HTML may live anywhere
    def fix(m):
        tag, src = m.group(0), m.group(2)
        if re.match(r"^[a-z]+:", src):
            return tag
        path = (base / src).resolve()
        tag = tag.replace(f'src="{src}"', f'src="{path.as_uri()}"')
        if " width=" not in tag:                     # 2× screenshots: show them at their real size
            px = image_width(path)
            if px:
                tag = tag.replace("<img", f'<img width="{px // 2}"', 1)
        return tag
    body = re.sub(r'<img[^>]*\ssrc="([^"]*)"[^>]*>', lambda m: fix(_Match(m)), body)
    out.write_text(f"""<!doctype html><html lang="{lang}" dir="{'rtl' if rtl else 'ltr'}"><head><meta charset="utf-8">
<title>{html.escape(title)}</title><style>{CSS}{alert_css(rtl)}</style></head><body>{body}</body></html>""",
                   encoding="utf-8")


def find_browser(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    candidates = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(env)
        if root:
            candidates += [Path(root) / "Microsoft/Edge/Application/msedge.exe",
                           Path(root) / "Google/Chrome/Application/chrome.exe"]
    candidates += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                   Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    for c in candidates:
        if c.is_file():
            return str(c)
    for name in ("msedge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def write_pdf(html_file: Path, out: Path, browser: str | None):
    if not browser:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                b = pw.chromium.launch()
                page = b.new_page()
                page.goto(html_file.resolve().as_uri(), wait_until="load")
                page.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True,
                         display_header_footer=True, header_template="<span></span>",
                         footer_template='<div style="font-size:8px;width:100%;text-align:center;color:#64748b">'
                                         '<span class="pageNumber"></span> / <span class="totalPages"></span></div>',
                         margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"})
                b.close()
            return
        except ImportError:
            pass
        except Exception as exc:          # Playwright installed without its browser, …
            print(f"Playwright could not print ({exc}); trying Edge/Chrome.", file=sys.stderr)
        browser = find_browser(None)
    if not browser:
        sys.exit("No browser found to print the PDF. Install Microsoft Edge or Google Chrome, or pass --browser PATH.")
    out = out.resolve()
    out.unlink(missing_ok=True)
    errors = []
    # The new headless mode first, then the old one. On Windows msedge.exe may return before the
    # PDF is on disk (another process writes it), so wait for the file after each try.
    for mode in ("--headless=new", "--headless"):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
            cmd = [browser, mode, "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                   "--disable-extensions", f"--user-data-dir={profile}", "--no-pdf-header-footer",
                   "--print-to-pdf-no-header", "--virtual-time-budget=10000", f"--print-to-pdf={out}",
                   html_file.resolve().as_uri()]
            try:
                done = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                if done.returncode:
                    errors.append(f"{mode}: exit code {done.returncode}")
                tail = (done.stderr or "").strip().splitlines()[-3:]
                errors += [f"{mode}: {line}" for line in tail]
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(f"{mode}: {exc}")
            if wait_for_file(out, seconds=30):
                return
    details = "\n  ".join(errors) or "no output"
    sys.exit(f"The browser did not write {out}.\n  browser: {browser}\n  {details}\n"
             "Try another browser with --browser PATH (chrome.exe or msedge.exe), or: "
             "pip install playwright && python -m playwright install chromium")


def wait_for_file(path: Path, seconds: float) -> bool:
    """True once `path` exists and its size has stopped changing."""
    import time
    deadline, last = time.monotonic() + seconds, -1
    while time.monotonic() < deadline:
        size = path.stat().st_size if path.is_file() else -1
        if size > 0 and size == last:
            return True
        last = size
        time.sleep(0.5)
    return path.is_file() and path.stat().st_size > 0


# ---------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert a Markdown file and its images to .docx and .pdf")
    ap.add_argument("source", type=Path, help="the .md file (images are found relative to it)")
    ap.add_argument("--format", choices=("both", "docx", "pdf"), default="both")
    ap.add_argument("--out-dir", type=Path, help="where to write (default: next to the .md file)")
    ap.add_argument("--dir", choices=("auto", "rtl", "ltr"), default="auto", help="text direction (default: from the text)")
    ap.add_argument("--html", action="store_true", help="also keep the intermediate .html file")
    ap.add_argument("--browser", help="path to msedge.exe / chrome.exe for the PDF")
    args = ap.parse_args(argv)

    src = args.source
    if not src.is_file():
        ap.error(f"{src} not found")
    md_text = src.read_text(encoding="utf-8")
    rtl = (detect_dir(md_text) if args.dir == "auto" else args.dir) == "rtl"
    title_match = re.search(r"^#\s+(.+)$", md_text, re.M)
    title = re.sub(r"[*_`]", "", title_match.group(1)).strip() if title_match else src.stem
    out_dir = args.out_dir or src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    body = to_html(md_text)
    base = src.parent.resolve()

    if args.format in ("both", "docx"):
        out = out_dir / f"{src.stem}.docx"
        write_docx(body, base, rtl, out, title)
        print(f"Word: {out}")
    if args.format in ("both", "pdf") or args.html:
        html_file = out_dir / f"{src.stem}.html"
        write_html(body, base, rtl, html_file, title)
        if args.format in ("both", "pdf"):
            out = out_dir / f"{src.stem}.pdf"
            write_pdf(html_file, out, args.browser)
            print(f"PDF:  {out}")
        if args.html:
            print(f"HTML: {html_file}")
        else:
            html_file.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
