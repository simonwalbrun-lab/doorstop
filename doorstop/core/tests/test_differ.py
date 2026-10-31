# SPDX-License-Identifier: LGPL-3.0-only

"""Unit tests for the doorstop.core.differ module."""

import os
import shutil
import tempfile
import unittest

from doorstop.common import DoorstopError, write_text
from doorstop.core.builder import build
from doorstop.core.differ import diff_trees, publish_diff
from doorstop.core.tests.helpers import (
    commit,
    git,
    make_git_repo,
    on_error_with_retry,
    write_document,
    write_item,
)
from doorstop.core.vcs.git import WorkingCopy


class RepoTestCase(unittest.TestCase):
    """Base class for tests on a temporary git repository."""

    def setUp(self):
        self.root = make_git_repo()
        self.temp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, onerror=on_error_with_retry)
        shutil.rmtree(self.temp, onerror=on_error_with_retry)

    def status(self):
        """Return the porcelain status and the staged diff of the repository."""
        return git(self.root, "status", "--porcelain"), git(
            self.root, "diff", "--cached"
        )


def build_revision(root, commit_hash, dest):
    """Build the tree of a commit in a new directory."""
    os.makedirs(dest)
    WorkingCopy(root).export(commit_hash, dest)
    return build(cwd=dest, root=dest)


class TestGitRevisions(RepoTestCase):
    """Unit tests for resolving and exporting revisions."""

    def setUp(self):
        super().setUp()
        write_document(self.root, "reqs", "REQ")
        write_item(self.root, "reqs/REQ001.yml", {"text": "First version."})
        self.first = commit(self.root, "first", tag="v1.0")
        write_item(self.root, "reqs/REQ001.yml", {"text": "Second version."})
        self.second = commit(self.root, "second")
        self.branch = git(self.root, "branch", "--show-current").strip()
        self.working_copy = WorkingCopy(self.root)

    def test_resolve(self):
        """Verify revisions resolve, and an unknown one raises an error naming it."""
        resolve = self.working_copy.resolve
        self.assertEqual(self.first, resolve("v1.0"))
        self.assertEqual(self.first, resolve(self.first))
        self.assertEqual(self.first, resolve(self.first[:7]))
        self.assertEqual(self.second, resolve(self.branch))
        with self.assertRaises(DoorstopError) as context:
            resolve("nosuch")
        self.assertEqual(
            "unknown revision: nosuch (fetch it if it exists only on the remote)",
            str(context.exception),
        )

    def test_export(self):
        """Verify a commit is exported without touching the repository."""
        write_item(self.root, "reqs/REQ001.yml", {"text": "Uncommitted."})
        before = self.status()
        self.working_copy.export(self.first, self.temp)
        with open(
            os.path.join(self.temp, "reqs", "REQ001.yml"), encoding="utf-8"
        ) as stream:
            self.assertEqual(
                "active: true\nderived: false\nheader: ''\nlevel: 1\nlinks: []\n"
                "normative: true\nref: ''\nreviewed: null\ntext: First version.\n",
                stream.read(),
            )
        self.assertTrue(
            os.path.isfile(os.path.join(self.temp, "reqs", ".doorstop.yml"))
        )
        self.assertEqual(before, self.status())


class ScenarioTestCase(RepoTestCase):
    """Base class with two tagged revisions of a REQ <- SW tree.

    The repository is built once per class; tests must not move its tags.
    """

    root = ""
    first = ""
    second = ""

    @classmethod
    def setUpClass(cls):
        cls.root = make_git_repo()
        cls.first, cls.second = cls.scenario(cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, onerror=on_error_with_retry)

    def setUp(self):  # pylint: disable=super-init-not-called
        self.temp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp, onerror=on_error_with_retry)

    @staticmethod
    def scenario(root):
        """Commit the two revisions and return their hashes."""
        write_document(root, "reqs", "REQ")
        write_item(root, "reqs/REQ001.yml", {"text": "Start quickly.", "level": "1.1"})
        write_item(root, "reqs/REQ002.yml", {"text": "Removed later.", "level": "1.2"})
        write_item(root, "reqs/REQ003.yml", {"text": "Wrapped\nlater.", "level": "1.3"})
        write_item(root, "reqs/REQ005.yml", {"text": "Goes away.", "level": "1.5"})
        write_item(
            root,
            "reqs/REQ006.yml",
            {"text": "Comes back.", "level": "1.6", "active": False},
        )
        write_item(root, "reqs/REQ007.yml", {"text": "Never.", "active": False})
        write_item(root, "reqs/REQ008.yml", {"text": "Norm.", "level": "1.8"})
        write_document(root, "sw", "SW", parent="REQ")
        write_item(root, "sw/SW001.yml", {"text": "Soft.", "links": [{"REQ001": None}]})
        write_document(root, "old", "OLD", parent="REQ")
        write_item(root, "old/OLD001.yml", {"text": "Old document."})
        first = commit(root, "base", tag="v1.0")
        write_item(
            root, "reqs/REQ001.yml", {"text": "Start very quickly.", "level": "1.1"}
        )
        os.remove(os.path.join(root, "reqs", "REQ002.yml"))
        write_item(root, "reqs/REQ003.yml", {"text": "Wrapped later.", "level": "1.3"})
        write_item(root, "reqs/REQ004.yml", {"text": "Brand new.", "level": "1.4"})
        write_item(
            root,
            "reqs/REQ005.yml",
            {"text": "Goes away.", "level": "1.5", "active": False},
        )
        write_item(root, "reqs/REQ006.yml", {"text": "Comes back.", "level": "1.6"})
        write_item(
            root,
            "reqs/REQ008.yml",
            {"text": "Norm.", "level": "1.8", "normative": False},
        )
        shutil.rmtree(os.path.join(root, "old"))
        write_document(root, "new", "NEW", parent="REQ")
        write_item(root, "new/NEW001.yml", {"text": "New document."})
        return first, commit(root, "change", tag="v1.1")

    def diff(self, first=None, second=None):
        """Compare two commits of the scenario."""
        first, second = first or self.first, second or self.second
        old = build_revision(self.root, first, os.path.join(self.temp, "old"))
        new = build_revision(self.root, second, os.path.join(self.temp, "new"))
        return diff_trees(old, new, (("v1.0", first), ("v1.1", second)))


def summary(diff):
    """Summarize a diff as {prefix: [(uid, kind)]}."""
    return {
        change.prefix: [(str(e.display.uid), e.kind) for e in change.entries]
        for change in diff.documents
    }


class TestDiffTrees(ScenarioTestCase):
    """Unit tests for matching and ordering items."""

    def test_diff(self):
        """Verify each item is matched and placed as data-model.md says."""
        expected = {
            "REQ": [
                ("REQ001", "matched"),
                ("REQ002", "removed"),
                ("REQ003", "matched"),
                ("REQ004", "added"),
                ("REQ005", "removed"),
                ("REQ006", "added"),
                ("REQ008", "matched"),
            ],
            "SW": [("SW001", "matched")],
            "NEW": [("NEW001", "added")],
            "OLD": [("OLD001", "removed")],
        }
        self.assertEqual(expected, summary(self.diff()))


class TestPublishDiff(ScenarioTestCase):
    """Unit tests for publishing a diff."""

    def setUp(self):
        super().setUp()
        self.out = os.path.join(self.temp, "out")

    def test_publish(self):
        """Verify the pages and the index are written."""
        path = publish_diff(self.root, "v1.0", "v1.1", self.out)
        self.assertEqual(self.out, path)
        pages = os.listdir(os.path.join(self.out, "documents"))
        self.assertEqual(
            ["NEW.html", "OLD.html", "REQ.html", "SW.html"],
            sorted(name for name in pages if name.endswith(".html")),
        )
        self.assertTrue(os.path.isfile(os.path.join(self.out, "index.html")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "traceability.html")))

    def test_one_document(self):
        """Verify a prefix publishes only that document, without an index."""
        publish_diff(self.root, "v1.0", "v1.1", self.out, prefix="req")
        self.assertEqual(
            ["REQ.html"],
            [n for n in os.listdir(os.path.join(self.out, "documents")) if "." in n],
        )
        self.assertFalse(os.path.exists(os.path.join(self.out, "index.html")))


def write_picture(root, name, data):
    """Write a picture into the REQ document's assets folder."""
    path = os.path.join(root, "my reqs", "assets", "images", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as stream:
        stream.write(data)


class PictureTestCase(ScenarioTestCase):
    """Base class with two revisions of items that show pictures.

    The second revision also moves the REQ document to another folder.
    """

    @staticmethod
    def scenario(root):
        write_document(root, "my reqs", "REQ")
        write_picture(root, "d.png", b"one")
        write_picture(root, "same.png", b"same")
        texts = {
            "REQ001": "![d](assets/images/d.png)",
            "REQ002": "Again ![d](assets/images/d.png)",
            "REQ003": "![s](assets/images/same.png)",
            "REQ004": "![m](assets/images/missing.png)",
            "REQ005": "![r](https://example.com/x.png) ![p](//host/x.png)",
            "REQ006": "![n](assets/images/new.png)",
            "REQ007": "Gone ![d](assets/images/d.png)",
        }
        for number, uid in enumerate(texts, start=1):
            data = {"text": texts[uid], "level": "1.{}".format(number)}
            write_item(root, "my reqs/{}.yml".format(uid), data)
        first = commit(root, "pictures", tag="v1.0")
        write_picture(root, "d.png", b"two")
        write_picture(root, "new.png", b"new")
        os.remove(os.path.join(root, "my reqs", "REQ007.yml"))
        os.rename(os.path.join(root, "my reqs"), os.path.join(root, "requirements"))
        return first, commit(root, "changed pictures", tag="v1.1")


class RenameTestCase(ScenarioTestCase):
    """Base class with renamed and moved item files."""

    @staticmethod
    def scenario(root):
        write_document(root, "reqs", "REQ")
        write_document(root, "sys", "SYS", parent="REQ")
        write_document(root, "sw", "SW", parent="REQ")
        items = {
            "reqs/REQ001.yml": {"text": "Stays.", "level": "1.1"},
            "reqs/REQ003.yml": {"text": "Renamed.", "level": "1.3"},
            "reqs/REQ005.yml": {"text": "Moved.", "level": "1.5"},
            "reqs/REQ006.yml": {"text": "Moved and edited.", "level": "1.6"},
            "reqs/REQ011.yml": {
                "text": "Links.",
                "level": "1.7",
                "links": [{"REQ003": None}],
            },
            "sys/SYS001.yml": {"text": "Stays in SYS."},
            "sys/SYS004.yml": {"text": "Moves.", "level": "2"},
            "sw/SW001.yml": {"text": "Stays in SW."},
        }
        for path, data in items.items():
            write_item(root, path, data)
        first = commit(root, "base", tag="v1.0")
        git(root, "mv", "reqs/REQ003.yml", "reqs/REQ010.yml")
        os.makedirs(os.path.join(root, "reqs", "sub"))
        git(root, "mv", "reqs/REQ005.yml", "reqs/sub/REQ005.yml")
        git(root, "mv", "reqs/REQ006.yml", "reqs/sub/REQ006.yml")
        write_item(
            root,
            "reqs/sub/REQ006.yml",
            {"text": "Moved and edited again.", "level": "1.6"},
        )
        write_item(
            root,
            "reqs/REQ011.yml",
            {"text": "Links.", "level": "1.7", "links": [{"REQ010": None}]},
        )
        git(root, "mv", "sys/SYS004.yml", "sw/SW012.yml")
        return first, commit(root, "renames and moves", tag="v1.1")


class TestRenames(RenameTestCase):
    """Unit tests for renamed and moved item files."""

    def test_diff(self):
        """Verify a renamed file is removed and added, and a moved one matched."""
        self.assertEqual(
            {
                "REQ": [
                    ("REQ001", "matched"),
                    ("REQ003", "removed"),
                    ("REQ010", "added"),
                    ("REQ005", "matched"),
                    ("REQ006", "matched"),
                    ("REQ011", "matched"),
                ],
                "SYS": [("SYS001", "matched"), ("SYS004", "removed")],
                "SW": [("SW001", "matched"), ("SW012", "added")],
            },
            summary(self.diff()),
        )


class TestUnreadableItem(RepoTestCase):
    """Unit tests for items that cannot be read."""

    def test_unreadable_item(self):
        """Verify an unreadable item is skipped with a warning."""
        write_document(self.root, "reqs", "REQ")
        write_item(self.root, "reqs/REQ001.yml", {"text": "Fine."})
        first = commit(self.root, "base")
        write_text("text: [unclosed\n", os.path.join(self.root, "reqs", "REQ002.yml"))
        second = commit(self.root, "broken")
        old = build_revision(self.root, first, os.path.join(self.temp, "old"))
        new = build_revision(self.root, second, os.path.join(self.temp, "new"))
        with self.assertLogs("doorstop.core.differ", "WARNING") as logs:
            diff = diff_trees(old, new, (("a", first), ("b", second)))
        self.assertIn("cannot read item", logs.output[0])
        self.assertEqual({"REQ": [("REQ001", "matched")]}, summary(diff))


class TestWorkingState(RepoTestCase):
    """Unit tests for comparing a revision with the working state."""

    def setUp(self):
        super().setUp()
        write_document(self.root, "reqs", "REQ")
        write_item(self.root, "reqs/REQ001.yml", {"text": "Committed.", "level": "1.1"})
        self.first = commit(self.root, "base", tag="v1.0")
        write_item(
            self.root, "reqs/REQ001.yml", {"text": "Uncommitted.", "level": "1.1"}
        )
        write_item(self.root, "reqs/REQ030.yml", {"text": "Untracked.", "level": "1.6"})

    def test_working_state(self):
        """Verify uncommitted edits and new files are compared."""
        old = build_revision(self.root, self.first, os.path.join(self.temp, "old"))
        new = build(cwd=self.root, root=self.root)
        diff = diff_trees(old, new, (("v1.0", self.first), ("working state", None)))
        self.assertEqual(
            {
                "REQ": [
                    ("REQ001", "matched"),
                    ("REQ030", "added"),
                ]
            },
            summary(diff),
        )
