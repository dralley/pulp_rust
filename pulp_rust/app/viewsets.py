import hashlib
import secrets

from django_filters import CharFilter
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.mixins import CreateModelMixin, DestroyModelMixin, ListModelMixin
from rest_framework.response import Response

from pulpcore.plugin import viewsets as core
from pulpcore.plugin.actions import ModifyRepositoryActionMixin
from pulpcore.plugin.serializers import (
    AsyncOperationResponseSerializer,
    RepositorySyncURLSerializer,
)
from pulpcore.plugin.tasking import dispatch
from pulpcore.plugin.viewsets import NamedModelViewSet, RemoteFilter

from . import models, serializers, tasks


class RustPackageFilter(core.ContentFilter):
    """
    FilterSet for RustPackage (Cargo packages).

    Provides filtering capabilities for package name, version, and checksum.
    """

    # Filter by exact package name
    name = CharFilter(field_name="name")

    # Filter by exact version string
    vers = CharFilter(field_name="vers")

    # Filter by checksum
    cksum = CharFilter(field_name="cksum")

    # Filter by minimum Rust version requirement
    rust_version = CharFilter(field_name="rust_version")

    class Meta:
        model = models.RustPackage
        fields = [
            "name",
            "vers",
            "cksum",
            "rust_version",
        ]


class RustPackageViewSet(core.ReadOnlyContentViewSet):
    """
    A read-only ViewSet for RustPackage (Cargo package versions).

    Content is created via ``cargo publish`` (the Cargo registry API),
    not through this viewset.

    API endpoint: /pulp/api/v3/content/rust/packages/
    """

    endpoint_name = "packages"
    queryset = models.RustPackage.objects.prefetch_related("dependencies").all()
    serializer_class = serializers.RustPackageSerializer
    filterset_class = RustPackageFilter

    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {
                "action": ["list", "retrieve"],
                "principal": "authenticated",
                "effect": "allow",
            },
        ],
        "queryset_scoping": {"function": "scope_queryset"},
    }


class RustRemoteFilter(RemoteFilter):
    """
    A FilterSet for RustRemote.
    """

    class Meta:
        model = models.RustRemote
        fields = [
            # ...
        ]


class CargoTokenViewSet(NamedModelViewSet, CreateModelMixin, ListModelMixin, DestroyModelMixin):
    """Manage Cargo API tokens for the current user."""

    endpoint_name = "cargo/tokens"
    queryset = models.RustCargoToken.objects.all()
    serializer_class = serializers.CargoTokenSerializer

    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {
                "action": ["create", "list", "retrieve", "destroy"],
                "principal": "authenticated",
                "effect": "allow",
            },
        ],
    }

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = f"crg_{secrets.token_hex(20)}"
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        serializer.save(user=request.user, token_hash=token_hash)
        data = serializer.data
        data["token"] = raw_token
        return Response(data, status=201)


class RustRemoteViewSet(core.RemoteViewSet, core.RolesMixin):
    """
    A ViewSet for RustRemote.
    """

    endpoint_name = "rust"
    queryset = models.RustRemote.objects.all()
    serializer_class = serializers.RustRemoteSerializer
    queryset_filtering_required_permission = "rust.view_rustremote"

    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {
                "action": ["list", "my_permissions"],
                "principal": "authenticated",
                "effect": "allow",
            },
            {
                "action": ["create"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_perms:rust.add_rustremote",
            },
            {
                "action": ["retrieve"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_or_obj_perms:rust.view_rustremote",
            },
            {
                "action": ["update", "partial_update", "set_label", "unset_label"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:rust.change_rustremote",
                    "has_model_or_domain_or_obj_perms:rust.view_rustremote",
                ],
            },
            {
                "action": ["destroy"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:rust.delete_rustremote",
                    "has_model_or_domain_or_obj_perms:rust.view_rustremote",
                ],
            },
            {
                "action": ["list_roles", "add_role", "remove_role"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:rust.manage_roles_rustremote",
                ],
            },
        ],
        "creation_hooks": [
            {
                "function": "add_roles_for_object_creator",
                "parameters": {"roles": "rust.rustremote_owner"},
            },
        ],
        "queryset_scoping": {"function": "scope_queryset"},
    }

    LOCKED_ROLES = {
        "rust.rustremote_creator": ["rust.add_rustremote"],
        "rust.rustremote_owner": [
            "rust.view_rustremote",
            "rust.change_rustremote",
            "rust.delete_rustremote",
            "rust.manage_roles_rustremote",
        ],
        "rust.rustremote_viewer": ["rust.view_rustremote"],
    }


class RustRepositoryViewSet(core.RepositoryViewSet, ModifyRepositoryActionMixin, core.RolesMixin):
    """
    A ViewSet for RustRepository.
    """

    endpoint_name = "rust"
    queryset = models.RustRepository.objects.all()
    serializer_class = serializers.RustRepositorySerializer
    queryset_filtering_required_permission = "rust.view_rustrepository"

    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {
                "action": ["list", "my_permissions"],
                "principal": "authenticated",
                "effect": "allow",
            },
            {
                "action": ["create"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_perms:rust.add_rustrepository",
                    "has_remote_param_model_or_domain_or_obj_perms:rust.view_rustremote",
                ],
            },
            {
                "action": ["retrieve"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_or_obj_perms:rust.view_rustrepository",
            },
            {
                "action": ["destroy"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:rust.delete_rustrepository",
                    "has_model_or_domain_or_obj_perms:rust.view_rustrepository",
                ],
            },
            {
                "action": ["update", "partial_update", "set_label", "unset_label"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:rust.change_rustrepository",
                    "has_model_or_domain_or_obj_perms:rust.view_rustrepository",
                    "has_remote_param_model_or_domain_or_obj_perms:rust.view_rustremote",
                ],
            },
            {
                "action": ["modify", "add_cached_content"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:rust.modify_rustrepository",
                    "has_model_or_domain_or_obj_perms:rust.view_rustrepository",
                ],
            },
            {
                "action": ["list_roles", "add_role", "remove_role"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:rust.manage_roles_rustrepository",
                ],
            },
        ],
        "creation_hooks": [
            {
                "function": "add_roles_for_object_creator",
                "parameters": {"roles": "rust.rustrepository_owner"},
            },
        ],
        "queryset_scoping": {"function": "scope_queryset"},
    }

    LOCKED_ROLES = {
        "rust.rustrepository_creator": ["rust.add_rustrepository"],
        "rust.rustrepository_owner": [
            "rust.view_rustrepository",
            "rust.change_rustrepository",
            "rust.delete_rustrepository",
            "rust.modify_rustrepository",
            "rust.manage_roles_rustrepository",
        ],
        "rust.rustrepository_viewer": ["rust.view_rustrepository"],
    }

    # This decorator is necessary since a sync operation is asyncrounous and returns
    # the id and href of the sync task.
    @extend_schema(
        description="Trigger an asynchronous task to sync content.",
        summary="Sync from remote",
        responses={202: AsyncOperationResponseSerializer},
    )
    @action(detail=True, methods=["post"], serializer_class=RepositorySyncURLSerializer)
    def sync(self, request, pk):
        """
        Dispatches a sync task.
        """
        repository = self.get_object()
        serializer = RepositorySyncURLSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        remote = serializer.validated_data.get("remote")
        mirror = serializer.validated_data.get("mirror")

        result = dispatch(
            tasks.synchronize,
            exclusive_resources=[repository],
            shared_resources=[remote],
            kwargs={
                "remote_pk": str(remote.pk),
                "repository_pk": str(repository.pk),
                "mirror": mirror,
            },
        )
        return core.OperationPostponedResponse(result, request)

    @extend_schema(
        description="Trigger an asynchronous task to add cached content to a repository.",
        summary="Add cached content",
        responses={202: AsyncOperationResponseSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        serializer_class=serializers.RepositoryAddCachedContentSerializer,
    )
    def add_cached_content(self, request, pk):
        """
        Add to the repository any new content that was cached using the remote since the last
        repository version was created.

        The `repository` field has to be provided.
        """
        serializer = serializers.RepositoryAddCachedContentSerializer(
            data=request.data, context={"request": request, "repository_pk": pk}
        )
        serializer.is_valid(raise_exception=True)

        repository = self.get_object()
        remote = serializer.validated_data.get("remote", repository.remote)

        result = dispatch(
            tasks.add_cached_content_to_repository,
            shared_resources=[remote],
            exclusive_resources=[repository],
            kwargs={
                "remote_pk": str(remote.pk),
                "repository_pk": str(repository.pk),
            },
        )
        return core.OperationPostponedResponse(result, request)


class RustRepositoryVersionViewSet(core.RepositoryVersionViewSet):
    """
    A ViewSet for a RustRepositoryVersion represents a single
    Rust repository version.
    """

    parent_viewset = RustRepositoryViewSet

    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {
                "action": ["list", "retrieve"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": (
                    "has_repository_model_or_domain_or_obj_perms:rust.view_rustrepository"
                ),
            },
            {
                "action": ["destroy"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_repository_model_or_domain_or_obj_perms:rust.delete_rustrepository",
                    "has_repository_model_or_domain_or_obj_perms:rust.view_rustrepository",
                ],
            },
        ],
    }


class RustDistributionViewSet(core.DistributionViewSet, core.RolesMixin):
    """
    A ViewSet for RustDistribution.
    """

    endpoint_name = "rust"
    queryset = models.RustDistribution.objects.all()
    serializer_class = serializers.RustDistributionSerializer
    queryset_filtering_required_permission = "rust.view_rustdistribution"

    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {
                "action": ["list", "my_permissions"],
                "principal": "authenticated",
                "effect": "allow",
            },
            {
                "action": ["create"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_perms:rust.add_rustdistribution",
                    "has_repo_or_repo_ver_param_model_or_domain_or_obj_perms:"
                    "rust.view_rustrepository",
                ],
            },
            {
                "action": ["retrieve"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_or_obj_perms:rust.view_rustdistribution",
            },
            {
                "action": ["update", "partial_update", "set_label", "unset_label"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:rust.change_rustdistribution",
                    "has_model_or_domain_or_obj_perms:rust.view_rustdistribution",
                    "has_repo_or_repo_ver_param_model_or_domain_or_obj_perms:"
                    "rust.view_rustrepository",
                ],
            },
            {
                "action": ["destroy"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:rust.delete_rustdistribution",
                    "has_model_or_domain_or_obj_perms:rust.view_rustdistribution",
                ],
            },
            {
                "action": ["list_roles", "add_role", "remove_role"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:rust.manage_roles_rustdistribution",
                ],
            },
        ],
        "creation_hooks": [
            {
                "function": "add_roles_for_object_creator",
                "parameters": {"roles": "rust.rustdistribution_owner"},
            },
        ],
        "queryset_scoping": {"function": "scope_queryset"},
    }

    LOCKED_ROLES = {
        "rust.rustdistribution_creator": ["rust.add_rustdistribution"],
        "rust.rustdistribution_owner": [
            "rust.view_rustdistribution",
            "rust.change_rustdistribution",
            "rust.delete_rustdistribution",
            "rust.manage_roles_rustdistribution",
            "rust.publish_rustdistribution",
            "rust.yank_rustdistribution",
        ],
        "rust.rustdistribution_publisher": [
            "rust.view_rustdistribution",
            "rust.publish_rustdistribution",
            "rust.yank_rustdistribution",
        ],
        "rust.rustdistribution_viewer": ["rust.view_rustdistribution"],
    }
