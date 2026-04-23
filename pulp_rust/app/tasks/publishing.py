import hashlib
import struct

from django.db import IntegrityError

from pulpcore.plugin.models import Artifact, ContentArtifact
from pulpcore.plugin.tasking import aadd_and_remove

from pulp_rust.app.models import RustDependency, RustPackage, RustRepository
from pulp_rust.app.utils import (
    canonicalize_crate_name,
    extract_cargo_toml,
    extract_dependencies,
    strip_semver_build_metadata,
)


def parse_cargo_publish_body(body):
    """
    Parse the binary request body from ``cargo publish``.

    Format (per https://doc.rust-lang.org/cargo/reference/registry-web-api.html#publish):
        4 bytes: JSON metadata length (little-endian u32)
        N bytes: JSON metadata (UTF-8)
        4 bytes: .crate file length (little-endian u32)
        M bytes: .crate file (binary)

    Returns:
        (metadata_dict, crate_bytes)
    """
    import json

    offset = 0

    json_len = struct.unpack_from("<I", body, offset)[0]
    offset += 4

    json_bytes = body[offset : offset + json_len]
    offset += json_len
    metadata = json.loads(json_bytes)

    crate_len = struct.unpack_from("<I", body, offset)[0]
    offset += 4

    crate_bytes = body[offset : offset + crate_len]
    offset += crate_len

    return metadata, crate_bytes


async def apublish_package(repository_pk, metadata, crate_path):
    """
    Publish a crate to a repository.

    Creates the Artifact, RustPackage, ContentArtifact, and RustDependency records,
    then adds the content to a new repository version.

    Args:
        repository_pk: Primary key of the target repository.
        metadata: Parsed JSON metadata from the cargo publish request.
        crate_path: Filesystem path to the .crate tarball.
    """
    repository = await RustRepository.objects.aget(pk=repository_pk)

    # Create the artifact from the .crate file, or reuse an existing one
    # with the same checksum (Artifact has a unique constraint on digests).
    with open(crate_path, "rb") as f:
        cksum = hashlib.sha256(f.read()).hexdigest()

    artifact = Artifact.init_and_validate(crate_path, expected_digests={"sha256": cksum})
    try:
        await artifact.asave()
    except IntegrityError:
        artifact = await Artifact.objects.aget(sha256=cksum)

    # Extract authoritative metadata from the Cargo.toml inside the .crate tarball.
    # The publish JSON metadata is NOT authoritative - a rogue client can send metadata
    # that doesn't match the actual package. We only use the JSON name/vers to locate the
    # Cargo.toml within the tarball, then extract everything from the Cargo.toml itself.
    # See: https://github.com/rust-lang/cargo/issues/14492
    #      https://github.com/rust-lang/crates.io/pull/7238
    with artifact.file.open("rb") as f:
        cargo_toml = extract_cargo_toml(f, metadata["name"], metadata["vers"])
    package = cargo_toml.get("package", {})

    name = package["name"]
    canonical_name = canonicalize_crate_name(name)
    # Strip build metadata - SemVer 2.0.0 treats versions differing only in
    # build metadata as identical, and the index must not contain duplicates.
    vers = strip_semver_build_metadata(package["version"])

    # Build dependency list from the Cargo.toml (authoritative source)
    deps = extract_dependencies(cargo_toml)

    # Reuse existing content if it already exists in the domain with the same
    # checksum (e.g. from a pull-through cache or another repository's publish).
    # Content in Pulp is globally shared - the same object can belong to
    # multiple repositories.  Including cksum in the lookup allows different
    # crates with the same name+version (e.g. a private crate shadowing a
    # public one) to coexist as separate content objects within a domain.
    content = await RustPackage.objects.filter(
        name=name,
        vers=vers,
        cksum=cksum,
        _pulp_domain_id=repository.pulp_domain_id,
    ).afirst()

    if content is None:
        content = RustPackage(
            name=name,
            canonical_name=canonical_name,
            vers=vers,
            cksum=cksum,
            features=cargo_toml.get("features", {}),
            features2=None,
            links=package.get("links"),
            rust_version=package.get("rust-version"),
            _pulp_domain_id=repository.pulp_domain_id,
        )
        await content.asave()

        if deps:
            await RustDependency.objects.abulk_create(
                [RustDependency(content=content, **dep) for dep in deps]
            )

    # Create the content artifact if it doesn't already exist
    relative_path = f"{name}/{name}-{vers}.crate"
    await ContentArtifact.objects.aget_or_create(
        content=content,
        relative_path=relative_path,
        defaults={"artifact": artifact},
    )

    # Add the content to a new repository version
    await aadd_and_remove(
        repository_pk=repository.pk,
        add_content_units=[content.pk],
        remove_content_units=[],
    )
