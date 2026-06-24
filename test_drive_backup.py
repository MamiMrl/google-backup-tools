import hashlib
from pathlib import Path

import pytest

from drive_backup import (
    get_type_folder,
    candidate_path,
    resolve_path,
    compute_md5,
    is_verified_on_ssd,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_file(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def md5_of(content: bytes) -> str:
    return hashlib.md5(content).hexdigest()


def fake_file(
    name: str = "video.mp4",
    mime: str = "video/mp4",
    size: int = 100 * 1024 * 1024,
    year: str = "2024",
    file_id: str = "abc123",
    md5: str | None = None,
) -> dict:
    f = {
        "id": file_id,
        "name": name,
        "mimeType": mime,
        "size": str(size),
        "createdTime": f"{year}-06-01T00:00:00.000Z",
    }
    if md5 is not None:
        f["md5Checksum"] = md5
    return f


# ---------------------------------------------------------------------------
# get_type_folder
# ---------------------------------------------------------------------------

class TestGetTypeFolder:
    def test_video_mp4(self):
        assert get_type_folder("video/mp4") == "Videos"

    def test_video_quicktime(self):
        assert get_type_folder("video/quicktime") == "Videos"

    def test_video_matroska(self):
        assert get_type_folder("video/x-matroska") == "Videos"

    def test_image_jpeg(self):
        assert get_type_folder("image/jpeg") == "Images"

    def test_image_png(self):
        assert get_type_folder("image/png") == "Images"

    def test_image_heic(self):
        assert get_type_folder("image/heic") == "Images"

    def test_image_heif(self):
        assert get_type_folder("image/heif") == "Images"

    def test_pdf(self):
        assert get_type_folder("application/pdf") == "PDFs"

    def test_zip(self):
        assert get_type_folder("application/zip") == "Archives"

    def test_rar(self):
        assert get_type_folder("application/x-rar-compressed") == "Archives"

    def test_7z(self):
        assert get_type_folder("application/x-7z-compressed") == "Archives"

    def test_gzip(self):
        assert get_type_folder("application/gzip") == "Archives"

    def test_x_compressed(self):
        assert get_type_folder("application/x-compressed") == "Archives"

    def test_x_zip_compressed(self):
        assert get_type_folder("application/x-zip-compressed") == "Archives"

    def test_tar(self):
        assert get_type_folder("application/x-tar") == "Archives"

    def test_bzip2(self):
        assert get_type_folder("application/x-bzip2") == "Archives"

    def test_unknown_falls_back_to_other(self):
        assert get_type_folder("text/plain") == "Other"

    def test_octet_stream_falls_back_to_other(self):
        assert get_type_folder("application/octet-stream") == "Other"


# ---------------------------------------------------------------------------
# resolve_path
# ---------------------------------------------------------------------------

class TestResolvePath:
    def test_returns_candidate_when_destination_missing(self, tmp_path):
        f = fake_file(name="clip.mp4", mime="video/mp4", size=200, year="2024")
        result = resolve_path(f, tmp_path)
        assert result == tmp_path / "Videos" / "2024" / "clip.mp4"

    def test_returns_candidate_when_size_matches(self, tmp_path):
        f = fake_file(name="clip.mp4", mime="video/mp4", size=200, year="2024")
        dest = tmp_path / "Videos" / "2024" / "clip.mp4"
        make_file(dest, b"x" * 200)
        result = resolve_path(f, tmp_path)
        assert result == dest

    def test_appends_file_id_on_size_collision(self, tmp_path):
        f = fake_file(name="clip.mp4", mime="video/mp4", size=200, year="2024", file_id="XYZ999")
        dest = tmp_path / "Videos" / "2024" / "clip.mp4"
        make_file(dest, b"x" * 999)  # different size — different file at same path
        result = resolve_path(f, tmp_path)
        assert result == tmp_path / "Videos" / "2024" / "clip_XYZ999.mp4"


# ---------------------------------------------------------------------------
# compute_md5
# ---------------------------------------------------------------------------

class TestComputeMd5:
    def test_matches_known_hash(self, tmp_path):
        content = b"hello world"
        path = make_file(tmp_path / "test.bin", content)
        assert compute_md5(path) == md5_of(content)

    def test_differs_for_different_content(self, tmp_path):
        a = make_file(tmp_path / "a.bin", b"aaa")
        b = make_file(tmp_path / "b.bin", b"bbb")
        assert compute_md5(a) != compute_md5(b)


# ---------------------------------------------------------------------------
# is_verified_on_ssd
# ---------------------------------------------------------------------------

class TestIsVerifiedOnSsd:
    def test_false_when_file_missing(self, tmp_path):
        f = fake_file(size=100)
        assert not is_verified_on_ssd(f, tmp_path / "missing.mp4")

    def test_false_when_size_mismatch(self, tmp_path):
        content = b"x" * 50
        path = make_file(tmp_path / "clip.mp4", content)
        f = fake_file(size=999)  # different from actual 50 bytes
        assert not is_verified_on_ssd(f, path)

    def test_true_when_size_matches_and_no_md5_in_metadata(self, tmp_path):
        content = b"x" * 100
        path = make_file(tmp_path / "clip.mp4", content)
        f = fake_file(size=100)  # no md5Checksum key
        assert is_verified_on_ssd(f, path)

    def test_true_when_size_and_md5_match(self, tmp_path):
        content = b"hello drive"
        path = make_file(tmp_path / "clip.mp4", content)
        f = fake_file(size=len(content), md5=md5_of(content))
        assert is_verified_on_ssd(f, path)

    def test_false_when_md5_mismatch(self, tmp_path):
        content = b"hello drive"
        path = make_file(tmp_path / "clip.mp4", content)
        f = fake_file(size=len(content), md5="000000000000000000000000deadbeef")
        assert not is_verified_on_ssd(f, path)
