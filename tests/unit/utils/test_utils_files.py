import errno
import os
from pathlib import Path

import pytest

from transoria.utils import files


def _no_links(*args):
    raise OSError(errno.ENOTSUP, "hard links unsupported")


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_publish_no_clobber(tmp_path, monkeypatch, fallback, existing):
    source, output = tmp_path / "candidate", tmp_path / "output"
    source.write_bytes(b"new content")
    if fallback:
        monkeypatch.setattr(files.os, "link", _no_links)
        monkeypatch.setattr(files.sys, "platform", "darwin")
    if existing:
        output.write_bytes(b"existing content")
        with pytest.raises(FileExistsError):
            files.publish_file(source, output)
        assert output.read_bytes() == b"existing content"
    else:
        files.publish_file(source, output)
        assert output.read_bytes() == b"new content"
    assert source.read_bytes() == b"new content"


@pytest.mark.parametrize("competing", [False, True])
def test_failed_copy_only_removes_owned_output(tmp_path, monkeypatch, competing):
    source, output = tmp_path / "candidate", tmp_path / "output"
    source.write_bytes(b"new content")
    monkeypatch.setattr(files.os, "link", _no_links)
    monkeypatch.setattr(files.sys, "platform", "darwin")

    def failed_copy(reader, writer):
        writer.write(b"partial")
        writer.flush()
        if competing:
            replacement = tmp_path / "replacement"
            replacement.write_bytes(b"someone else's file")
            os.replace(replacement, output)
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(files.shutil, "copyfileobj", failed_copy)
    with pytest.raises(OSError, match="disk full"):
        files.publish_file(source, output)
    if competing:
        assert output.read_bytes() == b"someone else's file"
    else:
        assert not output.exists()
    assert source.read_bytes() == b"new content"


def test_windows_fallback_renames_closed_candidate(tmp_path, monkeypatch):
    source, output = tmp_path / "candidate", tmp_path / "output"
    source.write_bytes(b"new content")
    monkeypatch.setattr(files.os, "link", _no_links)
    monkeypatch.setattr(files.sys, "platform", "win32")
    rename = files.os.rename
    calls = []

    def no_clobber_rename(src, dst):
        calls.append((src, dst))
        if Path(dst).exists():
            raise FileExistsError(dst)
        rename(src, dst)

    monkeypatch.setattr(files.os, "rename", no_clobber_rename)
    files.publish_file(source, output)
    assert calls == [(source, output)]
    assert not source.exists()
    assert output.read_bytes() == b"new content"
    source.write_bytes(b"another")
    with pytest.raises(FileExistsError):
        files.publish_file(source, output)
    assert output.read_bytes() == b"new content"


def test_failed_overwrite_never_truncates_destination(tmp_path, monkeypatch):
    source, output = tmp_path / "candidate", tmp_path / "output"
    source.write_bytes(b"new content")
    output.write_bytes(b"existing content")

    def denied(*args):
        raise PermissionError("locked")

    monkeypatch.setattr(files.os, "replace", denied)
    with pytest.raises(PermissionError):
        files.publish_file(source, output, overwrite=True)
    assert output.read_bytes() == b"existing content"
    assert source.read_bytes() == b"new content"


def test_unreadable_candidate_does_not_create_output(tmp_path, monkeypatch):
    monkeypatch.setattr(files.os, "link", _no_links)
    monkeypatch.setattr(files.sys, "platform", "darwin")
    output = tmp_path / "output"
    with pytest.raises(FileNotFoundError):
        files.publish_file(tmp_path / "missing", output)
    assert not output.exists()
