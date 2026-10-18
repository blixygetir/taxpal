"""Tiny PDF and DOCX writers for fake statements (sample data and tests). No dependencies.
Real statements are messier; these just need to be real files the importer can open."""

import io
import zipfile
from xml.sax.saxutils import escape

LINES_PER_PAGE = 60


def _pdf_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(lines: list[str]) -> bytes:
    """Monospaced text, one line per row, split into pages."""
    pages = [lines[i:i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)] or [[]]
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"]
    kids = []
    for page in pages:
        body = "BT /F1 9 Tf 11 TL 36 770 Td " + " ".join(f"({_pdf_text(l)}) Tj T*" for l in page) + " ET"
        objects.append(f"<< /Length {len(body.encode('latin-1'))} >>\nstream\n{body}\nendstream")
        content_id = len(objects)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>")
        kids.append(len(objects))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def write_docx(paragraphs: list[str], table: list[list[str]] | None = None, after: list[str] = ()) -> bytes:
    """Paragraphs, then an optional table (first row = header), then more paragraphs."""
    para = lambda t: f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(t)}</w:t></w:r></w:p>"
    cell = lambda t: f"<w:tc><w:p><w:r><w:t xml:space=\"preserve\">{escape(t)}</w:t></w:r></w:p></w:tc>"
    body = "".join(para(p) for p in paragraphs)
    if table:
        body += "<w:tbl>" + "".join("<w:tr>" + "".join(cell(c) for c in row) + "</w:tr>" for row in table) + "</w:tbl>"
    body += "".join(para(p) for p in after)
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:body>{body}</w:body></w:document>")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   "</Relationships>")
        z.writestr("word/document.xml", document)
    return out.getvalue()
