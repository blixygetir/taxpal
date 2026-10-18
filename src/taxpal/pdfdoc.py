"""A small PDF writer for the summary: headings, wrapped text, bullet lists and tables
in Helvetica, US Letter, with page numbers. No dependencies beyond the font widths pypdf ships."""

import zlib

try:  # Adobe's core-font widths, used to wrap and right-align text
    from pypdf._codecs.core_font_metrics import CORE_FONT_METRICS as _METRICS
    _WIDTHS = {f: _METRICS[f].character_widths for f in ("Helvetica", "Helvetica-Bold")}
except Exception:  # pypdf moved them; fall back to an average width
    _WIDTHS = {"Helvetica": {"default": 556}, "Helvetica-Bold": {"default": 611}}

PAGE_W, PAGE_H = 612, 792
MARGIN = 54
WIDTH = PAGE_W - 2 * MARGIN
FONTS = {"regular": ("F1", "Helvetica"), "bold": ("F2", "Helvetica-Bold")}
TEXT = (0.11, 0.12, 0.14)
MUTED = (0.36, 0.40, 0.45)
ACCENT = (0.12, 0.44, 0.36)
RULE = (0.85, 0.87, 0.9)
SHADE = (0.95, 0.96, 0.97)
BOXES = {"ok": ((0.91, 0.96, 0.94), ACCENT), "warn": ((1.0, 0.96, 0.88), (0.42, 0.31, 0.0))}


def text_width(s: str, weight: str = "regular", size: float = 10) -> float:
    w = _WIDTHS[FONTS[weight][1]]
    return sum(w.get(ch, w.get("default", 556)) for ch in s) * size / 1000


def wrap(s: str, width: float, weight: str = "regular", size: float = 10) -> list[str]:
    lines = []
    for para in str(s).split("\n"):
        line = ""
        for word in para.split(" "):
            trial = f"{line} {word}" if line else word
            if text_width(trial, weight, size) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
            while text_width(line, weight, size) > width and len(line) > 1:  # a word longer than the line
                cut = len(line)
                while cut > 1 and text_width(line[:cut], weight, size) > width:
                    cut -= 1
                lines.append(line[:cut])
                line = line[cut:]
        lines.append(line)
    return lines


def _pdf_string(s: str) -> str:
    out = []
    for b in s.encode("cp1252", errors="replace"):
        c = chr(b)
        out.append("\\" + c if c in "\\()" else c if 32 <= b < 127 else f"\\{b:03o}")
    return "(" + "".join(out) + ")"


def _rgb(c, stroke=False) -> str:
    return f"{c[0]:.3f} {c[1]:.3f} {c[2]:.3f} {'RG' if stroke else 'rg'}"


class Document:
    def __init__(self, footer: str = ""):
        self.footer = footer
        self.pages: list[list[str]] = []
        self._new_page()

    def _new_page(self) -> None:
        self.ops: list[str] = []
        self.pages.append(self.ops)
        self.y = PAGE_H - MARGIN

    def _room(self, height: float) -> None:
        if self.y - height < MARGIN + 20:
            self._new_page()

    def _text(self, x: float, y: float, s: str, weight="regular", size=10, color=TEXT) -> None:
        self.ops.append(f"BT {_rgb(color)} /{FONTS[weight][0]} {size} Tf {x:.2f} {y:.2f} Td {_pdf_string(s)} Tj ET")

    def _rect(self, x, y, w, h, fill) -> None:
        self.ops.append(f"{_rgb(fill)} {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")

    def _line(self, x1, y, x2, color=RULE, width=0.6) -> None:
        self.ops.append(f"{_rgb(color, stroke=True)} {width} w {x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S")

    def space(self, h: float) -> None:
        self.y -= h

    def paragraph(self, s: str, size=10, weight="regular", color=TEXT, indent=0.0, leading=1.35) -> None:
        for line in wrap(s, WIDTH - indent, weight, size):
            self._room(size * leading)
            self.y -= size * leading
            self._text(MARGIN + indent, self.y + size * 0.25, line, weight, size, color)

    def title(self, s: str) -> None:
        self.paragraph(s, size=18, weight="bold", leading=1.2)
        self.space(4)

    def heading(self, s: str) -> None:
        self._room(110)  # keep a heading with what follows it
        self.space(14)
        self.paragraph(s, size=12.5, weight="bold", color=ACCENT)
        self.space(3)
        self._line(MARGIN, self.y, MARGIN + WIDTH)
        self.space(6)

    def subheading(self, s: str) -> None:
        self._room(50)
        self.space(6)
        self.paragraph(s, size=10.5, weight="bold")
        self.space(2)

    def box(self, s: str, kind: str = "warn") -> None:
        fill, color = BOXES[kind]
        lines = wrap(s, WIDTH - 20, "bold", 10)
        h = len(lines) * 13.5 + 14
        self._room(h)
        self._rect(MARGIN, self.y - h, WIDTH, h, fill)
        self._rect(MARGIN, self.y - h, 3, h, color)
        y = self.y - 7
        for line in lines:
            y -= 13.5
            self._text(MARGIN + 12, y + 3, line, "bold", 10, color)
        self.y -= h + 6

    def bullets(self, items: list) -> None:
        """Items are strings, or (string, [sub-items])."""
        for item in items:
            text, subs = item if isinstance(item, tuple) else (item, [])
            lines = wrap(text, WIDTH - 14, "regular", 10)
            for i, line in enumerate(lines):
                self._room(13.5)
                self.y -= 13.5
                if i == 0:
                    self._text(MARGIN + 2, self.y + 2.5, "•", "regular", 10, ACCENT)
                self._text(MARGIN + 14, self.y + 2.5, line)
            for sub in subs:
                self.paragraph(sub, size=9, color=MUTED, indent=14)
            self.space(2)

    def table(self, headers: list[str], rows: list[list], numeric: set[int] = frozenset(),
              bold_rows: set[int] = frozenset(), widths: list[float] | None = None) -> None:
        size, pad, lead = 9.5, 5, 12
        cols = len(headers)
        if widths is None:  # share the width by content, numbers keep their natural width
            natural = [max([text_width(str(h), "bold", size)] + [text_width(str(r[c]), "regular", size) for r in rows]) + 2 * pad
                       for c, h in enumerate(headers)]
            fixed = sum(natural[c] for c in numeric)
            flexible = [c for c in range(cols) if c not in numeric]
            spare = WIDTH - fixed
            flex_total = sum(natural[c] for c in flexible) or 1
            widths = [natural[c] if c in numeric else max(40, spare * natural[c] / flex_total) for c in range(cols)]
            if sum(widths) > WIDTH:
                widths = [w * WIDTH / sum(widths) for w in widths]
        else:
            widths = [w * WIDTH for w in widths]

        def draw_row(cells, weight, shade=None):
            wrapped = [wrap(str(c), widths[i] - 2 * pad, weight, size) for i, c in enumerate(cells)]
            h = max(len(w) for w in wrapped) * lead + 2 * pad - 2
            self._room(h + (lead + 2 * pad if shade else 0))  # a header needs a row under it
            if shade:
                self._rect(MARGIN, self.y - h, WIDTH, h, shade)
            x = MARGIN
            for i, lines in enumerate(wrapped):
                for j, line in enumerate(lines):
                    ty = self.y - pad - (j + 1) * lead + 3
                    tx = x + widths[i] - pad - text_width(line, weight, size) if i in numeric else x + pad
                    self._text(tx, ty, line, weight, size, TEXT)
                x += widths[i]
            self.y -= h
            self._line(MARGIN, self.y, MARGIN + WIDTH)

        draw_row(headers, "bold", SHADE)
        for n, row in enumerate(rows):
            before = len(self.pages)
            draw_row(row, "bold" if n in bold_rows else "regular")
            if len(self.pages) != before:  # moved to a new page mid-table: repeat the header above that row
                self.ops.clear()
                self.y = PAGE_H - MARGIN
                draw_row(headers, "bold", SHADE)
                draw_row(row, "bold" if n in bold_rows else "regular")
        self.space(4)

    def to_bytes(self) -> bytes:
        total = len(self.pages)
        objects = ["<< /Type /Catalog /Pages 2 0 R >>", None,
                   "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
                   "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
        kids = []
        for n, ops in enumerate(self.pages, start=1):
            self.ops = list(ops)
            self._line(MARGIN, MARGIN - 4, MARGIN + WIDTH)
            self._text(MARGIN, MARGIN - 16, self.footer, "regular", 8, MUTED)
            label = f"Page {n} of {total}"
            self._text(MARGIN + WIDTH - text_width(label, "regular", 8), MARGIN - 16, label, "regular", 8, MUTED)
            body = zlib.compress("\n".join(self.ops).encode("latin-1"))
            objects.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(body) + body + b"\nendstream")
            objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                           f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {len(objects)} 0 R >>")
            kids.append(len(objects))
        objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {total} >>"

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, obj in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + (obj if isinstance(obj, bytes) else obj.encode("latin-1")) + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
        out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
        out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)
