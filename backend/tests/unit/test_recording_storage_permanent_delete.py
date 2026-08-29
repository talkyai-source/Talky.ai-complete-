"""Fail-closed permanent-erasure coverage for the S3 recording wrapper."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from app.domain.services.recording_service import S3Client


KEY = "recordings/tenant-a/call-a.wav"
BUCKET = "persisted-recording-bucket"


def _client_with_backend(backend: object) -> S3Client:
    """Build the thin wrapper without invoking boto3 credential discovery."""

    client = S3Client.__new__(S3Client)
    client.bucket = "configured-default-bucket"
    client._client = backend
    return client


def _not_found(operation: str = "HeadObject") -> ClientError:
    return ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}},
        operation,
    )


def test_delete_permanently_removes_only_exact_key_versions_and_markers() -> None:
    class VersionedBackend:
        def __init__(self) -> None:
            self.list_calls: list[dict] = []
            self.delete_calls: list[dict] = []
            self.head_calls: list[dict] = []

        def get_bucket_versioning(self, **kwargs):
            assert kwargs == {"Bucket": BUCKET}
            return {"Status": "Enabled"}

        def list_object_versions(self, **kwargs):
            self.list_calls.append(kwargs)
            if len(self.list_calls) == 1:
                return {
                    "Versions": [
                        {"Key": KEY, "VersionId": "version-current"},
                        {"Key": f"{KEY}.metadata", "VersionId": "neighbor-version"},
                    ],
                    "DeleteMarkers": [
                        {"Key": KEY, "VersionId": "marker-old"},
                        {"Key": f"{KEY}/child", "VersionId": "neighbor-marker"},
                    ],
                    "IsTruncated": True,
                    "NextKeyMarker": KEY,
                    "NextVersionIdMarker": "marker-old",
                }
            if len(self.list_calls) == 2:
                assert kwargs == {
                    "Bucket": BUCKET,
                    "Prefix": KEY,
                    "KeyMarker": KEY,
                    "VersionIdMarker": "marker-old",
                }
                return {
                    "Versions": [{"Key": KEY, "VersionId": "version-old"}],
                    "DeleteMarkers": [],
                    "IsTruncated": False,
                }
            assert kwargs == {"Bucket": BUCKET, "Prefix": KEY}
            return {
                # Prefix neighbours may remain. The exact-key verification must
                # ignore them rather than deleting another object's data.
                "Versions": [
                    {"Key": f"{KEY}.metadata", "VersionId": "neighbor-version"}
                ],
                "DeleteMarkers": [
                    {"Key": f"{KEY}/child", "VersionId": "neighbor-marker"}
                ],
                "IsTruncated": False,
            }

        def delete_objects(self, **kwargs):
            self.delete_calls.append(kwargs)
            return {"Deleted": kwargs["Delete"]["Objects"]}

        def head_object(self, **kwargs):
            self.head_calls.append(kwargs)
            raise _not_found()

    backend = VersionedBackend()
    _client_with_backend(backend).delete_permanently(KEY, BUCKET)

    assert len(backend.list_calls) == 3  # two discovery pages, then verification
    assert backend.delete_calls == [
        {
            "Bucket": BUCKET,
            "Delete": {
                "Objects": [
                    {"Key": KEY, "VersionId": "version-current"},
                    {"Key": KEY, "VersionId": "marker-old"},
                    {"Key": KEY, "VersionId": "version-old"},
                ],
                "Quiet": True,
            },
        }
    ]
    assert backend.head_calls == [{"Bucket": BUCKET, "Key": KEY}]


def test_delete_permanently_fails_closed_when_version_delete_reports_error() -> None:
    class DeleteErrorBackend:
        def __init__(self) -> None:
            self.head_called = False

        def get_bucket_versioning(self, **kwargs):
            return {"Status": "Enabled"}

        def list_object_versions(self, **kwargs):
            return {
                "Versions": [{"Key": KEY, "VersionId": "version-1"}],
                "IsTruncated": False,
            }

        def delete_objects(self, **kwargs):
            return {
                "Errors": [
                    {"Key": KEY, "VersionId": "version-1", "Code": "AccessDenied"}
                ]
            }

        def head_object(self, **kwargs):
            self.head_called = True
            raise AssertionError("HEAD must not mask a version-deletion failure")

    backend = DeleteErrorBackend()

    with pytest.raises(RuntimeError, match="object version deletion failed"):
        _client_with_backend(backend).delete_permanently(KEY, BUCKET)

    assert backend.head_called is False


def test_delete_permanently_fails_closed_when_an_exact_version_remains() -> None:
    class ResidualVersionBackend:
        def __init__(self) -> None:
            self.list_count = 0
            self.head_called = False

        def get_bucket_versioning(self, **kwargs):
            return {"Status": "Suspended"}

        def list_object_versions(self, **kwargs):
            self.list_count += 1
            version_id = "version-before-delete" if self.list_count == 1 else "residual"
            return {
                "Versions": [{"Key": KEY, "VersionId": version_id}],
                "IsTruncated": False,
            }

        def delete_objects(self, **kwargs):
            return {"Errors": []}

        def head_object(self, **kwargs):
            self.head_called = True
            raise AssertionError("HEAD cannot prove non-current versions are gone")

    backend = ResidualVersionBackend()

    with pytest.raises(RuntimeError, match="object versions remain"):
        _client_with_backend(backend).delete_permanently(KEY, BUCKET)

    assert backend.list_count == 2
    assert backend.head_called is False


def test_delete_permanently_fails_closed_when_head_still_finds_live_object() -> None:
    class LiveHeadBackend:
        def __init__(self) -> None:
            self.delete_calls: list[dict] = []

        def get_bucket_versioning(self, **kwargs):
            return {}

        def delete_object(self, **kwargs):
            self.delete_calls.append(kwargs)
            return {}

        def head_object(self, **kwargs):
            return {"ContentLength": 123}

    backend = LiveHeadBackend()

    with pytest.raises(RuntimeError, match="object remains readable"):
        _client_with_backend(backend).delete_permanently(KEY, BUCKET)

    assert backend.delete_calls == [{"Bucket": BUCKET, "Key": KEY}]


def test_delete_permanently_refuses_work_after_its_time_budget() -> None:
    class Backend:
        def get_bucket_versioning(self, **_kwargs):
            raise AssertionError("expired deletion must not contact storage")

    client = _client_with_backend(Backend())
    client._permanent_delete_timeout_seconds = -1.0

    with pytest.raises(TimeoutError, match="time budget"):
        client.delete_permanently(KEY, BUCKET)


def test_delete_permanently_caps_version_listing_pages() -> None:
    class EndlessVersionBackend:
        def __init__(self) -> None:
            self.list_count = 0

        def get_bucket_versioning(self, **_kwargs):
            return {"Status": "Enabled"}

        def list_object_versions(self, **_kwargs):
            self.list_count += 1
            return {
                "Versions": [],
                "DeleteMarkers": [],
                "IsTruncated": True,
                "NextKeyMarker": KEY,
                "NextVersionIdMarker": f"page-{self.list_count}",
            }

    backend = EndlessVersionBackend()
    client = _client_with_backend(backend)
    client._permanent_delete_max_pages = 1

    with pytest.raises(RuntimeError, match="page limit"):
        client.delete_permanently(KEY, BUCKET)

    assert backend.list_count == 1
