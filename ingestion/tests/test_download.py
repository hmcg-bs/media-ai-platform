"""Tests for ingestion/download.py (offline, mocked network)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from ingestion.download import _get_extension_from_url, download_creatives
from ingestion.models import CompetitorAd


class TestGetExtensionFromUrl:
    """Test URL extension parsing."""

    def test_jpg_extension(self) -> None:
        assert _get_extension_from_url("https://example.com/image.jpg") == "jpg"

    def test_jpeg_extension(self) -> None:
        assert _get_extension_from_url("https://example.com/image.jpeg") == "jpeg"

    def test_webp_extension(self) -> None:
        assert _get_extension_from_url("https://example.com/image.webp") == "webp"

    def test_png_extension(self) -> None:
        assert _get_extension_from_url("https://example.com/image.png") == "png"

    def test_gif_extension(self) -> None:
        assert _get_extension_from_url("https://example.com/image.gif") == "gif"

    def test_no_extension_defaults_to_jpg(self) -> None:
        assert _get_extension_from_url("https://example.com/image") == "jpg"

    def test_query_params_ignored(self) -> None:
        # The extension is extracted before query params.
        assert _get_extension_from_url("https://example.com/image.jpg?w=300&h=300") == "jpg"

    def test_case_insensitive(self) -> None:
        assert _get_extension_from_url("https://example.com/IMAGE.JPG") == "jpg"
        assert _get_extension_from_url("https://example.com/IMAGE.WEBP") == "webp"


class TestDownloadCreatives:
    """Test the download_creatives function."""

    def test_no_images_skipped(self, tmp_path: Path) -> None:
        """Ads with no images are skipped."""
        media_dir = tmp_path / "media"

        ad = CompetitorAd(
            ad_archive_id="no_images_ad",
            image_urls=[],  # Empty.
        )

        download_creatives([ad], media_dir)

        # No download attempted.
        assert not list(media_dir.glob("*"))

    def test_successful_download(self, tmp_path: Path) -> None:
        """Successful image download sets local_image_path."""
        media_dir = tmp_path / "media"

        ad = CompetitorAd(
            ad_archive_id="test_ad_123",
            image_urls=["https://example.com/test_image.jpg"],
        )

        mock_data = b"fake_image_data"

        def mock_urlopen_fn(url, *args, **kwargs):
            mock_response = Mock()
            mock_response.read = Mock(return_value=mock_data)
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=None)
            return mock_response

        with patch("ingestion.download.urlopen", side_effect=mock_urlopen_fn):
            download_creatives([ad], media_dir)

        # File should be written.
        expected_file = media_dir / "test_ad_123.jpg"
        assert expected_file.exists()
        assert expected_file.read_bytes() == mock_data

        # Ad should have local_image_path set.
        assert ad.local_image_path == str(expected_file)

    def test_download_failure_no_drop(self, tmp_path: Path) -> None:
        """Download failure is logged; ad is NOT dropped."""
        media_dir = tmp_path / "media"

        ad = CompetitorAd(
            ad_archive_id="failed_ad_456",
            image_urls=["https://example.com/broken.jpg"],
        )

        with patch("ingestion.download.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = Exception("Network error")

            download_creatives([ad], media_dir)

        # No file created.
        assert not list(media_dir.glob("*"))

        # Ad is NOT dropped; it's still in the list passed in.
        assert ad.ad_archive_id == "failed_ad_456"
        assert ad.local_image_path is None  # Fallback to snapshot_url.

    def test_multiple_ads_partial_failure(self, tmp_path: Path) -> None:
        """Some ads succeed, some fail; all are processed."""
        media_dir = tmp_path / "media"

        ads = [
            CompetitorAd(
                ad_archive_id="success_ad_1",
                image_urls=["https://example.com/good1.jpg"],
            ),
            CompetitorAd(
                ad_archive_id="fail_ad_2",
                image_urls=["https://example.com/bad.jpg"],
            ),
            CompetitorAd(
                ad_archive_id="success_ad_3",
                image_urls=["https://example.com/good3.png"],
            ),
        ]

        mock_data_good = b"good_image_data"
        mock_data_good_3 = b"good_image_data_3"

        def mock_urlopen_side_effect(url: str, *args, **kwargs):
            if "bad" in url:
                raise Exception("Network error")
            mock_response = Mock()
            if "good1" in url:
                mock_response.read = Mock(return_value=mock_data_good)
            else:
                mock_response.read = Mock(return_value=mock_data_good_3)
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=None)
            return mock_response

        with patch("ingestion.download.urlopen", side_effect=mock_urlopen_side_effect):
            download_creatives(ads, media_dir)

        # Two files created (success_ad_1 and success_ad_3).
        files = list(media_dir.glob("*"))
        assert len(files) == 2

        # Check success ads have local_image_path.
        assert ads[0].local_image_path is not None
        assert ads[2].local_image_path is not None

        # Check fail ad has no local_image_path.
        assert ads[1].local_image_path is None

    def test_extension_from_url_respected(self, tmp_path: Path) -> None:
        """File extension is extracted from the URL, not forced."""
        media_dir = tmp_path / "media"

        ad = CompetitorAd(
            ad_archive_id="webp_ad",
            image_urls=["https://example.com/image.webp"],
        )

        mock_data = b"webp_data"

        def mock_urlopen_fn(url, *args, **kwargs):
            mock_response = Mock()
            mock_response.read = Mock(return_value=mock_data)
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=None)
            return mock_response

        with patch("ingestion.download.urlopen", side_effect=mock_urlopen_fn):
            download_creatives([ad], media_dir)

        # File should use .webp extension.
        expected_file = media_dir / "webp_ad.webp"
        assert expected_file.exists()

    def test_media_dir_created(self, tmp_path: Path) -> None:
        """Media directory is created if it doesn't exist."""
        media_dir = tmp_path / "does" / "not" / "exist" / "yet"

        ad = CompetitorAd(
            ad_archive_id="test_ad",
            image_urls=["https://example.com/test.jpg"],
        )

        def mock_urlopen_fn(url, *args, **kwargs):
            mock_response = Mock()
            mock_response.read = Mock(return_value=b"data")
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=None)
            return mock_response

        with patch("ingestion.download.urlopen", side_effect=mock_urlopen_fn):
            download_creatives([ad], media_dir)

        # Directory should exist.
        assert media_dir.exists()
        assert (media_dir / "test_ad.jpg").exists()
