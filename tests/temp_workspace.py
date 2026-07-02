from __future__ import annotations

import contextlib
import os
import pathlib
import tempfile


@contextlib.contextmanager
def workspace(files: dict[str, str]):
    """Run a test body inside a temp cwd populated with the given files.

    Keys are relative paths; parent directories are created as needed. The
    original working directory is restored on exit.
    """
    original = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            for name, content in files.items():
                path = pathlib.Path(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            yield pathlib.Path(tmp)
        finally:
            os.chdir(original)
