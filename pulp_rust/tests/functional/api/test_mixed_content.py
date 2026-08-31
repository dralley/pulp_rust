"""Tests preventing a repository from mixing pull-through cached and uploaded content.

A repository must be used either for pull-through caching or for uploads, never both.
"""

import pytest

from pulpcore.client.pulp_rust.exceptions import ApiException

from pulp_rust.tests.functional.utils import CRATES_IO_URL


def test_single_distribution_remote_and_uploads_rejected(
    rust_repo_factory,
    rust_remote_factory,
    rust_distribution_factory,
):
    """A single distribution cannot have both a remote and allow_uploads."""
    repo = rust_repo_factory()
    remote = rust_remote_factory(url=CRATES_IO_URL)

    with pytest.raises(ApiException) as exc:
        rust_distribution_factory(
            repository=repo.pulp_href, remote=remote.pulp_href, allow_uploads=True
        )
    assert exc.value.status == 400


def test_upload_distribution_on_pull_through_repo_rejected(
    rust_repo_factory,
    rust_remote_factory,
    rust_distribution_factory,
):
    """A repo already used for pull-through caching cannot get an upload distribution."""
    repo = rust_repo_factory()
    remote = rust_remote_factory(url=CRATES_IO_URL)

    # First distribution caches into the repo via a remote.
    rust_distribution_factory(repository=repo.pulp_href, remote=remote.pulp_href)

    # A second distribution accepting uploads into the same repo must be rejected.
    with pytest.raises(ApiException) as exc:
        rust_distribution_factory(repository=repo.pulp_href, allow_uploads=True)
    assert exc.value.status == 400


def test_pull_through_distribution_on_upload_repo_rejected(
    rust_repo_factory,
    rust_remote_factory,
    rust_distribution_factory,
):
    """A repo already used for uploads cannot get a pull-through distribution."""
    repo = rust_repo_factory()
    remote = rust_remote_factory(url=CRATES_IO_URL)

    # First distribution accepts uploads into the repo.
    rust_distribution_factory(repository=repo.pulp_href, allow_uploads=True)

    # A second distribution caching into the same repo must be rejected.
    with pytest.raises(ApiException) as exc:
        rust_distribution_factory(repository=repo.pulp_href, remote=remote.pulp_href)
    assert exc.value.status == 400


def test_upload_distribution_on_repo_with_remote_rejected(
    rust_repo_factory,
    rust_remote_factory,
    rust_distribution_factory,
):
    """A repo with its own remote (for syncing) cannot get an upload distribution."""
    remote = rust_remote_factory(url=CRATES_IO_URL)
    repo = rust_repo_factory(remote=remote.pulp_href)

    with pytest.raises(ApiException) as exc:
        rust_distribution_factory(repository=repo.pulp_href, allow_uploads=True)
    assert exc.value.status == 400


def test_separate_repos_for_cache_and_uploads_succeed(
    rust_repo_factory,
    rust_remote_factory,
    rust_distribution_factory,
):
    """Using separate repositories for caching and uploads is allowed."""
    remote = rust_remote_factory(url=CRATES_IO_URL)

    cache_repo = rust_repo_factory()
    cache_distro = rust_distribution_factory(
        repository=cache_repo.pulp_href, remote=remote.pulp_href
    )
    assert cache_distro is not None

    upload_repo = rust_repo_factory()
    upload_distro = rust_distribution_factory(repository=upload_repo.pulp_href, allow_uploads=True)
    assert upload_distro is not None


def test_multiple_upload_distributions_on_one_repo_succeed(
    rust_repo_factory,
    rust_distribution_factory,
):
    """Multiple upload distributions may share one repo (no caching involved)."""
    repo = rust_repo_factory()

    first = rust_distribution_factory(repository=repo.pulp_href, allow_uploads=True)
    second = rust_distribution_factory(repository=repo.pulp_href, allow_uploads=True)
    assert first is not None
    assert second is not None
