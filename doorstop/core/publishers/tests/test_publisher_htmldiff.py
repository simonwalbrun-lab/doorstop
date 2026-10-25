# SPDX-License-Identifier: LGPL-3.0-only

"""Unit tests for the doorstop.core.publishers.htmldiff module."""

import os
import shutil
import tempfile
import unittest

from doorstop.core.differ import diff_trees, publish_diff
from doorstop.core.publishers.htmldiff import STYLE, HtmlDiffPublisher, mark
from doorstop.core.tests.helpers import on_error_with_retry
from doorstop.core.tests.test_differ import (
    PictureTestCase,
    ScenarioTestCase,
    build_revision,
)
from doorstop.core.vcs.git import word_diff


def div(kind, lines):
    """Get the lines of a diff division."""
    return (
        ['<div class="diff-{}" markdown="1">'.format(kind), ""] + lines + ["", "</div>"]
    )


class TestMark(unittest.TestCase):
    """Unit tests for turning git's word diff into marked lines."""

    def setUp(self):
        self.temp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp, onerror=on_error_with_retry)

    def mark(self, old, new):
        """Mark the changes between two lists of lines."""
        return mark(word_diff("\n".join(old), "\n".join(new), self.temp))

    def test_rewrap(self):
        """Verify a re-wrapped paragraph gets no marks."""
        old = ["## 1.1 REQ001 {#REQ001}", "", "A long sentence that", "wraps here."]
        new = ["## 1.1 REQ001 {#REQ001}", "", "A long sentence", "that wraps here."]
        self.assertEqual(new + [""], self.mark(old, new))

    def test_word(self):
        """Verify changed and inserted words are marked, outside list markers."""
        self.assertEqual(
            [
                'Start <del class="diff">slowly.</del><ins class="diff">fast.</ins>',
                "",
            ],
            self.mark(["Start slowly."], ["Start fast."]),
        )
        self.assertEqual(
            ['Start <ins class="diff">very</ins> quickly.', ""],
            self.mark(["Start quickly."], ["Start very quickly."]),
        )
        self.assertEqual(
            [
                '- <del class="diff">gone item</del>',
                "- kept",
                '- <ins class="diff">new item</ins>',
                "",
            ],
            self.mark(["- gone item", "- kept"], ["- kept", "- new item"]),
        )
        self.assertEqual(
            ["- first item", '- second <ins class="diff">changed</ins> item', ""],
            self.mark(
                ["- first item", "- second item"],
                ["- first item", "- second changed item"],
            ),
        )

    def test_fence(self):
        """Verify a changed fenced block, with a blank line, is one block."""
        old = ["```", "code a", "", "more", "```"]
        new = ["```", "code b", "", "more", "```"]
        self.assertEqual(
            div("del", old) + [""] + div("ins", new) + [""], self.mark(old, new)
        )

    def test_heading_level(self):
        """Verify a changed heading level is marked and the attribute list kept."""
        self.assertEqual(
            [
                '### <del class="diff">1.2 REQ003</del>'
                '<ins class="diff">1.2.1 REQ003</ins> {#REQ003}',
                "",
            ],
            self.mark(["## 1.2 REQ003 {#REQ003}"], ["### 1.2.1 REQ003 {#REQ003}"]),
        )

    def test_atomic_tokens(self):
        """Verify anchors, links and images are never split by a mark."""
        old = '*Links:* <a href="REQ.html#REQ001">REQ001 Title</a>, ![p](a.png)'
        new = '*Links:* <a href="REQ.html#REQ002">REQ002 Title</a>, ![p](b.png)'
        self.assertEqual(
            [
                '*Links:* <del class="diff"><a href="REQ.html#REQ001">REQ001 '
                'Title</a></del><ins class="diff"><a href="REQ.html#REQ002">'
                'REQ002 Title</a></ins>, <del class="diff">![p](a.png)</del>'
                '<ins class="diff">![p](b.png)</ins>',
                "",
            ],
            self.mark([old], [new]),
        )


class TestItemMarkup(ScenarioTestCase):
    """Unit tests for the markup of each kind of entry."""

    ENTRIES = {
        "REQ001": div(
            "changed",
            [
                "## 1.1 REQ001 {#REQ001}",
                "",
                'Start <ins class="diff">very</ins> quickly.',
                "",
                '*Child links:* <a href="SW.html#SW001">SW001</a>',
                "",
            ],
        )
        + [""],
        "REQ002": div("removed", ["## 1.2 REQ002 {#REQ002}", "", "Removed later.", ""])
        + [""],
        "REQ003": ["## 1.3 REQ003 {#REQ003}", "", "Wrapped later.", ""],
        "REQ004": div("added", ["## 1.4 REQ004 {#REQ004}", "", "Brand new.", ""])
        + [""],
        "REQ005": div("removed", ["## 1.5 REQ005 {#REQ005}", "", "Goes away.", ""])
        + [""],
        "REQ006": div("added", ["## 1.6 REQ006 {#REQ006}", "", "Comes back.", ""])
        + [""],
        "REQ008": ["## 1.8 REQ008 {#REQ008}", "", "Norm.", ""],
    }

    def setUp(self):
        super().setUp()
        old = build_revision(self.root, self.first, os.path.join(self.temp, "old"))
        self.new = build_revision(
            self.root, self.second, os.path.join(self.temp, "new")
        )
        revisions = (("v1.0", self.first), ("v1.1", self.second))
        self.diff = diff_trees(old, self.new, revisions)
        self.publisher = HtmlDiffPublisher(self.new, ".html", self.diff, temp=self.temp)

    def page(self, prefix, changed_only=False):
        """Get the markdown lines of a document page."""
        publisher = HtmlDiffPublisher(
            self.new, ".html", self.diff, changed_only=changed_only, temp=self.temp
        )
        return list(
            publisher._lines_markdown(  # pylint: disable=protected-access
                self.new.find_document(prefix), linkify=True, to_html=True
            )
        )

    def summary(self, counts=None):
        """Get the expected summary block, with a counts line for pages."""
        revisions = (
            "<p>Changes from <strong>v1.0</strong> (<code>{}</code>) to "
            "<strong>v1.1</strong> (<code>{}</code>)</p>".format(
                self.first[:12], self.second[:12]
            )
        )
        counts = [counts] if counts else []
        return ['<div class="diff-summary">', revisions] + counts + ["</div>", ""]

    def index(self, rows, files=()):
        """Get the expected index lines."""
        return (
            [STYLE]
            + self.summary()
            + [
                '<table class="table">',
                "<thead><tr><th>Document</th><th>Status</th><th>Added</th>"
                "<th>Removed</th><th>Changed</th></tr></thead>",
                "<tbody>",
            ]
            + [
                '<tr><td><a href="documents/{0}.html">{0}</a></td><td>{1}</td>'
                "<td>{2}</td><td>{3}</td><td>{4}</td></tr>".format(*row)
                for row in rows
            ]
            + ["</tbody>", "</table>", "", "<h3>Tree Structure:</h3>"]
            # The tree drawing depends on the console encoding (Tree.draw).
            + ["<pre><code>" + self.new.draw(html_links=True) + "</pre></code>"]
            + ["", "<hr>", "", "<h3>Published Documents:</h3>", "<p>", "<ul>"]
            + [
                '<li> <a href="documents/{0}.html">{0}</a> </li>'.format(name)
                for name in files
            ]
            + ["</ul>", "</p>"]
        )

    def test_page(self):
        """Verify a page has the styles, the summary and every entry in order."""
        self.assertEqual(
            [STYLE, ""]
            + self.summary("<p>2 added, 2 removed, 1 changed</p>")
            + [line for lines in self.ENTRIES.values() for line in lines],
            self.page("REQ"),
        )

    def test_changed_only(self):
        """Verify the changed-only option drops unchanged entries."""
        publisher = HtmlDiffPublisher(
            self.new, ".html", self.diff, changed_only=True, temp=self.temp
        )
        toc = publisher.table_of_contents(True, self.new.find_document("REQ"))
        self.assertEqual(
            ["toc", "REQ001", "REQ002", "REQ004", "REQ005", "REQ006"],
            [str(entry["uid"]) for entry in toc],
        )
        shown = ("REQ001", "REQ002", "REQ004", "REQ005", "REQ006")
        self.assertEqual(
            [STYLE, ""]
            + self.summary("<p>2 added, 2 removed, 1 changed</p>")
            + [line for uid in shown for line in self.ENTRIES[uid]],
            self.page("REQ", changed_only=True),
        )

    def test_index(self):
        """Verify the index lists every page with its status and counts."""
        self.assertEqual(
            self.index(
                [
                    ("REQ", "changed", 2, 2, 1),
                    ("NEW", "added", 1, 0, 0),
                    ("SW", "unchanged", 0, 0, 0),
                    ("OLD", "removed", 0, 1, 0),
                ],
                files=["REQ"],
            ),
            list(self.publisher.lines_index(["REQ.html"], tree=self.new)),
        )


class TestPictureMarkup(PictureTestCase):
    """Unit tests for the markup of changed and missing pictures."""

    def setUp(self):
        super().setUp()
        old = build_revision(self.root, self.first, os.path.join(self.temp, "old"))
        new = build_revision(self.root, self.second, os.path.join(self.temp, "new"))
        revisions = (("v1.0", self.first), ("v1.1", self.second))
        self.diff = diff_trees(old, new, revisions)
        self.publisher = HtmlDiffPublisher(new, ".html", self.diff, temp=self.temp)
        with self.assertLogs("doorstop.core.publishers.htmldiff", "WARNING") as logs:
            self.publisher._entries(self.diff.documents[0])  # pylint: disable=W0212
        self.warnings = logs.output
        self.entries = {str(e.display.uid): e for e in self.diff.documents[0].entries}

    def lines(self, uid):
        """Get the markdown lines of one entry."""
        return list(
            self.publisher._lines_entry(  # pylint: disable=protected-access
                self.entries[uid], linkify=True, to_html=True
            )
        )

    def test_kinds(self):
        """Verify items are changed exactly when a picture they show changed.

        REQ003's picture moved with its document folder but did not change.
        """
        self.assertEqual(
            {
                "REQ001": "changed",
                "REQ002": "changed",
                "REQ003": "unchanged",
                "REQ004": "unchanged",
                "REQ005": "unchanged",
                "REQ006": "changed",
                "REQ007": "removed",
            },
            {uid: entry.kind for uid, entry in self.entries.items()},
        )
        self.assertEqual(
            [
                "WARNING:doorstop.core.publishers.htmldiff:picture not found: "
                "assets/images/missing.png (REQ004)",
                "WARNING:doorstop.core.publishers.htmldiff:picture not found: "
                "assets/images/new.png (REQ006)",
            ],
            sorted(self.warnings),
        )

    def test_copies(self):
        """Verify publishing writes the old picture versions."""
        out = os.path.join(self.temp, "out")
        with self.assertLogs("doorstop.core.publishers.htmldiff", "WARNING"):
            publish_diff(self.root, "v1.0", "v1.1", out)
        path = os.path.join(out, "documents", "diff-old", "my reqs", "assets", "images")
        path = os.path.join(path, "d.png")
        with open(path, "rb") as stream:
            self.assertEqual(b"one", stream.read())
        path = os.path.join(out, "documents", "assets", "images", "d.png")
        with open(path, "rb") as stream:
            self.assertEqual(b"two", stream.read())
