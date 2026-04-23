from pulpcore.plugin.tasking import aadd_and_remove

from pulp_rust.app.models import RustPackage, RustPackageYank, RustRepository
from pulp_rust.app.utils import canonicalize_crate_name


async def ayank_package(repository_pk, name, vers):
    """
    Yank a package version in a repository by adding a RustPackageYank marker.

    Creates a new repository version with the yank marker added.
    """
    name = canonicalize_crate_name(name)
    repository = await RustRepository.objects.aget(pk=repository_pk)
    latest = await repository.alatest_version()

    # Verify the package version exists in this repository
    exists = await RustPackage.objects.filter(
        pk__in=latest.content, canonical_name=name, vers=vers
    ).aexists()
    if not exists:
        raise ValueError(f"Package {name}=={vers} not found in repository")

    # Check if already yanked
    already_yanked = await RustPackageYank.objects.filter(
        pk__in=latest.content, name=name, vers=vers
    ).aexists()
    if already_yanked:
        return  # Already yanked, no-op

    yank_marker, _ = await RustPackageYank.objects.aget_or_create(
        name=name,
        vers=vers,
        _pulp_domain_id=repository.pulp_domain_id,
    )

    await aadd_and_remove(
        repository_pk=repository.pk,
        add_content_units=[yank_marker.pk],
        remove_content_units=[],
    )


async def aunyank_package(repository_pk, name, vers):
    """
    Unyank a package version by removing its RustPackageYank marker.

    Creates a new repository version with the yank marker removed.
    """
    name = canonicalize_crate_name(name)
    repository = await RustRepository.objects.aget(pk=repository_pk)
    latest = await repository.alatest_version()

    yank_marker = await RustPackageYank.objects.filter(
        pk__in=latest.content, name=name, vers=vers
    ).afirst()

    if yank_marker is None:
        return  # Not yanked, no-op

    await aadd_and_remove(
        repository_pk=repository.pk,
        add_content_units=[],
        remove_content_units=[yank_marker.pk],
    )
