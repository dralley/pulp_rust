import json
import logging
import os
import struct
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urljoin

from django.core.exceptions import ObjectDoesNotExist
from django.http.response import (
    Http404,
    HttpResponse,
    HttpResponseNotFound,
)
from django.shortcuts import get_object_or_404, redirect
from drf_spectacular.utils import extend_schema
from dynaconf import settings
from rest_framework.exceptions import Throttled
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from pulpcore.plugin.tasking import dispatch
from pulpcore.plugin.util import get_domain

from pulp_rust.app.auth import CargoTokenAuthentication
from pulp_rust.app.models import (
    RustDistribution,
    RustPackage,
    RustPackageYank,
    _strip_sparse_prefix,
)
from pulp_rust.app.serializers import (
    IndexRootSerializer,
    RustPackageSerializer,
)
from pulp_rust.app.tasks import (
    apublish_package,
    aunyank_package,
    ayank_package,
    parse_cargo_publish_body,
)
from pulp_rust.app.utils import (
    canonicalize_crate_name,
    strip_semver_build_metadata,
    validate_crate_name,
    validate_crate_version,
)

log = logging.getLogger(__name__)

BASE_CONTENT_URL = urljoin(settings.CONTENT_ORIGIN, settings.CONTENT_PATH_PREFIX)


def cargo_error(detail, status=400):
    """Build a Cargo-style JSON error response."""
    return HttpResponse(
        json.dumps({"errors": [{"detail": detail}]}),
        content_type="application/json",
        status=status,
    )


def repository_write_error(distro):
    """Return a Cargo error response if the distribution can't be written to, else None.

    Write operations (publish, yank, unyank) require the distribution to point at a
    repository. A distribution serving a fixed repository_version is read-only, and one
    with neither has nothing to write to.
    """
    if distro.repository:
        return None
    if distro.repository_version:
        return cargo_error(
            "This distribution serves a fixed repository version and cannot be modified.",
            status=400,
        )
    return cargo_error("No repository associated with this distribution", status=404)


class PlainTextRenderer(BaseRenderer):
    """Renderer for text/plain responses (Cargo sends Accept: text/plain)."""

    media_type = "text/plain"
    format = "txt"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class ApiMixin:
    """Mixin to get index specific info."""

    renderer_classes = [PlainTextRenderer]
    _distro = None

    @property
    def distribution(self):
        if not self._distro:
            self._distro = self.get_distribution(self.kwargs["repo"])
        return self._distro

    @staticmethod
    def get_distribution(repo):
        """Finds the distribution associated with this base_path."""
        distro_qs = RustDistribution.objects.select_related(
            "repository", "repository_version", "remote"
        )
        try:
            return distro_qs.get(base_path=repo, pulp_domain=get_domain())
        except ObjectDoesNotExist:
            raise Http404(f"No RustDistribution found for base_path {repo}")

    @staticmethod
    def get_repository_version(distribution):
        """Finds the repository version this distribution is serving."""
        rep = distribution.repository
        rep_version = distribution.repository_version
        if rep:
            return rep.latest_version()
        elif rep_version:
            return rep_version
        else:
            raise Http404("No repository associated with this index")

    @staticmethod
    def get_content(repository_version):
        """Returns queryset of the content in this repository version."""
        return RustPackage.objects.filter(pk__in=repository_version.content)

    def get_rvc(self):
        """Takes the base_path and returns the repository_version and content."""
        if self.distribution.remote:
            if not self.distribution.repository:
                return None, None
        repo_ver = self.get_repository_version(self.distribution)
        content = self.get_content(repo_ver)
        return repo_ver, content

    def initial(self, request, *args, **kwargs):
        """Perform common initialization tasks for API endpoints."""
        super().initial(request, *args, **kwargs)
        domain_name = get_domain().name
        repo = self.kwargs["repo"]
        if settings.DOMAIN_ENABLED:
            cargo_base = request.build_absolute_uri(f"/pulp/cargo/{domain_name}/{repo}/")
            self.base_content_url = urljoin(BASE_CONTENT_URL, f"pulp/cargo/{domain_name}/{repo}/")
        else:
            cargo_base = request.build_absolute_uri(f"/pulp/cargo/{repo}/")
            self.base_content_url = urljoin(BASE_CONTENT_URL, f"pulp/cargo/{repo}/")
        self.base_api_url = cargo_base.rstrip("/")
        self.base_download_url = f"{cargo_base}api/v1/crates"

    @classmethod
    def urlpattern(cls):
        """Mocking NamedModelViewSet behavior to get Cargo APIs to support RBAC access polices."""
        return f"pulp/cargo/{cls.endpoint_name}"


class CargoIndexApiViewSet(ApiMixin, ViewSet):
    """View for the Cargo JSON metadata endpoint."""

    endpoint_name = "api"
    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {
                "action": ["retrieve"],
                "principal": "*",
                "effect": "allow",
            },
        ],
    }

    @extend_schema(
        tags=["Cargo: Metadata"],
        responses={200: RustPackageSerializer},
        summary="Get package metadata",
    )
    def retrieve(self, request, path, **kwargs):
        """
        Retrieve crate metadata for the sparse protocol.

        The sparse protocol uses a directory structure based on crate name length:
        - 1 char: 1/{crate}
        - 2 chars: 2/{crate}
        - 3 chars: 3/{first-char}/{crate}
        - 4+ chars: {first-two}/{second-two}/{crate}

        Returns newline-delimited JSON, one version per line.

        If the crate is not found locally and the distribution has a remote,
        the metadata is proxied from the upstream sparse index.
        """
        repo_ver, content = self.get_rvc()

        # Extract crate name from the path (last component)
        crate_name = path.rsplit("/", 1)[-1]
        canonical = canonicalize_crate_name(crate_name)

        # For pull-through caches (distributions with a remote), proxy the
        # index from upstream so that newly published upstream versions are
        # discovered.  If the upstream is unreachable, fall through to serve
        # from locally cached content.
        if self.distribution.remote:
            remote = self.distribution.remote.cast()
            index_url = _strip_sparse_prefix(remote.url).rstrip("/")
            upstream_url = f"{index_url}/{path}"
            try:
                response = urllib.request.urlopen(upstream_url, timeout=30)
                return HttpResponse(response.read(), content_type="text/plain")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return HttpResponseNotFound(f"Crate '{crate_name}' not found")
                log.warning(
                    "Upstream index request failed (HTTP %d) for %s, "
                    "falling back to cached content",
                    e.code,
                    upstream_url,
                )
            except (urllib.error.URLError, TimeoutError) as e:
                log.warning(
                    "Upstream index request failed for %s: %s, falling back to cached content",
                    upstream_url,
                    e,
                )

        # Serve from local index. For private registries this is the only source; for pull-through
        # caches this is the fallback when upstream is unavailable.
        # Use canonical_name for the lookup so that requests for any name form
        # (e.g. cfg-if, cfg_if, Cfg-If) find the right content.
        if content is not None:
            crate_versions = content.filter(canonical_name=canonical).order_by("vers")
            if crate_versions.exists():
                yanked_versions = set(
                    RustPackageYank.objects.filter(
                        pk__in=repo_ver.content, name=canonical
                    ).values_list("vers", flat=True)
                )
                return self._build_index_response(crate_versions, yanked_versions)

        return HttpResponseNotFound(f"Crate '{crate_name}' not found")

    @staticmethod
    def _build_index_response(crate_versions, yanked_versions=frozenset()):
        """Build a newline-delimited JSON response from local crate versions."""
        lines = []
        for crate_version in crate_versions:
            deps = []
            for dep in crate_version.dependencies.all():
                dep_obj = {
                    "name": dep.name,
                    "req": dep.req,
                    "features": dep.features,
                    "optional": dep.optional,
                    "default_features": dep.default_features,
                    "target": dep.target,
                    "kind": dep.kind,
                }
                # crates.io omits these keys when not set
                if dep.registry is not None:
                    dep_obj["registry"] = dep.registry
                if dep.package is not None:
                    dep_obj["package"] = dep.package
                deps.append(dep_obj)

            version_obj = {
                "name": crate_version.name,
                "vers": crate_version.vers,
                "deps": deps,
                "cksum": crate_version.cksum,
                "features": crate_version.features,
                "yanked": crate_version.vers in yanked_versions,
                "links": crate_version.links,
                "v": crate_version.v,
            }

            if crate_version.features2:
                version_obj["features2"] = crate_version.features2
            if crate_version.rust_version:
                version_obj["rust_version"] = crate_version.rust_version

            lines.append(json.dumps(version_obj))

        return HttpResponse("\n".join(lines), content_type="text/plain")


class IndexRoot(ApiMixin, ViewSet):
    """View for base_url of distribution."""

    endpoint_name = "root"
    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {
                "action": ["retrieve"],
                "principal": "*",
                "effect": "allow",
            },
        ],
    }

    @extend_schema(responses={200: IndexRootSerializer}, summary="Get index info")
    def retrieve(self, request, repo):
        """Gets index route."""
        data = {
            "dl": self.base_download_url,
            "api": self.base_api_url,
            "auth-required": False,
        }
        return HttpResponse(json.dumps(data), content_type="application/json")


class CargoMeApiView(APIView):
    """
    Auth verification endpoint for ``cargo login``.

    Cargo calls GET /me after login to verify the token is valid.
    See: https://doc.rust-lang.org/cargo/reference/registry-web-api.html
    """

    authentication_classes = [CargoTokenAuthentication]
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request, **kwargs):
        return HttpResponse(json.dumps({"ok": True}), content_type="application/json")


class CargoPublishApiView(APIView):
    """
    View for Cargo's crate publish endpoint (PUT /api/v1/crates/new).

    Parses the custom binary format from ``cargo publish`` and dispatches a task
    to create the artifact, content, and new repository version.

    See: https://doc.rust-lang.org/cargo/reference/registry-web-api.html#publish
    """

    authentication_classes = [CargoTokenAuthentication]
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get_distribution(self):
        return get_object_or_404(
            RustDistribution, base_path=self.kwargs["repo"], pulp_domain=get_domain()
        )

    def put(self, request, **kwargs):
        """
        Handle ``cargo publish`` requests.

        Parses the binary body (JSON metadata + .crate tarball), validates the
        distribution allows uploads and the crate doesn't already exist in the
        repository, then dispatches a publish task.
        """
        distro = self.get_distribution()

        if not request.user.has_perm("rust.publish_rustdistribution", distro):
            return cargo_error("insufficient permissions", status=403)

        if not distro.allow_uploads:
            return cargo_error("this registry does not allow uploads", status=403)

        if error := repository_write_error(distro):
            return error

        try:
            metadata, crate_bytes = parse_cargo_publish_body(request.body)
        except (struct.error, json.JSONDecodeError, UnicodeDecodeError):
            return cargo_error("invalid publish request body")

        name = metadata.get("name")
        vers = metadata.get("vers")
        if not name or not vers:
            return cargo_error("missing required fields: name, vers")

        error = validate_crate_name(name)
        if error:
            return cargo_error(error)

        error = validate_crate_version(vers)
        if error:
            return cargo_error(error)

        # Check for duplicates using canonical name form to prevent confusable
        # packages (e.g. "my-crate" vs "my_crate" or "MyCrate" vs "mycrate").
        # Strip build metadata because SemVer 2.0.0 treats versions differing only
        # in build metadata as identical (e.g. "1.0.0" and "1.0.0+build1" collide).
        canonical = canonicalize_crate_name(name)
        vers_base = strip_semver_build_metadata(vers)
        repo_version = distro.repository.latest_version()
        if RustPackage.objects.filter(
            pk__in=repo_version.content, canonical_name=canonical, vers=vers_base
        ).exists():
            return cargo_error(f"crate version `{name}@{vers}` is already uploaded")

        # Write the .crate bytes to a temp file — raw bytes can't be passed
        # through dispatch() because task kwargs are stored as JSON.
        tmp = tempfile.NamedTemporaryFile(suffix=".crate", delete=False)
        tmp.write(crate_bytes)
        tmp.close()

        try:
            task = dispatch(
                apublish_package,
                exclusive_resources=[distro.repository],
                immediate=True,
                kwargs={
                    "repository_pk": str(distro.repository.pk),
                    "metadata": metadata,
                    "crate_path": tmp.name,
                },
            )
            has_task_completed(task)
        finally:
            os.unlink(tmp.name)

        return HttpResponse(
            json.dumps(
                {
                    "warnings": {
                        "invalid_categories": [],
                        "invalid_badges": [],
                        "other": [],
                    }
                }
            ),
            content_type="application/json",
        )


class CargoDownloadApiView(APIView):
    """
    View for Cargo's crate download, readme, yank, and unyank endpoints.
    """

    authentication_classes = [CargoTokenAuthentication]
    renderer_classes = [PlainTextRenderer, JSONRenderer]

    def get_permissions(self):
        if self.request.method in ("DELETE", "PUT"):
            return [IsAuthenticated()]
        return []

    def get_full_path(self, base_path, pulp_domain=None):
        if settings.DOMAIN_ENABLED:
            domain = pulp_domain or get_domain()
            return f"{domain.name}/{base_path}"
        return base_path

    def redirect_to_content_app(self, distribution, relative_path, request):
        full_path = self.get_full_path(distribution.base_path)
        content_path = f"{settings.CONTENT_PATH_PREFIX}{full_path}/{relative_path}"
        return redirect(request.build_absolute_uri(content_path))

    def get_distribution(self):
        return get_object_or_404(
            RustDistribution, base_path=self.kwargs["repo"], pulp_domain=get_domain()
        )

    def get(self, request, name, version, rest, **kwargs):
        """
        Responds to GET requests for crate downloads and readmes.

        Handles:
        - api/v1/crates/{name}/{version}/download - redirect to .crate file
        - api/v1/crates/{name}/{version}/readme - not yet implemented
        """
        distro = self.get_distribution()

        if rest == "download":
            relative_path = f"{name}/{name}-{version}.crate"
            return self.redirect_to_content_app(distro, relative_path, request)
        elif rest == "readme":
            raise Http404("Readme endpoint is not yet implemented")
        else:
            raise Http404(f"Unknown action: {rest}")

    def delete(self, request, name, version, rest, **kwargs):
        """
        Responds to DELETE requests for yanking crate versions.

        Handles:
        - api/v1/crates/{name}/{version}/yank
        """
        if rest != "yank":
            raise Http404(f"Unknown action: {rest}")

        distro = self.get_distribution()
        if not request.user.has_perm("rust.yank_rustdistribution", distro):
            return cargo_error("insufficient permissions", status=403)
        if error := repository_write_error(distro):
            return error

        canonical = canonicalize_crate_name(name)
        repo_version = distro.repository.latest_version()
        if not RustPackage.objects.filter(
            pk__in=repo_version.content, canonical_name=canonical, vers=version
        ).exists():
            return HttpResponse(
                json.dumps(
                    {"errors": [{"detail": f"crate `{name}` does not have a version `{version}`"}]}
                ),
                content_type="application/json",
                status=404,
            )

        task = dispatch(
            ayank_package,
            exclusive_resources=[distro.repository],
            immediate=True,
            kwargs={
                "repository_pk": str(distro.repository.pk),
                "name": canonical,
                "vers": version,
            },
        )
        has_task_completed(task)
        return HttpResponse(json.dumps({"ok": True}), content_type="application/json")

    def put(self, request, name, version, rest, **kwargs):
        """
        Responds to PUT requests for unyanking crate versions.

        Handles:
        - api/v1/crates/{name}/{version}/unyank
        """
        if rest != "unyank":
            raise Http404(f"Unknown action: {rest}")

        distro = self.get_distribution()
        if not request.user.has_perm("rust.yank_rustdistribution", distro):
            return cargo_error("insufficient permissions", status=403)
        if error := repository_write_error(distro):
            return error

        task = dispatch(
            aunyank_package,
            exclusive_resources=[distro.repository],
            immediate=True,
            kwargs={
                "repository_pk": str(distro.repository.pk),
                "name": canonicalize_crate_name(name),
                "vers": version,
            },
        )
        has_task_completed(task)
        return HttpResponse(json.dumps({"ok": True}), content_type="application/json")


def has_task_completed(task):
    """
    Verify whether an immediate task ran properly.

    Returns:
        bool: True if the task ended successfully.

    Raises:
        Exception: If an error occured during the task's runtime.
        Throttled: If the task did not run due to resource constraints.

    """
    if task.state == "completed":
        task.delete()
        return True
    elif task.state == "canceled":
        raise Throttled()
    else:
        error = task.error
        task.delete()
        raise Exception(str(error))
