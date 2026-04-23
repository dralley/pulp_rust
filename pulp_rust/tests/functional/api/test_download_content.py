"""Tests that verify download of content served by Pulp.

NOTE: This test overlaps with tests in test_pull_through_caching.py which cover
the same scenarios (and more) across both on_demand and streamed policies.
"""

import hashlib
from urllib.parse import urljoin

from pulp_rust.tests.functional.utils import CRATES_IO_URL, download_file


def test_download_content(
    rust_distribution_factory,
    rust_remote_factory,
    rust_repo_factory,
    rust_content_api_client,
    rust_distro_api_client,
    rust_repo_api_client,
    monitor_task,
    cargo_registry_url,
):
    """Verify pull-through download of content served by Pulp.

    1. Create a Remote, Repository, and Distribution with the remote attached.
    2. Download a .crate file through Pulp (pull-through from upstream).
    3. Verify that the content was automatically added to the repository.
    4. Remove the remote and verify the content is still served from cache.
    """
    remote = rust_remote_factory(url=CRATES_IO_URL)
    repository = rust_repo_factory(remote=remote.pulp_href)
    distribution = rust_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    # Download a .crate file through Pulp (triggers pull-through)
    unit_path = "api/v1/crates/itoa/1.0.0/download"
    pulp_unit_url = urljoin(cargo_registry_url(distribution.base_path), unit_path)
    downloaded_file = download_file(pulp_unit_url)
    pulp_unit_checksum = hashlib.sha256(downloaded_file.body).hexdigest()
    assert downloaded_file.response_obj.status == 200

    # Add cached content to the repository
    monitor_task(
        rust_repo_api_client.add_cached_content(
            repository.pulp_href, {"remote": remote.pulp_href}
        ).task
    )

    repository = rust_repo_api_client.read(repository.pulp_href)
    assert not repository.latest_version_href.endswith("/versions/0/")

    # Check that Pulp created a RustPackage record
    content_response = rust_content_api_client.list(name="itoa", vers="1.0.0")
    assert content_response.count == 1

    # Remove the remote from the distribution
    monitor_task(
        rust_distro_api_client.partial_update(distribution.pulp_href, {"remote": None}).task
    )

    # The content should still be available since it's in the repository now
    downloaded_file = download_file(pulp_unit_url)
    assert downloaded_file.response_obj.status == 200

    # Verify the checksum is consistent
    assert hashlib.sha256(downloaded_file.body).hexdigest() == pulp_unit_checksum
