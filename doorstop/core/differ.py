# SPDX-License-Identifier: LGPL-3.0-only

"""Compare the tree at two revisions."""

import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from doorstop import common
from doorstop.common import DoorstopError
from doorstop.core import builder, publisher, vcs
from doorstop.core.document import Document
from doorstop.core.item import Item
from doorstop.core.publishers.htmldiff import HtmlDiffPublisher
from doorstop.core.vcs import git

log = common.logger(__name__)

Revision = Tuple[str, Optional[str]]


@dataclass
class ItemChange:
    """One entry of a document page, in display order."""

    # added, removed, or matched: the publisher decides changed/unchanged
    kind: str
    old: Optional[Item] = None
    new: Optional[Item] = None

    @property
    def display(self):
        """Get the item shown for this entry."""
        return self.new if self.new is not None else self.old

    @property
    def differs(self):
        """Determine if the entry shows a change."""
        return self.kind in ("added", "removed", "changed")


@dataclass
class DocumentChange:
    """The entries of one output page."""

    prefix: str
    old: Optional[Document]
    new: Optional[Document]
    entries: List[ItemChange] = field(default_factory=list)

    @property
    def counts(self) -> Dict[str, int]:
        """Count the added, removed and changed entries."""
        counts = dict.fromkeys(("added", "removed", "changed"), 0)
        for entry in self.entries:
            if entry.kind in counts:
                counts[entry.kind] += 1
        return counts


@dataclass
class TreeDiff:
    """The comparison of two trees."""

    old: Revision
    new: Revision
    documents: List[DocumentChange]


def _readable(tree):
    """Get each document with its readable items, in publish order."""
    documents = {}
    for document in tree.documents:
        items = []
        for item in document:
            try:
                item.load()
            except DoorstopError as exc:
                log.warning("cannot read item %s: %s", item.path, exc)
            else:
                items.append(item)
        documents[str(document.prefix)] = (document, sorted(items))
    return documents


def diff_trees(old, new, revisions):
    """Match and order every item of two trees.

    Items are matched by document prefix and UID: a renamed item file is a
    removed and an added item, a moved one is matched.

    :param old: tree at the older revision
    :param new: tree at the newer revision
    :param revisions: (old, new) revisions as (name, commit) tuples

    :return: :class:`TreeDiff`

    """
    old_docs, new_docs = _readable(old), _readable(new)
    changes = []
    prefixes = list(new_docs) + [p for p in old_docs if p not in new_docs]
    for prefix in prefixes:
        new_document, new_items = new_docs.get(prefix, (None, []))
        old_document, old_items = old_docs.get(prefix, (None, []))
        olds = {str(item.uid): item for item in old_items if item.active}
        news = {str(item.uid) for item in new_items if item.active}
        entries = []
        for item in new_items:
            if not item.active:
                continue
            old_item = olds.get(str(item.uid))
            if old_item is None:
                change = ItemChange("added", new=item)
            else:
                change = ItemChange("matched", old_item, item)
            entries.append((item, change))
        # Insert removed entries after the nearest preceding old item shown here.
        inserts = defaultdict(list)
        anchor = None
        for uid, old_item in olds.items():
            if uid in news:
                anchor = uid
            else:
                inserts[anchor].append((old_item, ItemChange("removed", old_item)))
        # Added items with a lower level than a pending old item go first.
        ordered, pending = [], list(inserts[None])
        for item, change in entries:
            while pending and not (
                change.kind == "added" and item.level < pending[0][0].level
            ):
                ordered.append(pending.pop(0)[1])
            ordered.append(change)
            pending.extend(inserts[str(item.uid)])
        ordered.extend(entry for _, entry in pending)
        changes.append(DocumentChange(prefix, old_document, new_document, ordered))
    old_rev, new_rev = revisions
    return TreeDiff(old_rev, new_rev, changes)


class _DiffTree:
    """Tree-like object listing the pages of a diff for publish()."""

    def __init__(self, diff, tree):
        self.documents = [
            change.new if change.new is not None else change.old
            for change in diff.documents
        ]
        self._tree = tree

    def __len__(self):
        return len(self.documents)

    def draw(self, encoding=None, html_links=False):
        """Draw the newer tree, or nothing if it is empty."""
        if not len(self._tree):  # pylint: disable=len-as-condition
            return ""
        return self._tree.draw(encoding=encoding, html_links=html_links)


def _require_git(root):
    """Get the git working copy at a root."""
    if not os.path.exists(os.path.join(root, git.WorkingCopy.DIRECTORY)):
        raise DoorstopError("revision diffs need a git repository")
    return git.WorkingCopy(root)


def _snapshot(working_copy, commit, path):
    """Build the tree at a commit, or of the working state for None."""
    if commit is None:
        return builder.build(cwd=working_copy.path, root=working_copy.path)
    os.makedirs(path)
    # ponytail: whole-commit export; restrict to document directories plus
    # referenced paths if large repositories are slow.
    working_copy.export(commit, path)
    tree = builder.build(cwd=path, root=path)
    # An export has no VCS folder; use the fallback without warning about it.
    tree._vcs = vcs.DEFAULT(path)  # pylint: disable=protected-access
    return tree


def publish_diff(
    root: str,
    old: str,
    new: Optional[str],
    path: str,
    prefix: str = "all",
    changed_only: bool = False,
    template: Optional[str] = None,
) -> str:
    """Compare the tree at two revisions and publish the result as HTML.

    :param root: path to the root of the git working copy
    :param old: older revision name
    :param new: newer revision name, or None for the working state
    :param path: output directory ('all') or output file/directory (one document)
    :param prefix: document prefix or 'all'
    :param changed_only: only include items that differ
    :param template: as for publish()

    :raises: :class:`~doorstop.common.DoorstopError` for a missing git
        repository, unknown revisions or an unknown prefix, before anything
        is written

    :return: output path

    """
    working_copy = _require_git(root)
    old_rev = (old, working_copy.resolve(old))
    new_rev = (new, working_copy.resolve(new)) if new else ("working state", None)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        old_tree = _snapshot(working_copy, old_rev[1], os.path.join(temp, "old"))
        new_tree = _snapshot(working_copy, new_rev[1], os.path.join(temp, "new"))
        diff = diff_trees(old_tree, new_tree, (old_rev, new_rev))
        if prefix == "all":
            obj = _DiffTree(diff, new_tree)
        else:
            changes = [c for c in diff.documents if c.prefix.lower() == prefix.lower()]
            if not changes:
                raise DoorstopError("no document with prefix: {}".format(prefix))
            obj = changes[0].new if changes[0].new is not None else changes[0].old
        publisher.publish(
            obj,
            path,
            ".html",
            matrix=False,
            template=template,
            publisher=HtmlDiffPublisher(obj, ".html", diff, changed_only, temp),
        )
    return path
