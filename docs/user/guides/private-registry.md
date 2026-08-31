# Host a Private Cargo Registry

This guide walks you through setting up Pulp as a private Cargo registry for hosting internal
crates. This is useful for organizations that need to distribute proprietary or internal-only
Rust packages.

## Create a Repository

```bash
pulp rust repository create --name my-crates
```

!!! note
    A repository may be used for pull-through caching **or** for uploads, but not both. Pulp
    rejects attempts to attach an upload distribution to a repository that is already used for
    caching (and vice versa). Use separate repositories for caching and publishing.

## Create a Distribution

A distribution makes the repository's content available to Cargo over HTTP. Set `--allow-uploads`
to enable publishing crates via `cargo publish`.

```bash
pulp rust distribution create \
    --name my-crates \
    --base-path my-crates \
    --repository my-crates \
    --allow-uploads
```

Your private registry is now served at `http://<pulp-host>/pulp/cargo/my-crates/`.

## Configure Cargo

Add the private registry to your Cargo configuration. Create or edit `~/.cargo/config.toml`:

```toml
[registries.my-crates]
index = "sparse+http://<pulp-host>/pulp/cargo/my-crates/"
```

## Authentication

State-changing operations (publishing, yanking, and unyanking) require a Cargo API token.
See the [Authentication & Authorization](authentication.md) guide for how to create tokens
and manage access.

Read-only operations (downloading crates, browsing the index) do not require a token.

## Publish a Crate

Once the registry is configured and a distribution with `--allow-uploads` exists, you can publish
crates using standard Cargo tooling:

```bash
cargo publish --registry my-crates
```

This uploads the crate to Pulp, which creates the artifact, content metadata, and a new repository
version. The crate is immediately available for download through the distribution.

Publishing the same crate version twice is rejected — crate versions are immutable, consistent
with crates.io behavior.

## Yank and Unyank

Yanking marks a crate version as unavailable for new dependency resolution, while still allowing
existing projects that already depend on it to continue downloading it. This matches the
[crates.io yank semantics](https://doc.rust-lang.org/cargo/reference/publishing.html#cargo-yank).

```bash
# Yank a version
cargo yank --registry my-crates --version 1.0.0 my-crate

# Unyank a version
cargo yank --registry my-crates --version 1.0.0 --undo my-crate
```

### Using the Private Registry as a Dependency Source

To depend on crates from your private registry, specify the registry in your `Cargo.toml`:

```toml
[dependencies]
my-internal-lib = { version = "1.0", registry = "my-crates" }
```

### Setting the Default Registry

You can set your private registry as the default for `cargo publish` and other registry commands
so you don't need to pass `--registry` every time:

```toml
[registry]
default = "my-crates"
```

This affects commands like `cargo publish`, `cargo yank`, and `cargo owner`. It does **not**
change where dependencies are resolved from — that is controlled by source replacement (below).

!!! tip
    Setting a default registry is recommended for organizations with private crates. Without it,
    running `cargo publish` without `--registry` will publish to crates.io by default, which could
    accidentally leak proprietary code to the public registry.

### Replacing crates.io Entirely

If you want all crate lookups to go through your private registry (for example, in an air-gapped
environment), you can replace the default source:

```toml
[source.crates-io]
replace-with = "my-crates"

[source.my-crates]
registry = "sparse+http://<pulp-host>/pulp/cargo/my-crates/"
```

This redirects all dependency resolution — including transitive dependencies — through your
private registry. Any crate not present in the registry will fail to resolve.

## Combining with Pull-Through Caching

If you need both private crates and public crates.io dependencies, use separate distributions and
separate repositories -- one for publishing and one for pull-through caching. Pulp enforces that
a distribution cannot have both `allow_uploads` and a `remote` set at the same time.

!!! warning
    The two distributions should also use **separate repositories**. Mixing public and private
    content in a single repository creates a risk of
    [dependency confusion](https://medium.com/@alex.birsan/dependency-confusion-4a5d60fec610)
    attacks, where a crate from the public registry conflicts with a private crate of the same
    name and version.

```bash
# Set up a separate pull-through cache for crates.io
pulp rust remote create --name crates-io --url "sparse+https://index.crates.io/" --policy on_demand
pulp rust repository create --name crates-io-cache --remote crates-io --retain-repo-versions 1
pulp rust distribution create \
    --name crates-io-cache \
    --base-path crates-io-cache \
    --repository crates-io-cache \
    --remote crates-io
```

Then configure Cargo to use both registries, with crates.io going through the cache and private
crates resolved from your private registry:

```toml
[registries.my-crates]
index = "sparse+http://<pulp-host>/pulp/cargo/my-crates/"

[source.crates-io]
replace-with = "crates-io-cache"

[source.crates-io-cache]
registry = "sparse+http://<pulp-host>/pulp/cargo/crates-io-cache/"
```

```toml
[dependencies]
serde = "1.0"                                              # resolved from crates-io-cache
my-internal-lib = { version = "1.0", registry = "my-crates" }  # resolved from private registry
```

## Crate Name Handling

Crate names in the Cargo spec are case-insensitive, and hyphens and underscores are treated as
equivalent (e.g. `my-crate` and `my_crate` refer to the same package). Pulp enforces this:
publishing `my-crate` when `my_crate` already exists in the same repository is rejected as a
duplicate. Yank and unyank operations use the same matching.

!!! tip "Separate Registries"
    Pulp enforces that private registries and pull-through caches use separate repositories.
    A repository that is a pull-through cache (targeted by a distribution with a `remote`, or
    with its own `remote` set) cannot also accept uploads, and vice versa. This prevents
    dependency-confusion attacks where uploaded content could override cached upstream content.

## Further Reading

- [Cargo registries configuration](https://doc.rust-lang.org/cargo/reference/registries.html) -- configuring alternate registries in Cargo
- [Cargo source replacement](https://doc.rust-lang.org/cargo/reference/source-replacement.html) -- replacing crates.io with an alternate source
- [Cargo config reference](https://doc.rust-lang.org/cargo/reference/config.html) -- full reference for `.cargo/config.toml`
- [Specifying dependencies](https://doc.rust-lang.org/cargo/reference/specifying-dependencies.html#specifying-dependencies-from-other-registries) -- using dependencies from alternate registries
