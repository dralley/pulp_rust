"""Tests for the Cargo publish API (PUT /api/v1/crates/new).

NOTE: The test helpers (build_publish_metadata, cargo_publish) reuse
pulp_rust's own extract_cargo_toml / extract_dependencies to build the
publish request — the same code the server uses to process it.  The index
fidelity tests below validate that code path by comparing Pulp's index
output against independently-fetched crates.io data.  If those app
functions ever produce wrong results, these fidelity tests are what will
catch it.  Other test modules (auth, yank) also depend on the same helpers,
so keep these fidelity checks passing.
"""

from urllib.parse import urljoin

import pytest

from pulp_rust.tests.functional.utils import (
    CRATES_IO_URL,
    assert_index_entry_matches_upstream,
    build_publish_metadata,
    cargo_publish,
    download_crate_from_upstream,
    download_file,
    get_index_entry,
)


@pytest.fixture
def admin_auth_headers(rust_token_factory, pulp_admin_user):
    """Create a Cargo token for the admin user and return auth headers."""
    token = rust_token_factory(pulp_admin_user)
    return {"Authorization": token.token}


def test_cargo_publish_and_index_fidelity(
    delete_orphans_pre,
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    upstream_index_entry,
    admin_auth_headers,
):
    """Publish a crate via the Cargo publish API and verify the index matches crates.io."""
    crate_name = "serde"
    crate_version = "1.0.210"

    crate_path, cksum = download_crate_from_upstream(crate_name, crate_version)
    with open(crate_path, "rb") as f:
        crate_bytes = f.read()

    metadata = build_publish_metadata(crate_path, crate_name, crate_version)

    repository = rust_repo_factory()
    distribution = rust_distribution_factory(repository=repository.pulp_href, allow_uploads=True)
    base = cargo_registry_url(distribution.base_path)

    response = cargo_publish(base, metadata, crate_bytes, headers=admin_auth_headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert "warnings" in result

    # Fetch the index from Pulp and compare against crates.io
    pulp_entry = get_index_entry(base, "se/rd/serde", "1.0.210")
    assert_index_entry_matches_upstream(pulp_entry, upstream_index_entry)


def test_cargo_publish_duplicate_rejected(
    delete_orphans_pre,
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    admin_auth_headers,
):
    """Publishing the same crate version twice should be rejected."""
    crate_name = "serde"
    crate_version = "1.0.210"

    crate_path, _ = download_crate_from_upstream(crate_name, crate_version)
    with open(crate_path, "rb") as f:
        crate_bytes = f.read()

    metadata = build_publish_metadata(crate_path, crate_name, crate_version)

    repository = rust_repo_factory()
    distribution = rust_distribution_factory(repository=repository.pulp_href, allow_uploads=True)
    base = cargo_registry_url(distribution.base_path)

    # First publish should succeed
    response = cargo_publish(base, metadata, crate_bytes, headers=admin_auth_headers)
    assert response.status_code == 200, response.text

    # Second publish of the same version should be rejected
    response = cargo_publish(base, metadata, crate_bytes, headers=admin_auth_headers)
    assert response.status_code == 400
    errors = response.json()["errors"]
    assert any("already uploaded" in e["detail"] for e in errors)


def test_cargo_publish_ignores_tampered_json_metadata(
    delete_orphans_pre,
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    upstream_index_entry,
    admin_auth_headers,
):
    """Tampered JSON metadata should be ignored in favor of the Cargo.toml in the tarball.

    See: https://github.com/rust-lang/cargo/issues/14492
    """
    crate_name = "serde"
    crate_version = "1.0.210"

    crate_path, cksum = download_crate_from_upstream(crate_name, crate_version)
    with open(crate_path, "rb") as f:
        crate_bytes = f.read()

    # Build correct metadata, then tamper with it
    metadata = build_publish_metadata(crate_path, crate_name, crate_version)
    metadata["deps"] = [
        {
            "name": "evil-dep",
            "version_req": "^1.0",
            "features": [],
            "optional": False,
            "default_features": True,
            "target": None,
            "kind": "normal",
            "registry": None,
            "explicit_name_in_toml": None,
        }
    ]
    metadata["features"] = {"backdoor": ["evil-dep"]}
    metadata["links"] = "tampered-link"

    repository = rust_repo_factory()
    distribution = rust_distribution_factory(repository=repository.pulp_href, allow_uploads=True)
    base = cargo_registry_url(distribution.base_path)

    response = cargo_publish(base, metadata, crate_bytes, headers=admin_auth_headers)
    assert response.status_code == 200, response.text

    # The index entry should match crates.io (i.e. the Cargo.toml), not the tampered JSON
    pulp_entry = get_index_entry(base, "se/rd/serde", "1.0.210")
    assert_index_entry_matches_upstream(pulp_entry, upstream_index_entry)


def test_cargo_publish_octet_stream_content_type(
    delete_orphans_pre,
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    admin_auth_headers,
):
    """Publish should accept Content-Type: application/octet-stream.

    Current Cargo omits Content-Type entirely; a proposed upstream fix
    will send application/octet-stream instead.  The server must accept both.
    """
    crate_name = "serde"
    crate_version = "1.0.210"

    crate_path, _ = download_crate_from_upstream(crate_name, crate_version)
    with open(crate_path, "rb") as f:
        crate_bytes = f.read()

    metadata = build_publish_metadata(crate_path, crate_name, crate_version)

    repository = rust_repo_factory()
    distribution = rust_distribution_factory(repository=repository.pulp_href, allow_uploads=True)
    base = cargo_registry_url(distribution.base_path)

    response = cargo_publish(
        base,
        metadata,
        crate_bytes,
        content_type="application/octet-stream",
        headers=admin_auth_headers,
    )
    assert response.status_code == 200, response.text


def test_cargo_publish_uploads_disabled(
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    admin_auth_headers,
):
    """Publishing to a distribution with allow_uploads=False should be rejected."""
    repository = rust_repo_factory()
    distribution = rust_distribution_factory(repository=repository.pulp_href, allow_uploads=False)
    base = cargo_registry_url(distribution.base_path)

    metadata = {"name": "foo", "vers": "0.1.0", "deps": [], "features": {}}
    response = cargo_publish(base, metadata, b"fake-crate-data", headers=admin_auth_headers)
    assert response.status_code == 403
    errors = response.json()["errors"]
    assert any("does not allow uploads" in e["detail"] for e in errors)


def test_cargo_publish_invalid_name_rejected(
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    admin_auth_headers,
):
    """Publishing with an invalid crate name should be rejected."""
    repository = rust_repo_factory()
    distribution = rust_distribution_factory(repository=repository.pulp_href, allow_uploads=True)
    base = cargo_registry_url(distribution.base_path)

    # Starts with a digit
    metadata = {"name": "123invalid", "vers": "0.1.0", "deps": [], "features": {}}
    response = cargo_publish(base, metadata, b"fake", headers=admin_auth_headers)
    assert response.status_code == 400
    assert "crate name" in response.json()["errors"][0]["detail"]


def test_cargo_publish_name_too_long_rejected(
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    admin_auth_headers,
):
    """Publishing with a crate name exceeding 64 characters should be rejected."""
    repository = rust_repo_factory()
    distribution = rust_distribution_factory(repository=repository.pulp_href, allow_uploads=True)
    base = cargo_registry_url(distribution.base_path)

    metadata = {"name": "a" * 65, "vers": "0.1.0", "deps": [], "features": {}}
    response = cargo_publish(base, metadata, b"fake", headers=admin_auth_headers)
    assert response.status_code == 400
    assert "maximum length" in response.json()["errors"][0]["detail"]


def test_cargo_publish_invalid_version_rejected(
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    admin_auth_headers,
):
    """Publishing with an invalid version should be rejected."""
    repository = rust_repo_factory()
    distribution = rust_distribution_factory(repository=repository.pulp_href, allow_uploads=True)
    base = cargo_registry_url(distribution.base_path)

    metadata = {"name": "validname", "vers": "not-a-version", "deps": [], "features": {}}
    response = cargo_publish(base, metadata, b"fake", headers=admin_auth_headers)
    assert response.status_code == 400
    assert "semver" in response.json()["errors"][0]["detail"]


def test_cargo_publish_canonical_name_conflict_rejected(
    delete_orphans_pre,
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    admin_auth_headers,
):
    """Publishing with a canonically-equivalent name (case or hyphen/underscore) should fail."""
    crate_name = "serde"
    crate_version = "1.0.210"

    crate_path, _ = download_crate_from_upstream(crate_name, crate_version)
    with open(crate_path, "rb") as f:
        crate_bytes = f.read()

    metadata = build_publish_metadata(crate_path, crate_name, crate_version)

    repository = rust_repo_factory()
    distribution = rust_distribution_factory(repository=repository.pulp_href, allow_uploads=True)
    base = cargo_registry_url(distribution.base_path)

    # First publish should succeed
    response = cargo_publish(base, metadata, crate_bytes, headers=admin_auth_headers)
    assert response.status_code == 200, response.text

    # Uppercase variant should be rejected as a duplicate
    metadata_upper = dict(metadata, name="Serde")
    response = cargo_publish(base, metadata_upper, b"fake", headers=admin_auth_headers)
    assert response.status_code == 400
    assert "already uploaded" in response.json()["errors"][0]["detail"]


def test_cargo_publish_cross_repo_reuses_content(
    delete_orphans_pre,
    rust_repo_factory,
    rust_repo_api_client,
    rust_distribution_factory,
    rust_content_api_client,
    cargo_registry_url,
    admin_auth_headers,
):
    """Publishing the same crate to two repos should reuse the global content object."""
    crate_name = "serde"
    crate_version = "1.0.210"

    crate_path, _ = download_crate_from_upstream(crate_name, crate_version)
    with open(crate_path, "rb") as f:
        crate_bytes = f.read()

    metadata = build_publish_metadata(crate_path, crate_name, crate_version)

    # Publish to first repository
    repo_a = rust_repo_factory()
    distro_a = rust_distribution_factory(repository=repo_a.pulp_href, allow_uploads=True)
    base_a = cargo_registry_url(distro_a.base_path)

    response = cargo_publish(base_a, metadata, crate_bytes, headers=admin_auth_headers)
    assert response.status_code == 200, response.text

    # Publish to second repository — should succeed
    repo_b = rust_repo_factory()
    distro_b = rust_distribution_factory(repository=repo_b.pulp_href, allow_uploads=True)
    base_b = cargo_registry_url(distro_b.base_path)

    response = cargo_publish(base_b, metadata, crate_bytes, headers=admin_auth_headers)
    assert response.status_code == 200, response.text

    # Verify the same content object is present in both repos (same pulp_href)
    repo_a = rust_repo_api_client.read(repo_a.pulp_href)
    repo_b = rust_repo_api_client.read(repo_b.pulp_href)

    content_in_a = rust_content_api_client.list(
        repository_version=repo_a.latest_version_href, name="serde", vers="1.0.210"
    ).results
    content_in_b = rust_content_api_client.list(
        repository_version=repo_b.latest_version_href, name="serde", vers="1.0.210"
    ).results

    assert len(content_in_a) == 1
    assert len(content_in_b) == 1
    assert content_in_a[0].pulp_href == content_in_b[0].pulp_href


def test_cargo_publish_build_metadata_collision_rejected(
    delete_orphans_pre,
    rust_repo_factory,
    rust_distribution_factory,
    cargo_registry_url,
    admin_auth_headers,
):
    """Publishing versions that differ only in build metadata should be rejected.

    Per SemVer 2.0.0, 1.0.210 and 1.0.210+build1 have equal precedence and
    must be treated as the same version by the registry.
    """
    crate_name = "serde"
    crate_version = "1.0.210"

    crate_path, _ = download_crate_from_upstream(crate_name, crate_version)
    with open(crate_path, "rb") as f:
        crate_bytes = f.read()

    metadata = build_publish_metadata(crate_path, crate_name, crate_version)

    repository = rust_repo_factory()
    distribution = rust_distribution_factory(repository=repository.pulp_href, allow_uploads=True)
    base = cargo_registry_url(distribution.base_path)

    # First publish should succeed
    response = cargo_publish(base, metadata, crate_bytes, headers=admin_auth_headers)
    assert response.status_code == 200, response.text

    # Same version with build metadata appended should be rejected
    metadata_with_build = dict(metadata, vers="1.0.210+build1")
    response = cargo_publish(base, metadata_with_build, b"fake", headers=admin_auth_headers)
    assert response.status_code == 400
    assert "already uploaded" in response.json()["errors"][0]["detail"]


def test_cargo_publish_cross_repo_reuses_pull_through_content(
    delete_orphans_pre,
    rust_remote_factory,
    rust_repo_factory,
    rust_repo_api_client,
    rust_distribution_factory,
    rust_content_api_client,
    cargo_registry_url,
    admin_auth_headers,
):
    """Publishing a crate that was already cached via pull-through should reuse
    the same global RustPackage object.

    Content in Pulp is shared within a domain. When a crate is first cached
    via pull-through and then published to a private registry, the publish
    task should find the existing content rather than creating a duplicate.
    """
    crate_name = "serde"
    crate_version = "1.0.210"

    # --- Pull-through: cache the crate from crates.io ---
    remote = rust_remote_factory(url=CRATES_IO_URL, policy="on_demand")
    pt_repo = rust_repo_factory(remote=remote.pulp_href)
    pt_distro = rust_distribution_factory(remote=remote.pulp_href, repository=pt_repo.pulp_href)
    pt_base = cargo_registry_url(pt_distro.base_path)

    download_file(urljoin(pt_base, f"api/v1/crates/{crate_name}/{crate_version}/download"))

    # Verify content was cached
    pt_repo = rust_repo_api_client.read(pt_repo.pulp_href)
    pt_content = rust_content_api_client.list(
        repository_version=pt_repo.latest_version_href,
        name=crate_name,
        vers=crate_version,
    )
    assert pt_content.count == 1, "Content was not cached by pull-through"

    # --- Publish: push the same crate to a private registry ---
    crate_path, _ = download_crate_from_upstream(crate_name, crate_version)
    with open(crate_path, "rb") as f:
        crate_bytes = f.read()

    metadata = build_publish_metadata(crate_path, crate_name, crate_version)

    pub_repo = rust_repo_factory()
    pub_distro = rust_distribution_factory(repository=pub_repo.pulp_href, allow_uploads=True)
    pub_base = cargo_registry_url(pub_distro.base_path)

    response = cargo_publish(pub_base, metadata, crate_bytes, headers=admin_auth_headers)
    assert response.status_code == 200, response.text

    # --- Verify: same content object in both repos ---
    pub_repo = rust_repo_api_client.read(pub_repo.pulp_href)
    pub_content = rust_content_api_client.list(
        repository_version=pub_repo.latest_version_href,
        name=crate_name,
        vers=crate_version,
    )
    assert pub_content.count == 1, "Content not found in private registry"

    assert pt_content.results[0].pulp_href == pub_content.results[0].pulp_href, (
        "Pull-through and publish created separate content objects — expected reuse"
    )
