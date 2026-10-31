# SPDX-License-Identifier: LGPL-3.0-only

"""Plug-in module to store requirements in a Git repository."""

import io
import os
import subprocess
import tarfile
from typing import List, Tuple

from doorstop import common
from doorstop.common import DoorstopError
from doorstop.core.vcs.base import BaseWorkingCopy

log = common.logger(__name__)


class WorkingCopy(BaseWorkingCopy):
    """Git working copy."""

    DIRECTORY = ".git"
    IGNORES = (".gitignore",)
    # characters of paths per call, below the Windows command-line limit (32767)
    MAX_CHARS = 30000

    def lock(self, path):
        log.debug("`git` does not support locking: %s", path)
        self.call("git", "pull")

    def edit(self, path):
        self.call("git", "add", self.relpath(path))

    def edit_many(self, paths):
        chunk: List[str] = []
        size = 0
        for name in map(self.relpath, paths):
            cost = len(name) + 3  # separator and quotes around paths with spaces
            if chunk and size + cost > self.MAX_CHARS:
                self.call("git", "add", "--", *chunk)
                chunk, size = [], 0
            chunk.append(name)
            size += cost
        if chunk:
            self.call("git", "add", "--", *chunk)

    def add(self, path):
        self.call("git", "add", self.relpath(path))

    def delete(self, path):
        self.call("git", "rm", self.relpath(path), "--force", "--quiet")

    def commit(self, message=None):
        message = message or input("Commit message: ")
        self.call("git", "commit", "--all", "--message", message)
        self.call("git", "push")

    def _run(self, *args) -> subprocess.CompletedProcess:
        """Run a git command in the working copy and capture its output."""
        log.debug("$ git %s", " ".join(args))
        try:
            return subprocess.run(
                ["git", *args], cwd=self.path, capture_output=True, check=False
            )
        except FileNotFoundError:
            raise DoorstopError("Command not found: git") from None

    def resolve(self, rev: str) -> str:
        """Get the full commit hash of a revision name.

        :param rev: tag, full or abbreviated hash, branch or other revision name

        :raises: :class:`~doorstop.common.DoorstopError` for an unknown revision

        :return: full commit hash

        """
        result = self._run("rev-parse", "--verify", "--quiet", rev + "^{commit}")
        if result.returncode:
            raise DoorstopError(
                "unknown revision: {} (fetch it if it exists only on the remote)".format(
                    rev
                )
            )
        return result.stdout.decode("utf-8").strip()

    def export(self, commit: str, dest: str) -> None:
        """Extract the files of a commit into an existing directory.

        :param commit: commit hash
        :param dest: directory to extract into

        """
        result = self._run("archive", "--format=tar", commit)
        if result.returncode:
            raise DoorstopError(result.stderr.decode("utf-8", "replace").strip())
        with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:  # nosec B202
            if hasattr(tarfile, "data_filter"):
                archive.extractall(dest, filter="data")
            else:  # pragma: no cover (Python before 3.10.12)
                archive.extractall(dest)  # nosec B202


TOKEN = (
    r"<a [^>]*>[^<]*</a>|<[^>]*>|!?\[[^]]*\]\([^)]*\)|\{#[^}]*\}"
    r"|[[:alnum:]_.-]+|[^[:space:]]"
)


def word_diff(old: str, new: str, temp: str) -> List[List[Tuple[str, str]]]:
    """Diff two texts word by word with `git diff --word-diff=porcelain`.

    Anchors, HTML tags, markdown links and images, and `{#...}` attribute
    lists are kept whole (POSIX leftmost-longest matching of `TOKEN`).

    :param old: older text
    :param new: newer text
    :param temp: directory for the two input files

    :raises: :class:`~doorstop.common.DoorstopError` if git fails

    :return: one list of (op, text) segments per line, op in " ", "-", "+"

    """
    paths = [os.path.join(temp, "old.md"), os.path.join(temp, "new.md")]
    for path, text in zip(paths, (old, new)):
        with open(path, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text + "\n")
    context = "-U{}".format(max(old.count("\n"), new.count("\n")) + 1)
    # ponytail: one git process per changed item; batch into one --no-index
    # directory diff if many changed items get slow.
    args = ["diff", "--no-index", "--no-color", "--no-ext-diff", context]
    args += ["--word-diff=porcelain", "--word-diff-regex=" + TOKEN, *paths]
    result = WorkingCopy(temp)._run(*args)  # pylint: disable=protected-access
    if result.returncode > 1:
        raise DoorstopError(result.stderr.decode("utf-8", "replace").strip())
    lines: List[List[Tuple[str, str]]] = []
    line: List[Tuple[str, str]] = []
    rows = result.stdout.decode("utf-8").replace("\r", "").split("\n")
    started = False
    for row in rows:
        if row.startswith("@@"):
            started = True
        elif started and row == "~":
            lines.append(line)
            line = []
        elif started and row[:1] in (" ", "-", "+"):
            line.append((row[0], row[1:]))
    return lines
