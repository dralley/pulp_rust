"""Tests for Cargo pull-through caching via the sparse index proxy."""

import hashlib
import json
from urllib.parse import urljoin

import pytest

from pulp_rust.tests.functional.utils import (
    CRATES_IO_URL,
    assert_index_entry_matches_upstream,
    download_file,
    get_index_entry,
)


def test_pull_through_sparse_index(
    rust_remote_factory,
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
):
    """Verify that the sparse index metadata is proxied from the upstream remote."""
    remote = rust_remote_factory(url=CRATES_IO_URL)
    repository = rust_repo_factory(remote=remote.pulp_href)
    distribution = rust_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    # Request sparse index metadata for a well-known small crate
    # The sparse index path for "itoa" (4 chars) is "it/oa/itoa"
    index_url = urljoin(cargo_registry_url(distribution.base_path), "it/oa/itoa")
    downloaded = download_file(index_url)
    assert downloaded.response_obj.status == 200

    # The response should be newline-delimited JSON
    body = downloaded.body.decode("utf-8")
    assert body.strip()
    # Each line should be valid JSON containing the crate name
    for line in body.strip().split("\n"):
        assert '"name":"itoa"' in line or '"name": "itoa"' in line


@pytest.mark.parametrize("policy", ["on_demand", "streamed"])
def test_pull_through_crate_download(
    rust_remote_factory,
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    policy,
):
    """Verify that .crate files can be downloaded via pull-through."""
    remote = rust_remote_factory(url=CRATES_IO_URL, policy=policy)
    repository = rust_repo_factory(remote=remote.pulp_href)
    distribution = rust_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    unit_path = "api/v1/crates/itoa/1.0.0/download"
    pulp_unit_url = urljoin(cargo_registry_url(distribution.base_path), unit_path)
    downloaded = download_file(pulp_unit_url)
    assert downloaded.response_obj.status == 200
    assert len(downloaded.body) > 0


@pytest.mark.parametrize("policy", ["on_demand", "streamed"])
def test_pull_through_repeated_download(
    rust_remote_factory,
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    policy,
):
    """Downloading the same crate twice should work for both policies."""
    remote = rust_remote_factory(url=CRATES_IO_URL, policy=policy)
    repository = rust_repo_factory(remote=remote.pulp_href)
    distribution = rust_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    unit_path = "api/v1/crates/itoa/1.0.0/download"
    pulp_unit_url = urljoin(cargo_registry_url(distribution.base_path), unit_path)

    first = download_file(pulp_unit_url)
    assert first.response_obj.status == 200

    second = download_file(pulp_unit_url)
    assert second.response_obj.status == 200

    # Both downloads should return identical content
    assert hashlib.sha256(first.body).hexdigest() == hashlib.sha256(second.body).hexdigest()


def test_pull_through_on_demand_creates_content(
    delete_orphans_pre,
    rust_remote_factory,
    rust_repo_factory,
    rust_distribution_factory,
    rust_content_api_client,
    cargo_registry_url,
):
    """on_demand pull-through should create a RustPackage record and cache the artifact."""
    remote = rust_remote_factory(url=CRATES_IO_URL, policy="on_demand")
    repository = rust_repo_factory(remote=remote.pulp_href)
    distribution = rust_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    unit_path = "api/v1/crates/itoa/1.0.0/download"
    pulp_unit_url = urljoin(cargo_registry_url(distribution.base_path), unit_path)
    downloaded = download_file(pulp_unit_url)
    assert downloaded.response_obj.status == 200

    # A RustPackage record should have been created
    content_response = rust_content_api_client.list(name="itoa", vers="1.0.0")
    assert content_response.count == 1


def test_pull_through_on_demand_serves_from_cache_without_remote(
    rust_remote_factory,
    rust_repo_factory,
    rust_distribution_factory,
    rust_distro_api_client,
    monitor_task,
    cargo_registry_url,
):
    """on_demand: after caching, content should be served even after removing the remote."""
    remote = rust_remote_factory(url=CRATES_IO_URL, policy="on_demand")
    repository = rust_repo_factory(remote=remote.pulp_href)
    distribution = rust_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    unit_path = "api/v1/crates/itoa/1.0.0/download"
    pulp_unit_url = urljoin(cargo_registry_url(distribution.base_path), unit_path)
    first_download = download_file(pulp_unit_url)
    first_checksum = hashlib.sha256(first_download.body).hexdigest()

    # Remove the remote from the distribution
    monitor_task(
        rust_distro_api_client.partial_update(distribution.pulp_href, {"remote": None}).task
    )

    # Content should still be served from cache
    second_download = download_file(pulp_unit_url)
    assert second_download.response_obj.status == 200
    assert hashlib.sha256(second_download.body).hexdigest() == first_checksum


def test_pull_through_streamed_no_content_created(
    delete_orphans_pre,
    rust_remote_factory,
    rust_repo_factory,
    rust_distribution_factory,
    rust_content_api_client,
    cargo_registry_url,
):
    """streamed: pull-through should NOT create a RustPackage record."""
    remote = rust_remote_factory(url=CRATES_IO_URL, policy="streamed")
    repository = rust_repo_factory(remote=remote.pulp_href)
    distribution = rust_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    unit_path = "api/v1/crates/itoa/1.0.0/download"
    pulp_unit_url = urljoin(cargo_registry_url(distribution.base_path), unit_path)
    downloaded = download_file(pulp_unit_url)
    assert downloaded.response_obj.status == 200

    # No RustPackage record should have been created
    content_response = rust_content_api_client.list(name="itoa", vers="1.0.0")
    assert content_response.count == 0


def test_pull_through_multiple_crates_on_demand(
    delete_orphans_pre,
    rust_remote_factory,
    rust_repo_factory,
    rust_distribution_factory,
    rust_content_api_client,
    rust_repo_api_client,
    cargo_registry_url,
):
    """on_demand: downloading multiple crates should cache all of them."""
    remote = rust_remote_factory(url=CRATES_IO_URL, policy="on_demand")
    repository = rust_repo_factory(remote=remote.pulp_href)
    distribution = rust_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    base = cargo_registry_url(distribution.base_path)
    download_file(urljoin(base, "api/v1/crates/itoa/1.0.0/download"))
    download_file(urljoin(base, "api/v1/crates/cfg-if/1.0.0/download"))

    # Both should have content records
    assert rust_content_api_client.list(name="itoa", vers="1.0.0").count == 1
    assert rust_content_api_client.list(name="cfg-if", vers="1.0.0").count == 1

    # Content should have been automatically added to the repository
    repository = rust_repo_api_client.read(repository.pulp_href)
    assert not repository.latest_version_href.endswith("/versions/0/")


def test_pull_through_on_demand_preserves_metadata(
    delete_orphans_pre,
    rust_remote_factory,
    rust_repo_factory,
    rust_distribution_factory,
    rust_content_api_client,
    cargo_registry_url,
):
    """on_demand: cached content should have full metadata (deps, features).

    Ensure that all metadata is saved appropriately when a package is created via
    pull-through caching.
    """
    remote = rust_remote_factory(url=CRATES_IO_URL, policy="on_demand")
    repository = rust_repo_factory(remote=remote.pulp_href)
    distribution = rust_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    # serde has dependencies (serde_derive) and features in most versions
    crate_name, crate_version = "serde", "1.0.210"
    unit_path = f"api/v1/crates/{crate_name}/{crate_version}/download"
    pulp_unit_url = urljoin(cargo_registry_url(distribution.base_path), unit_path)
    download_file(pulp_unit_url)

    # Verify the content record was created with full metadata
    content_response = rust_content_api_client.list(name=crate_name, vers=crate_version)
    assert content_response.count == 1
    content = content_response.results[0]

    # Should have dependencies (serde has serde_derive as an optional dep)
    assert len(content.dependencies) > 0
    dep_names = [d.name for d in content.dependencies]
    assert "serde_derive" in dep_names

    # Should have features
    assert len(content.features) > 0
    assert "derive" in content.features

    # Fetch the sparse index from Pulp (now served from local data)
    index_url = urljoin(cargo_registry_url(distribution.base_path), f"se/rd/{crate_name}")
    index_response = download_file(index_url)
    assert index_response.response_obj.status == 200

    body = index_response.body.decode("utf-8")
    lines = body.strip().split("\n")
    # Find the line for our version
    version_entry = None
    for line in lines:
        entry = json.loads(line)
        if entry["vers"] == crate_version:
            version_entry = entry
            break

    assert version_entry is not None, f"Version {crate_version} not found in index"
    assert len(version_entry["deps"]) > 0, "Index entry has no dependencies"
    index_dep_names = [d["name"] for d in version_entry["deps"]]
    assert "serde_derive" in index_dep_names
    assert len(version_entry["features"]) > 0, "Index entry has no features"


# ---------------------------------------------------------------------------
# Index fidelity tests: compare Pulp output against crates.io for each mode
# ---------------------------------------------------------------------------


def test_index_fidelity_streamed(
    rust_remote_factory,
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    upstream_index_entry,
):
    """streamed: proxied sparse index entry should match crates.io exactly."""
    remote = rust_remote_factory(url=CRATES_IO_URL, policy="streamed")
    repository = rust_repo_factory(remote=remote.pulp_href)
    distribution = rust_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    base = cargo_registry_url(distribution.base_path)
    pulp_entry = get_index_entry(base, "se/rd/serde", "1.0.210")
    assert_index_entry_matches_upstream(pulp_entry, upstream_index_entry)


def test_index_fidelity_on_demand_proxied(
    rust_remote_factory,
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    upstream_index_entry,
):
    """on_demand: index with no local content proxies upstream and should match."""
    remote = rust_remote_factory(url=CRATES_IO_URL, policy="on_demand")
    repository = rust_repo_factory(remote=remote.pulp_href)
    distribution = rust_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    base = cargo_registry_url(distribution.base_path)

    # No local content in this repo, so the index is proxied from upstream
    pulp_entry = get_index_entry(base, "se/rd/serde", "1.0.210")
    assert_index_entry_matches_upstream(pulp_entry, upstream_index_entry)


def test_index_fidelity_on_demand_cached(
    delete_orphans_pre,
    rust_remote_factory,
    rust_repo_factory,
    rust_distribution_factory,
    rust_content_api_client,
    cargo_registry_url,
    upstream_index_entry,
):
    """on_demand: after caching, the locally-served index entry should match crates.io."""
    remote = rust_remote_factory(url=CRATES_IO_URL, policy="on_demand")
    repository = rust_repo_factory(remote=remote.pulp_href)
    distribution = rust_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    base = cargo_registry_url(distribution.base_path)

    # Download the .crate to trigger content creation
    download_file(urljoin(base, "api/v1/crates/serde/1.0.210/download"))

    # Verify content was cached
    content = rust_content_api_client.list(name="serde", vers="1.0.210")
    assert content.count == 1, "Content was not cached after on_demand download"

    # Now the index is served from local data — compare against upstream
    pulp_entry = get_index_entry(base, "se/rd/serde", "1.0.210")
    assert_index_entry_matches_upstream(pulp_entry, upstream_index_entry)


def test_pull_through_index_falls_back_to_cache_when_upstream_unavailable(
    delete_orphans_pre,
    rust_remote_factory,
    rust_remote_api_client,
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    monitor_task,
):
    """on_demand: if the upstream is unreachable, the index should fall back to cached content."""
    remote = rust_remote_factory(url=CRATES_IO_URL, policy="on_demand")
    repository = rust_repo_factory(remote=remote.pulp_href)
    distribution = rust_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    base = cargo_registry_url(distribution.base_path)

    # Cache itoa via pull-through
    download_file(urljoin(base, "api/v1/crates/itoa/1.0.0/download"))

    # Point the remote at an unreachable URL
    monitor_task(
        rust_remote_api_client.partial_update(
            remote.pulp_href, {"url": "sparse+https://localhost:1/"}
        ).task
    )

    # The index should fall back to locally cached content
    entry = get_index_entry(base, "it/oa/itoa", "1.0.0")
    assert entry["name"] == "itoa"
    assert entry["vers"] == "1.0.0"
