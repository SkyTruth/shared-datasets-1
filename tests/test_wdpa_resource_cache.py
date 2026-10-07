"""Linux cache contracts, using small synthetic files and real process ancestry."""

from contextlib import contextmanager
import ctypes
import json
import mmap
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from ingestion.wdpa_monthly import resources


MIB = 1024**2
WRITER = r"""
import hashlib, json, os, pathlib, sys
files = []
block = bytes(range(256)) * 4096
for name, size in json.loads(sys.argv[1]):
    path = pathlib.Path(name)
    stream = path.open("w+b", buffering=0)
    digest = hashlib.sha256()
    for offset in range(0, size, len(block)):
        chunk = block[:min(len(block), size - offset)]
        assert stream.write(chunk) == len(chunk)
        digest.update(chunk)
    os.fsync(stream.fileno())
    path.unlink()
    files.append((stream, digest.hexdigest(), size))
print(json.dumps({"pid": os.getpid(), "files": [
    {"fd": stream.fileno(), "size": size, "sha256": digest}
    for stream, digest, size in files
]}), flush=True)
assert sys.stdin.readline().strip() == "verify"
verified = []
for stream, digest, size in files:
    stream.seek(0)
    actual = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        actual.update(chunk)
    verified.append({"size": os.fstat(stream.fileno()).st_size, "sha256": actual.hexdigest()})
    stream.close()
print(json.dumps(verified), flush=True)
"""


def resident_bytes(pid, descriptor, size):
    """Inspect residency without reading pages; unmap before cache advice."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mincore.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p)
    libc.mincore.restype = ctypes.c_int
    pinned = os.open(f"/proc/{pid}/fd/{descriptor}", os.O_RDONLY)
    try:
        with mmap.mmap(pinned, size, access=mmap.ACCESS_COPY) as mapping:
            address = ctypes.addressof(ctypes.c_char.from_buffer(mapping))
            page_size = os.sysconf("SC_PAGE_SIZE")
            pages = (size + page_size - 1) // page_size
            vector = (ctypes.c_ubyte * pages)()
            if libc.mincore(address, size, vector) != 0:
                raise OSError(ctypes.get_errno(), "mincore failed")
            return min(size, sum(value & 1 for value in vector) * page_size)
    finally:
        os.close(pinned)


@unittest.skipUnless(sys.platform == "linux", "Linux open-file cache contract")
class DeletedScratchCacheTests(unittest.TestCase):
    def setUp(self):
        work_root = Path(os.environ.get(
            "SHARED_DATASETS_WORKDIR",
            Path(tempfile.gettempdir()) / "shared-datasets-1",
        ))
        work_root = work_root / "_scratch" / "wdpa-cache-tests"
        work_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=work_root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scratch = self.root / "owned"
        self.scratch.mkdir()

    @contextmanager
    def deleted_files(self, owned_size=MIB, outside_size=MIB):
        paths = (self.scratch / "native-temp.fgb", self.root / "outside.bin")
        config = list(zip(map(str, paths), (owned_size, outside_size)))
        launcher = "import subprocess,sys; raise SystemExit(subprocess.call(sys.argv[1:]))"
        with subprocess.Popen(
            [sys.executable, "-c", launcher, sys.executable, "-c", WRITER, json.dumps(config)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        ) as child:
            info = json.loads(child.stdout.readline())
            self.assertTrue(all(not path.exists() for path in paths))
            try:
                yield info
            finally:
                child.stdin.write("verify\n")
                child.stdin.flush()
                verified = json.loads(child.stdout.readline())
                self.assertEqual(child.wait(timeout=5), 0)
                self.assertEqual(verified, [
                    {"size": item["size"], "sha256": item["sha256"]}
                    for item in info["files"]
                ])

    def residency(self, info):
        return [
            resident_bytes(info["pid"], item["fd"], item["size"])
            for item in info["files"]
        ]

    def test_releases_real_pages_for_grandchild_without_changing_bytes(self):
        with self.deleted_files(64 * MIB, 8 * MIB) as info:
            self.assertEqual(self.residency(info), [64 * MIB, 8 * MIB])
            resources.release_file_cache(self.scratch)
            self.assertEqual(self.residency(info), [0, 8 * MIB])
            resources.release_file_cache(self.scratch)
            self.assertEqual(self.residency(info), [0, 8 * MIB])

    def test_reused_descriptor_cannot_advise_an_outside_file(self):
        with self.deleted_files() as info:
            real_open = os.open
            owned = f"/proc/{info['pid']}/fd/{info['files'][0]['fd']}"
            outside = f"/proc/{info['pid']}/fd/{info['files'][1]['fd']}"

            def redirected(path, flags):
                return real_open(outside if str(path) == owned else path, flags)

            with mock.patch.object(os, "open", side_effect=redirected), \
                 mock.patch.object(os, "posix_fadvise") as advise:
                resources.release_file_cache(self.scratch)
                advise.assert_not_called()
            self.assertEqual(self.residency(info), [MIB, MIB])

    def test_cache_advice_failure_is_visible(self):
        with self.deleted_files():
            with mock.patch.object(os, "posix_fadvise", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(PermissionError, "denied"):
                    resources.release_file_cache(self.scratch)

    def test_closed_descriptor_is_an_expected_sampling_race(self):
        with self.deleted_files() as info:
            real_open = os.open
            owned = f"/proc/{info['pid']}/fd/{info['files'][0]['fd']}"

            def disappeared(path, flags):
                if str(path) == owned:
                    raise FileNotFoundError("native writer closed its file")
                return real_open(path, flags)

            with mock.patch.object(os, "open", side_effect=disappeared), \
                 mock.patch.object(os, "posix_fadvise") as advise:
                resources.release_file_cache(self.scratch)
                advise.assert_not_called()

    def test_exited_thread_does_not_hide_other_descendants(self):
        with self.deleted_files() as info:
            real_iterdir = Path.iterdir
            tasks = Path(f"/proc/{os.getpid()}/task")

            def with_exited_thread(path):
                if path == tasks:
                    return iter([*real_iterdir(path), tasks / "999999999"])
                return real_iterdir(path)

            with mock.patch.object(Path, "iterdir", with_exited_thread):
                resources.release_file_cache(self.scratch)
            self.assertEqual(self.residency(info), [0, MIB])

    def test_exited_descendant_does_not_hide_live_descendants(self):
        with self.deleted_files() as info:
            real_read = Path.read_text
            tasks = Path(f"/proc/{os.getpid()}/task")

            def with_exited_child(path, *args, **kwargs):
                value = real_read(path, *args, **kwargs)
                if path.name == "children" and path.is_relative_to(tasks):
                    return value + " 999999999"
                return value

            with mock.patch.object(Path, "read_text", with_exited_child):
                resources.release_file_cache(self.scratch)
            self.assertEqual(self.residency(info), [0, MIB])

    def test_missing_or_inaccessible_current_process_is_a_failure(self):
        real_iterdir = Path.iterdir
        tasks = Path(f"/proc/{os.getpid()}/task")
        for error in (FileNotFoundError("missing proc"), PermissionError("denied")):
            with self.subTest(error=type(error).__name__):
                def unavailable(path):
                    if path == tasks:
                        raise error
                    return real_iterdir(path)

                with mock.patch.object(Path, "iterdir", unavailable):
                    with self.assertRaises(type(error)):
                        resources.release_file_cache(self.scratch)


if __name__ == "__main__":
    unittest.main()
