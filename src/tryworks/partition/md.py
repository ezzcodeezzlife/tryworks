"""Partition Markdown by rendering it to HTML and partitioning that.

The renderer covers the Markdown people write in practice: ATX and setext headings, paragraphs,
emphasis, inline code, links, images, nested lists, block quotes, fenced and indented code,
horizontal rules, pipe tables and raw HTML blocks. Unlike Python-Markdown, a list or heading may
interrupt a paragraph without a blank line before it, as in CommonMark.
"""

from __future__ import annotations

import html
import re
from typing import IO, Any, Optional

from tryworks.partition.common import FileType, exactly_one, get_last_modified_date, read_text

_ATX_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_HR_RE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_FENCE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})[ \t]*([^`\s]*)[^`]*$")
_LIST_RE = re.compile(r"^( *)([-*+]|\d{1,9}[.)])[ \t]+(.*)$")
_EMPTY_LIST_RE = re.compile(r"^( *)([-*+]|\d{1,9}[.)])[ \t]*$")
_QUOTE_RE = re.compile(r"^ {0,3}> ?(.*)$")
_TABLE_SEP_RE = re.compile(r"^ *\|? *:?-+:? *(?:\| *:?-+:? *)*\|? *$")
_HTML_BLOCK_RE = re.compile(
    r"^ {0,3}<(?:/?(?:address|article|aside|blockquote|body|center|details|dialog|div|dl|fieldset|"
    r"figcaption|figure|footer|form|h[1-6]|header|hr|html|main|nav|ol|p|pre|section|summary|table|"
    r"tbody|td|tfoot|th|thead|tr|ul)\b|!--)",
    re.IGNORECASE,
)

_CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1", re.DOTALL)
_INLINE_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s+[^<>]*)?/?>")
_AUTOLINK_RE = re.compile(r"<((?:https?|ftp)://[^\s>]+|mailto:[^\s>]+)>")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"([^\"]*)\")?\s*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*<?([^)\s>]*)>?(?:\s+\"([^\"]*)\")?\s*\)")
_STRONG_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_EM_STAR_RE = re.compile(r"(?<![*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![*\w])", re.DOTALL)
_EM_UNDER_RE = re.compile(r"(?<![_\w])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![_\w])", re.DOTALL)
_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")
_HARD_BREAK_RE = re.compile(r"(?: {2,}|\\)\n")


class _Inline:
    """Render inline Markdown to HTML, protecting code spans and raw tags with placeholders."""

    def __init__(self) -> None:
        self._stash: list[str] = []

    def _hold(self, rendered: str) -> str:
        self._stash.append(rendered)
        return f"\x00{len(self._stash) - 1}\x00"

    def render(self, text: str) -> str:
        text = _CODE_SPAN_RE.sub(lambda m: self._hold(f"<code>{html.escape(m.group(2).strip())}</code>"), text)
        text = _ESCAPE_RE.sub(lambda m: self._hold(html.escape(m.group(1))), text)
        text = _AUTOLINK_RE.sub(
            lambda m: self._hold(f'<a href="{html.escape(m.group(1))}">{html.escape(m.group(1))}</a>'), text
        )
        text = _INLINE_TAG_RE.sub(lambda m: self._hold(m.group(0)), text)
        text = _IMAGE_RE.sub(
            lambda m: self._hold(f'<img alt="{html.escape(m.group(1))}" src="{html.escape(m.group(2))}" />'),
            text,
        )
        text = _LINK_RE.sub(lambda m: self._hold(f'<a href="{html.escape(m.group(2))}">') + m.group(1) + self._hold("</a>"), text)
        text = html.escape(text, quote=False)
        text = _STRONG_RE.sub(r"<strong>\2</strong>", text)
        text = _EM_STAR_RE.sub(r"<em>\1</em>", text)
        text = _EM_UNDER_RE.sub(r"<em>\1</em>", text)
        text = _HARD_BREAK_RE.sub("<br />\n", text)
        while "\x00" in text:
            text = re.sub(r"\x00(\d+)\x00", lambda m: self._stash[int(m.group(1))], text)
        return text


def _split_row(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", row)]


def _interrupts_paragraph(line: str) -> bool:
    return bool(
        _ATX_RE.match(line)
        or _HR_RE.match(line)
        or _FENCE_RE.match(line)
        or _QUOTE_RE.match(line)
        or _LIST_RE.match(line)
        or _HTML_BLOCK_RE.match(line)
    )


def _strip_indent(line: str, width: int) -> str:
    removed = 0
    while removed < width and line[:1] == " ":
        line = line[1:]
        removed += 1
    return line


def markdown_to_html(text: str) -> str:
    """Render Markdown text to an HTML fragment."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4).split("\n")
    return "\n".join(_render_blocks(lines))


def _render_blocks(lines: list[str]) -> list[str]:
    out: list[str] = []
    inline = _Inline()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence:
            marker, lang = fence.group(2), fence.group(3)
            body = []
            i += 1
            while i < n and not lines[i].strip().startswith(marker[0] * len(marker)):
                body.append(_strip_indent(lines[i], len(fence.group(1))))
                i += 1
            i += 1
            cls = f' class="language-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(body))}\n</code></pre>")
            continue

        if line.startswith("    "):
            body = []
            while i < n and (lines[i].startswith("    ") or not lines[i].strip()):
                body.append(lines[i][4:])
                i += 1
            while body and not body[-1].strip():
                body.pop()
            out.append(f"<pre><code>{html.escape(chr(10).join(body))}\n</code></pre>")
            continue

        atx = _ATX_RE.match(line)
        if atx:
            level = len(atx.group(1))
            out.append(f"<h{level}>{inline.render((atx.group(2) or '').strip())}</h{level}>")
            i += 1
            continue

        if _HR_RE.match(line) and not _LIST_RE.match(line):
            out.append("<hr />")
            i += 1
            continue

        if _QUOTE_RE.match(line):
            inner = []
            while i < n and lines[i].strip():
                quoted = _QUOTE_RE.match(lines[i])
                inner.append(quoted.group(1) if quoted else lines[i])
                i += 1
            out.append("<blockquote>\n" + "\n".join(_render_blocks(inner)) + "\n</blockquote>")
            continue

        if _HTML_BLOCK_RE.match(line):
            block = []
            while i < n and lines[i].strip():
                block.append(lines[i])
                i += 1
            out.append("\n".join(block))
            continue

        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]) and "-" in lines[i + 1]:
            header = _split_row(line)
            i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(_split_row(lines[i]))
                i += 1
            head_html = "".join(f"<th>{inline.render(c)}</th>" for c in header)
            body_html = "".join(
                "<tr>" + "".join(f"<td>{inline.render(c)}</td>" for c in (row + [""] * len(header))[: len(header)]) + "</tr>"
                for row in rows
            )
            out.append(f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>")
            continue

        if _LIST_RE.match(line):
            html_list, i = _render_list(lines, i)
            out.append(html_list)
            continue

        para = [line.strip()]
        i += 1
        while i < n and lines[i].strip():
            setext = _SETEXT_RE.match(lines[i])
            if setext:
                level = 1 if setext.group(1)[0] == "=" else 2
                out.append(f"<h{level}>{inline.render(' '.join(para))}</h{level}>")
                para = []
                i += 1
                break
            if _interrupts_paragraph(lines[i]):
                break
            para.append(lines[i].strip() if not lines[i].endswith("  ") else lines[i].lstrip())
            i += 1
        if para:
            out.append(f"<p>{inline.render(chr(10).join(para))}</p>")
    return out


def _render_list(lines: list[str], i: int) -> tuple[str, int]:
    first = _LIST_RE.match(lines[i])
    assert first is not None
    base_indent = len(first.group(1))
    ordered = first.group(2)[0].isdigit()
    tag = "ol" if ordered else "ul"
    items: list[list[str]] = []
    n = len(lines)
    while i < n:
        match = _LIST_RE.match(lines[i])
        if match and len(match.group(1)) == base_indent and match.group(2)[0].isdigit() == ordered:
            content_indent = len(lines[i]) - len(match.group(3))
            items.append([match.group(3)])
            i += 1
            while i < n:
                nxt = lines[i]
                if not nxt.strip():
                    if i + 1 < n and (len(lines[i + 1]) - len(lines[i + 1].lstrip())) > base_indent:
                        items[-1].append("")
                        i += 1
                        continue
                    break
                indent = len(nxt) - len(nxt.lstrip())
                sibling = _LIST_RE.match(nxt)
                if sibling and indent <= base_indent:
                    break
                if indent <= base_indent and _interrupts_paragraph(nxt):
                    break
                items[-1].append(_strip_indent(nxt, min(indent, content_indent)))
                i += 1
            continue
        break
    inline = _Inline()
    rendered = []
    for item in items:
        head, rest = item[0], item[1:]
        split_at = next((k for k, line in enumerate(rest) if _LIST_RE.match(line) or not line.strip()), len(rest))
        text_lines = [head] + [line.strip() for line in rest[:split_at]]
        nested = rest[split_at:]
        body = inline.render("\n".join(text_lines))
        if any(line.strip() for line in nested):
            body += "\n" + "\n".join(_render_blocks(nested))
        rendered.append(f"<li>{body}</li>")
    return f"<{tag}>\n" + "\n".join(rendered) + f"\n</{tag}>", i


def partition_md(
    filename: Optional[str] = None,
    file: Optional[IO[Any]] = None,
    text: Optional[str] = None,
    url: Optional[str] = None,
    metadata_filename: Optional[str] = None,
    metadata_last_modified: Optional[str] = None,
    languages: Optional[list[str]] = None,
    **kwargs: Any,
) -> list:
    """Partition a Markdown document; element rules are those of ``partition_html``."""
    from tryworks.partition.html import fetch_url, partition_html

    exactly_one(filename=filename, file=file, text=text, url=url)
    if url is not None:
        source = fetch_url(url, headers=kwargs.pop("headers", {}), ssl_verify=kwargs.pop("ssl_verify", True))[1]
    else:
        source = read_text(filename=filename, file=file, text=text, encoding=kwargs.pop("encoding", None))
    last_modified = metadata_last_modified or (get_last_modified_date(filename) if filename else None)
    return partition_html(
        text=markdown_to_html(source),
        metadata_filename=metadata_filename or filename,
        metadata_file_type=FileType.MD,
        metadata_last_modified=last_modified,
        languages=languages,
        url=url,
        **kwargs,
    )
