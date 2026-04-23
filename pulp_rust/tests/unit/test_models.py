"""Unit tests for pulp_rust model helpers and static methods."""

import django

django.setup()

from pulp_rust.app.models import (  # noqa: E402
    RustRemote,
    _parse_crate_relative_path,
    _strip_sparse_prefix,
)


class TestStripSparsePrefix:
    def test_with_sparse_prefix(self):
        assert _strip_sparse_prefix("sparse+https://index.crates.io/") == "https://index.crates.io/"

    def test_without_sparse_prefix(self):
        assert _strip_sparse_prefix("https://index.crates.io/") == "https://index.crates.io/"

    def test_sparse_prefix_only_stripped_once(self):
        url = "sparse+sparse+https://example.com/"
        assert _strip_sparse_prefix(url) == "sparse+https://example.com/"

    def test_empty_string(self):
        assert _strip_sparse_prefix("") == ""


class TestParseCrateRelativePath:
    def test_simple_crate(self):
        name, version = _parse_crate_relative_path("serde/serde-1.0.0.crate")
        assert name == "serde"
        assert version == "1.0.0"

    def test_hyphenated_crate_name(self):
        name, version = _parse_crate_relative_path("serde-json/serde-json-1.0.140.crate")
        assert name == "serde-json"
        assert version == "1.0.140"

    def test_underscored_crate_name(self):
        name, version = _parse_crate_relative_path("serde_json/serde_json-1.0.140.crate")
        assert name == "serde_json"
        assert version == "1.0.140"

    def test_prerelease_version(self):
        name, version = _parse_crate_relative_path("foo/foo-1.0.0-alpha.1.crate")
        assert name == "foo"
        assert version == "1.0.0-alpha.1"

    def test_build_metadata_version(self):
        name, version = _parse_crate_relative_path("foo/foo-1.0.0+build.123.crate")
        assert name == "foo"
        assert version == "1.0.0+build.123"

    def test_single_char_crate(self):
        name, version = _parse_crate_relative_path("a/a-0.1.0.crate")
        assert name == "a"
        assert version == "0.1.0"

    def test_two_digit_version(self):
        name, version = _parse_crate_relative_path("itoa/itoa-1.0.crate")
        assert name == "itoa"
        assert version == "1.0"


class TestGetRemoteArtifactContentType:
    def test_crate_file(self):
        from pulp_rust.app.models import RustPackage

        assert RustRemote.get_remote_artifact_content_type("serde/serde-1.0.0.crate") is RustPackage

    def test_non_crate_file(self):
        assert RustRemote.get_remote_artifact_content_type("se/rd/serde") is None

    def test_none_path(self):
        assert RustRemote.get_remote_artifact_content_type(None) is None

    def test_empty_path(self):
        assert RustRemote.get_remote_artifact_content_type("") is None
