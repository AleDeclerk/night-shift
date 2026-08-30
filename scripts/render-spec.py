#!/usr/bin/env python3
"""Render a spec from Markdown to one self-contained HTML file.

Usage: python3 scripts/render-spec.py docs/specs/2026-08-30-design.md
The HTML goes to build/ and it is not in the repository. The Markdown file
stays the only source.
"""
import pathlib
import sys

import markdown

CSS = """
:root {
  --bg: #fbfaf8; --fg: #23201c; --muted: #6b645c; --rule: #e3ded6;
  --accent: #9a5b2a; --card: #ffffff; --code-bg: #f2efea;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16130f; --fg: #e9e3da; --muted: #9c9389; --rule: #2e2921;
    --accent: #d98f52; --card: #1d1914; --code-bg: #221d17;
  }
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 17px/1.65 ui-serif, Georgia, "Iowan Old Style", serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { display: grid; grid-template-columns: 220px minmax(0, 68ch); gap: 3.5rem;
        max-width: 1040px; margin: 0 auto; padding: 4rem 2rem 8rem; }
nav { position: sticky; top: 4rem; align-self: start; font: 13px/1.5 ui-sans-serif, -apple-system, system-ui, sans-serif; }
nav .label { text-transform: uppercase; letter-spacing: .09em; font-size: 11px;
             color: var(--muted); margin-bottom: .9rem; }
nav ul { list-style: none; margin: 0; padding: 0; }
nav li { margin: 0 0 .45rem; }
nav a { color: var(--muted); text-decoration: none; display: block;
        border-left: 2px solid var(--rule); padding-left: .7rem; }
nav a:hover { color: var(--accent); border-left-color: var(--accent); }
main { min-width: 0; }
h1 { font-size: 2.3rem; line-height: 1.15; margin: 0 0 .4rem; letter-spacing: -.02em; }
h2 { font-size: 1.35rem; margin: 3.2rem 0 .9rem; padding-top: 1.1rem;
     border-top: 1px solid var(--rule); letter-spacing: -.01em; }
h2:first-of-type { border-top: 0; padding-top: 0; }
h3 { font-size: 1.05rem; margin: 2rem 0 .6rem; }
p, li { color: var(--fg); }
li { margin: .35rem 0; }
strong { font-weight: 650; }
a { color: var(--accent); }
code { font: .86em ui-monospace, "SF Mono", Menlo, monospace;
       background: var(--code-bg); padding: .12em .38em; border-radius: 4px; }
pre { background: var(--card); border: 1px solid var(--rule); border-radius: 8px;
      padding: 1.1rem 1.2rem; overflow-x: auto; font-size: 12.5px; line-height: 1.45; }
pre code { background: none; padding: 0; font-size: inherit; }
blockquote { margin: 0; padding-left: 1rem; border-left: 3px solid var(--rule); color: var(--muted); }
.meta { font: 13px/1.5 ui-sans-serif, -apple-system, system-ui, sans-serif;
        color: var(--muted); margin: 0 0 3rem; }
.meta .pill { display: inline-block; background: var(--accent); color: var(--bg);
              border-radius: 100px; padding: .16rem .7rem; font-weight: 600;
              font-size: 11px; text-transform: uppercase; letter-spacing: .06em; }
@media (max-width: 860px) {
  .wrap { grid-template-columns: minmax(0, 1fr); gap: 0; padding: 2.5rem 1.25rem 5rem; }
  nav { position: static; margin-bottom: 2.5rem; }
}
"""

TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head><body>
<div class="wrap">
<nav><div class="label">Sections</div>{toc}</nav>
<main>{body}</main>
</div>
</body></html>
"""


def render(src: pathlib.Path, out_dir: pathlib.Path) -> pathlib.Path:
    md = markdown.Markdown(extensions=["fenced_code", "tables", "toc", "attr_list"])
    body = md.convert(src.read_text())
    title = src.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (src.stem + ".html")
    out.write_text(TEMPLATE.format(title=title, css=CSS, toc=md.toc, body=body))
    return out


if __name__ == "__main__":
    source = pathlib.Path(sys.argv[1])
    target = render(source, pathlib.Path("build"))
    print(target.resolve())
