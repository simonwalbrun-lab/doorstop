# SPDX-License-Identifier: LGPL-3.0-only

"""Publish the differences between two revisions as HTML."""

import filecmp
import html
import os
import re
import shutil
from typing import List
from urllib.parse import quote

from doorstop import common
from doorstop.core.publishers.html import HtmlPublisher
from doorstop.core.vcs import git

log = common.logger(__name__)

STYLE = """<style>
div.diff-added, div.diff-removed, div.diff-changed { border-left: 4px solid; padding-left: 0.75em; }
div.diff-added { border-color: #1a7f37; background-color: #e6ffec; text-decoration: underline; }
div.diff-removed { border-color: #cf222e; background-color: #ffebe9; text-decoration: line-through; }
div.diff-changed { border-color: #bf8700; }
del.diff, div.diff-del { background-color: #ffebe9; text-decoration: line-through; }
ins.diff, div.diff-ins { background-color: #e6ffec; text-decoration: underline; }
.diff-missing { font-style: italic; border: 1px dashed; padding: 0 0.3em; }
.diff-summary { border: 1px solid #d0d7de; padding: 0.5em 1em; margin-bottom: 1em; }
@media print { div.diff-added, div.diff-removed, div.diff-changed, del.diff, ins.diff, div.diff-del, div.diff-ins { print-color-adjust: exact; -webkit-print-color-adjust: exact; } }
</style>"""
IMAGE = re.compile(r"(!\[[^\]]*\]\(\s*)([^)\s]+)([^)]*\))")
REMOTE = re.compile(r"^([a-zA-Z][\w+.-]*:|//)")
# Heading marks may lose their space on the old side: git gives the
# whitespace between a context and a changed word to the new side.
PREFIX = re.compile(r"^(\s*(?:#+\s*|[-*+]\s+|\d+\.\s+|>\s?)*)")
ATTRS = re.compile(r"\s*\{#[^}]*\}")
OPAQUE = re.compile(
    r"^\s*(```|~~~|\||\$\$|@startuml"
    r"|</?(div|table|pre|p|ul|ol|dl|blockquote|h[1-6]|hr|figure|details|style)\b)"
)
FENCE = re.compile(r"^\s*(```|~~~)")


def _blocks(lines):
    """Split lines into blocks at blank lines, keeping fenced blocks whole."""
    blocks: List[list] = []
    block: list = []
    fenced = False
    for line in lines:
        text = line if isinstance(line, str) else _side(line, "-") + _side(line, "+")
        if FENCE.match(text):
            fenced = not fenced
        if not text.strip() and not fenced:
            if block:
                blocks.append(block)
            block = []
        else:
            block.append(line)
    if block:
        blocks.append(block)
    return blocks


def _words(lines):
    """Get the words of each block, ignoring whitespace and wrapping."""
    return [tuple(" ".join(block).split()) for block in _blocks(lines)]


def _side(segments, skip):
    """Get one side of a word-diff line: skip '+' for old, '-' for new."""
    return "".join(text for op, text in segments if op != skip)


def _opaque(block):
    """Determine if a block can only be replaced as a whole."""
    return any(OPAQUE.match(line) for line in block)


def _tag(op, text):
    """Wrap text in a deleted or inserted mark, keeping outer whitespace."""
    core = text.strip()
    if not core:
        return text
    tag = "del" if op == "-" else "ins"
    lead, trail = text[: len(text) - len(text.lstrip())], text[len(text.rstrip()) :]
    return '{}<{t} class="diff">{}</{t}>{}'.format(lead, core, trail, t=tag)


def _line(segments):
    """Mark the changes of one word-diff line, outside its structure."""
    attrs = ATTRS.search(_side(segments, "-")) or ATTRS.search(_side(segments, "+"))
    suffix = attrs.group(0) if attrs else ""
    segments = [(op, ATTRS.sub("", text)) for op, text in segments]
    segments = [(op, text) for op, text in segments if text]
    old, new = _side(segments, "+"), _side(segments, "-")
    old_prefix, new_prefix = PREFIX.match(old).group(1), PREFIX.match(new).group(1)  # type: ignore
    first_op, first_text = segments[0] if segments else (" ", "")
    if old_prefix == new_prefix and (
        not new_prefix or (first_op == " " and first_text.startswith(new_prefix))
    ):
        out = [text if op == " " else _tag(op, text) for op, text in segments]
        return "".join(out).rstrip() + suffix
    prefix = new_prefix if new.strip() else old_prefix
    old_rest, new_rest = old[len(old_prefix) :], new[len(new_prefix) :]
    return (prefix + _tag("-", old_rest) + _tag("+", new_rest)).rstrip() + suffix


def _div(kind, block):
    """Wrap lines in a division of one diff kind."""
    return (
        ['<div class="diff-{}" markdown="1">'.format(kind), ""] + block + ["", "</div>"]
    )


def mark(lines):
    """Turn word-diff lines into markdown lines with <del>/<ins> marks.

    :param lines: output of :func:`doorstop.core.vcs.git.word_diff`

    :return: list of markdown lines

    """
    out: List[str] = []
    for block in _blocks(lines):
        old = [_side(line, "+") for line in block if any(op != "+" for op, _ in line)]
        new = [_side(line, "-") for line in block if any(op != "-" for op, _ in line)]
        if all(op == " " for line in block for op, _ in line):
            out.extend(new)
        elif _opaque(old) or _opaque(new):
            if old:
                out.extend(_div("del", old) + [""])
            if new:
                out.extend(_div("ins", new) + [""])
            out.pop()
        else:
            out.extend(_line(line) for line in block)
        out.append("")
    return out


def _revision(revision):
    """Format a (name, commit) revision for the summary."""
    name, commit = revision
    if commit is None:
        return "<strong>{}</strong>".format(html.escape(name))
    return "<strong>{}</strong> (<code>{}</code>)".format(
        html.escape(name), commit[:12]
    )


class HtmlDiffPublisher(HtmlPublisher):
    """HTML publisher for the differences between two revisions."""

    def __init__(self, obj, ext, diff, changed_only=False, temp=None):
        super().__init__(obj, ext)
        self.diff = diff
        self.changed_only = changed_only
        self.temp = temp
        self.changes = {change.prefix: change for change in diff.documents}
        self.copies = {}  # old picture copies: path in diff-old/ -> source path
        self.missing = set()  # pictures already reported as missing

    def _entries(self, change):
        """Get the entries of a page that are shown, deciding matched ones."""
        for entry in change.entries:
            if entry.kind == "matched":
                old = self._lines_side(entry.old, True, linkify=True, to_html=True)
                new = self._lines_side(entry.new, False, linkify=True, to_html=True)
                same = _words(old) == _words(new)
                entry.kind = "unchanged" if same else "changed"
        if not self.changed_only:
            return change.entries
        return [e for e in change.entries if e.differs]

    def _summary(self, change=None):
        """Yield the summary block naming both revisions and the counts."""
        yield '<div class="diff-summary">'
        yield "<p>Changes from {} to {}</p>".format(
            _revision(self.diff.old), _revision(self.diff.new)
        )
        if change is not None:
            if any(e.differs for e in change.entries):
                yield "<p>{added} added, {removed} removed, {changed} changed</p>".format(
                    **change.counts
                )
            else:
                yield "<p>No changes.</p>"
        yield "</div>"
        yield ""

    def _item_lines(self, item, **kwargs):
        """Get the published markdown lines of one item."""
        return list(super()._lines_markdown([item], **kwargs))

    def _lines_side(self, item, old, **kwargs):
        """Get the published lines of one side of an entry, pictures rewritten."""
        return self._pictures(self._item_lines(item, **kwargs), item, old)

    def _lines_entry(self, entry, **kwargs):
        """Yield the markdown lines of one entry."""
        if entry.kind == "unchanged":
            yield from self._item_lines(entry.new, **kwargs)
            return
        if entry.kind == "changed":
            old = self._lines_side(entry.old, True, **kwargs)
            new = self._lines_side(entry.new, False, **kwargs)
            diff = git.word_diff("\n".join(old), "\n".join(new), self.temp)
            lines = mark(diff)
        elif entry.kind == "added":
            lines = self._lines_side(entry.new, False, **kwargs)
        else:
            lines = self._lines_side(entry.old, True, **kwargs)
        yield from _div(entry.kind, lines)
        yield ""

    def _pictures(self, lines, item, old):
        """Point changed old pictures at copies and replace missing pictures.

        :param lines: published lines of one side of an entry
        :param item: the item the lines were rendered from
        :param old: True for the old side

        """

        def replace(match):
            path = match.group(2)
            if REMOTE.match(path):
                return match.group(0)
            source = os.path.normpath(os.path.join(item.document.path, path))
            if not os.path.isfile(source):
                if (path, str(item.uid)) not in self.missing:
                    self.missing.add((path, str(item.uid)))
                    log.warning("picture not found: %s (%s)", path, item.uid)
                return '<span class="diff-missing">picture not found: {}</span>'.format(
                    html.escape(path)
                )
            new = self.changes[str(item.document.prefix)].new
            if not old or new is None:
                return match.group(0)
            target = os.path.join(new.path, path)
            if os.path.isfile(target) and filecmp.cmp(source, target, shallow=False):
                return match.group(0)
            try:
                name = os.path.relpath(source, item.root)
            except ValueError:
                name = ".."
            # shortcut: a picture outside the repository keeps its link to the
            # new version; copy it under another name if anyone links there.
            if name.startswith(".."):
                return match.group(0)
            name = name.replace(os.sep, "/")
            self.copies[name] = source
            return match.group(1) + "diff-old/" + quote(name) + match.group(3)

        return [IMAGE.sub(replace, line) for line in lines]

    def concludePublish(self):
        """Copy the old versions of changed pictures next to the pages."""
        folder = os.path.join(os.path.dirname(self.documentPath), "diff-old")
        for name, source in self.copies.items():
            path = os.path.join(folder, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copyfile(source, path)

    def _lines_markdown(self, obj, **kwargs):
        """Yield the markdown lines of a diff page."""
        change = self.changes[str(obj.prefix)]
        entries = self._entries(change)
        yield STYLE
        yield ""
        yield from self._summary(change)
        for entry in entries:
            yield from self._lines_entry(entry, **kwargs)

    def table_of_contents(self, linkify=None, obj=None):
        """Generate the table of contents over the shown entries."""
        change = self.changes[str(obj.prefix)]
        items = [e.display for e in self._entries(change)]
        return super().table_of_contents(linkify, items)

    def lines_index(self, filenames, tree=None):
        """Yield lines of HTML for the index, with the summary and counts."""
        yield STYLE
        yield from self._summary()
        for revision, side in ((self.diff.old, "old"), (self.diff.new, "new")):
            if self.diff.documents and all(
                getattr(c, side) is None for c in self.diff.documents
            ):
                yield "<p>{} has no documents.</p>".format(html.escape(revision[0]))
        yield '<table class="table">'
        yield (
            "<thead><tr><th>Document</th><th>Status</th><th>Added</th>"
            "<th>Removed</th><th>Changed</th></tr></thead>"
        )
        yield "<tbody>"
        for change in self.diff.documents:
            self._entries(change)
            yield (
                '<tr><td><a href="documents/{p}.html">{p}</a></td><td>{s}</td>'
                "<td>{added}</td><td>{removed}</td><td>{changed}</td></tr>"
            ).format(p=change.prefix, s=self._status(change), **change.counts)
        yield "</tbody>"
        yield "</table>"
        yield from super().lines_index(filenames, tree=tree)

    @staticmethod
    def _status(change):
        """Get the status of a document for the index."""
        if change.new is None:
            return "removed"
        if change.old is None:
            return "added"
        if any(e.differs for e in change.entries):
            return "changed"
        return "unchanged"
