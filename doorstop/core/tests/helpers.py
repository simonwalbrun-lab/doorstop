# SPDX-License-Identifier: LGPL-3.0-only
# pylint: disable=unused-argument,protected-access

"""Unit test helper functions to reduce code duplication."""

import os
import subprocess
import tempfile
import time
from logging import NullHandler
from os import chmod
from shutil import copytree
from stat import S_IWRITE
from typing import List

import yaml

from doorstop import common
from doorstop.core.builder import build
from doorstop.core.tests import ROOT


class ListLogHandler(NullHandler):
    """Create a log handler for asserting log calls."""

    def __init__(self, log):
        super().__init__()
        self.records: List[str] = []
        self.log = log

    def __enter__(self):
        self.log.addHandler(self)
        return self

    def __exit__(self, kind, value, traceback):
        self.log.removeHandler(self)

    def handle(self, record):
        self.records.append(str(record.msg))


def build_expensive_tree(obj):
    # Build a tree.
    copytree(ROOT, obj.datapath)
    obj.mock_tree = build(cwd=obj.datapath, root=obj.datapath, request_next_number=None)


def on_error_with_retry(func, path, exc_info, max_retries=10, max_time=5):
    """Define a separate function to handle errors for rmtree.

    This callback function is used to retry rmtree operations
    that fail for up to max_time seconds. This is necessary because
    Windows does not always release file handles immediately,
    and the rmtree operation can fail.
    """

    start_time = time.time()

    for attempt in range(max_retries):
        elapsed = time.time() - start_time

        # Timeout check
        if elapsed >= max_time:
            print(f"Timeout: Failed to remove {path} after {elapsed:.1f}s. Giving up.")
            return

        # Change the file permissions
        try:
            chmod(path, S_IWRITE)
        except Exception:
            pass  # Ignore chmod failures

        # Sleep briefly
        time.sleep(0.2)

        try:
            # Try the operation again
            func(path)
            return  # Success!
        except Exception as e:
            print(f"Retry {attempt + 1}/{max_retries} failed for {path}, error: {e}")
            # Continue to next attempt

    # All retries exhausted
    elapsed = time.time() - start_time
    print(
        f"Failed to remove {path} after {attempt + 1} retries ({elapsed:.1f}s). Giving up."
    )


def git(root, *args):
    """Run a git command in a repository and return its output."""
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout


def make_git_repo():
    """Create an empty git repository in a new temporary directory."""
    root = tempfile.mkdtemp()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Doorstop Tests")
    git(root, "config", "user.email", "tests@doorstop.invalid")
    git(root, "config", "core.autocrlf", "false")
    return root


def write_document(root, relpath, prefix, parent=None, extra=None):
    """Write a document's .doorstop.yml (no separator, three digits)."""
    settings = {"digits": 3, "prefix": prefix, "sep": ""}
    if parent:
        settings["parent"] = parent
    data = {"settings": settings}
    data.update(extra or {})
    path = os.path.join(root, relpath, ".doorstop.yml")
    common.create_dirname(path)
    common.write_text(yaml.safe_dump(data), path)


def write_item(root, relpath, data):
    """Write a YAML item file, filling in the standard attributes."""
    item = {
        "active": True,
        "derived": False,
        "header": "",
        "level": 1,
        "links": [],
        "normative": True,
        "ref": "",
        "reviewed": None,
        "text": "",
    }
    item.update(data)
    path = os.path.join(root, relpath)
    common.create_dirname(path)
    common.write_text(yaml.safe_dump(item), path)


def commit(root, message, tag=None):
    """Commit every change, optionally tag it, and return the commit hash."""
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    if tag:
        git(root, "tag", tag)
    return git(root, "rev-parse", "HEAD").strip()
