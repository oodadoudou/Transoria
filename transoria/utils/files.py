from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def publish_file(source: Path, output: Path, *, overwrite: bool = False) -> None:
    """Publish a closed candidate without clobbering unconfirmed destinations."""
    if overwrite:
        os.replace(source, output)
        return
    try:
        os.link(source, output)
        return
    except FileExistsError:
        raise
    except OSError:
        # Windows rename refuses existing targets, including on non-NTFS disks.
        if sys.platform == "win32":
            os.rename(source, output)
            return

    # Some mounted filesystems support neither hard links nor no-clobber rename.
    created = None
    try:
        with source.open("rb") as reader, output.open("xb") as writer:
            created = os.fstat(writer.fileno())
            shutil.copyfileobj(reader, writer)
    except BaseException:
        if created is not None:
            try:
                if os.path.samestat(created, output.stat()):
                    output.unlink()
            except OSError:
                pass
        raise
