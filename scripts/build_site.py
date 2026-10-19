"""Build the hosted website: the UI plus the Python API as a zip that runs in the browser (Pyodide).

    uv run python scripts/build_site.py          # writes dist/
    python -m http.server -d dist 8080           # try it at http://localhost:8080

dist/ is plain static files; any static host serves it.
"""
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "taxpal"
SAMPLE = ROOT / "data" / "sample"
MARKER = '<meta name="taxpal-backend" content="browser">'


def build(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(PKG / "web", out)
    index = out / "index.html"
    html = index.read_text()
    html = html.replace('<meta charset="utf-8">', f'<meta charset="utf-8">\n  {MARKER}', 1)
    # /docs is served by FastAPI's server, which the hosted site doesn't have
    html = html.replace('<div class="drawer-foot muted small">For developers: <a href="/docs" target="_blank" rel="noopener">API docs</a></div>\n', "")
    index.write_text(html)
    with zipfile.ZipFile(out / "taxpal-py.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(PKG.rglob("*")):
            rel = f.relative_to(PKG)
            if f.is_file() and rel.parts[0] != "web" and "__pycache__" not in rel.parts:
                z.write(f, Path("src/taxpal") / rel)
        for f in sorted(SAMPLE.glob("*.csv")):  # for "Load sample data"
            z.write(f, Path("data/sample") / f.name)
    (out / ".nojekyll").touch()  # GitHub Pages: serve files as-is
    print(f"Built {out.relative_to(ROOT)}/ ({sum(f.stat().st_size for f in out.rglob('*') if f.is_file()) // 1024} KB)")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist")
