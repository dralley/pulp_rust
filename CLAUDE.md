# CLAUDE.md

The role of this file is to describe common mistakes and confusion points that agents might encounter as they work in this project.
If you ever encounter something in the project that surprises you, please alert the developer working with you and indicate that this is the case in the CLAUDE.md file to help prevent future agents from having the same issue.

## Interacting with the developer environment

Use the `pulp-cli` to interact with the Pulp API. Fallback on `httpie/curl` when the CLI doesn't support the endpoint/options needed. See the [user guides](docs/user/guides/) for workflow examples.

Use the `oci-env` cli to interact with the developer's Pulp instance. It has commands for managing state, running tests, and executing commands against a running Pulp.

```bash
oci-env --help
oci-env compose ps  # check status of the Pulp dev container
oci-env compose up/down/restart  # start/stop/restart the Pulp dev container
oci-env poll --attempts 10 --wait 10  # wait till Pulp container finishes booting up
oci-env pstart/pstop/prestart  # start/stop/restart the services inside the Pulp container
oci-env generate-client --help  # create the client bindings needed for the functional tests!
oci-env test --help # run the functional/unit tests
oci-env pulpcore-manager  # run any pulpcore or Django commands
```

## Running/Writing tests

Prefer writing functional tests for new changes/bugfixes and only fallback on unit tests when the change is not easily testable through the API.

pulpcore & pulp_rust functional tests require both client bindings to be installed. The bindings must be regenerated for any changes to the API spec.

**Always** use the `oci-env` to run the functional and unit tests.

## Modifying template_config.yml

Use the `plugin-template` tool after any changes made to `template_config.yml`.

```bash
# typically located in the parent directory of pulpcore/plugin
../plugin_template/plugin-template --github
```

## Fixing failed backports

When patchback fails to cherry-pick a PR into an older branch, you need to manually apply the equivalent change. Key things to know:

- When creating a PR include `[<version>]` in the PR title (e.g. `[0.1] Fix crate upload handling`).
- Use `git cherry-pick -x`.

## Contributing

When preparing to commit and create a PR you **must** follow our [PR checklist](https://pulpproject.org/pulpcore/docs/dev/guides/pull-request-walkthrough/) Important to note is the AI attribution requirement in our commit messages. Also, note that our changelog entries are markdown.

## Project status

This project is in **Tech Preview**. APIs, behaviors, and data models are subject to breaking changes without prior notice. See the README for current limitations.

## Cargo protocol & domain knowledge

For understanding the Cargo registry protocol, refer to the upstream documentation:

- [Cargo Book](https://doc.rust-lang.org/stable/cargo/)
- [Registry Web API](https://doc.rust-lang.org/cargo/reference/registry-web-api.html)
- [Registry Index](https://doc.rust-lang.org/cargo/reference/registry-index.html)

## Common pitfalls

- **Cargo.toml is authoritative during publish**: When a crate is published, dependencies are extracted from the `Cargo.toml` inside the `.crate` tarball, NOT from the JSON metadata submitted alongside it. This is an intentional security measure (see rust-lang/cargo#14492).
- **RustDependency is NOT a Content type**: Unlike `RustPackage` and `RustPackageYank`, `RustDependency` is a regular Django model with an FK to `RustPackage`. Do not treat it as a Pulpcore Content subclass.
- **Django app label is `rust`, not `pulp_rust`**: When running Django management commands (e.g. `makemigrations`), use the app label `rust`. The Python package is `pulp_rust` but the Django app label is set to `rust` in `PulpRustPluginAppConfig`.

