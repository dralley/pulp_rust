import uuid

import pytest

from pulpcore.client.pulp_rust.exceptions import ApiException

from pulp_rust.tests.functional.utils import CRATES_IO_URL


class TestRepositoryRBAC:
    """Test RBAC for Rust repositories."""

    @pytest.mark.parallel
    def test_basic_crud(self, gen_users, rust_repo_api_client, try_action):
        alice, bob, charlie = gen_users("rustrepository")

        # List — everyone can list, but only viewers/creators see repos they have access to
        try_action(alice, rust_repo_api_client, "list", 200)
        b_list = try_action(bob, rust_repo_api_client, "list", 200)
        c_list = try_action(charlie, rust_repo_api_client, "list", 200)
        assert (b_list.count, c_list.count) == (0, 0)

        # Create — only creator (bob) can create
        try_action(
            alice,
            rust_repo_api_client,
            "create",
            403,
            {"name": str(uuid.uuid4())},
        )
        repo = try_action(
            bob,
            rust_repo_api_client,
            "create",
            201,
            {"name": str(uuid.uuid4())},
        )
        try_action(
            charlie,
            rust_repo_api_client,
            "create",
            403,
            {"name": str(uuid.uuid4())},
        )

        # Read — alice has model-level viewer, bob is owner (creation hook), charlie has nothing
        try_action(alice, rust_repo_api_client, "read", 200, repo.pulp_href)
        try_action(bob, rust_repo_api_client, "read", 200, repo.pulp_href)
        try_action(charlie, rust_repo_api_client, "read", 404, repo.pulp_href)

        # Update — only owner (bob)
        update_args = [repo.pulp_href, {"name": str(uuid.uuid4())}]
        try_action(alice, rust_repo_api_client, "partial_update", 403, *update_args)
        try_action(bob, rust_repo_api_client, "partial_update", 202, *update_args)
        try_action(charlie, rust_repo_api_client, "partial_update", 404, *update_args)

        # Delete — only owner (bob)
        try_action(alice, rust_repo_api_client, "delete", 403, repo.pulp_href)
        try_action(charlie, rust_repo_api_client, "delete", 404, repo.pulp_href)
        try_action(bob, rust_repo_api_client, "delete", 202, repo.pulp_href)

    @pytest.mark.parallel
    def test_modify(self, gen_users, rust_repo_api_client, rust_repo_factory, try_action):
        alice, bob, charlie = gen_users("rustrepository")

        with bob:
            repo = rust_repo_factory()

        body = {}
        try_action(alice, rust_repo_api_client, "modify", 403, repo.pulp_href, body)
        try_action(bob, rust_repo_api_client, "modify", 202, repo.pulp_href, body)
        try_action(charlie, rust_repo_api_client, "modify", 404, repo.pulp_href, body)

    @pytest.mark.parallel
    def test_role_management(self, gen_users, rust_repo_api_client, rust_repo_factory, try_action):
        alice, bob, charlie = gen_users("rustrepository")

        with bob:
            repo = rust_repo_factory()

        # Only owner (bob) can manage roles
        role_body = {"role": "rust.rustrepository_viewer", "users": [alice.username]}
        try_action(alice, rust_repo_api_client, "list_roles", 403, repo.pulp_href)
        try_action(bob, rust_repo_api_client, "list_roles", 200, repo.pulp_href)
        try_action(charlie, rust_repo_api_client, "list_roles", 404, repo.pulp_href)

        try_action(bob, rust_repo_api_client, "add_role", 201, repo.pulp_href, role_body)

        # Now alice has object-level viewer on this specific repo
        try_action(alice, rust_repo_api_client, "read", 200, repo.pulp_href)

        try_action(bob, rust_repo_api_client, "remove_role", 201, repo.pulp_href, role_body)


class TestRemoteRBAC:
    """Test RBAC for Rust remotes."""

    @pytest.mark.parallel
    def test_basic_crud(self, gen_users, rust_remote_api_client, try_action):
        alice, bob, charlie = gen_users("rustremote")

        # Create
        try_action(
            alice,
            rust_remote_api_client,
            "create",
            403,
            {"name": str(uuid.uuid4()), "url": CRATES_IO_URL},
        )
        remote = try_action(
            bob,
            rust_remote_api_client,
            "create",
            201,
            {"name": str(uuid.uuid4()), "url": CRATES_IO_URL},
        )
        try_action(
            charlie,
            rust_remote_api_client,
            "create",
            403,
            {"name": str(uuid.uuid4()), "url": CRATES_IO_URL},
        )

        # Read
        try_action(alice, rust_remote_api_client, "read", 200, remote.pulp_href)
        try_action(bob, rust_remote_api_client, "read", 200, remote.pulp_href)
        try_action(charlie, rust_remote_api_client, "read", 404, remote.pulp_href)

        # Update
        update_args = [remote.pulp_href, {"name": str(uuid.uuid4())}]
        try_action(alice, rust_remote_api_client, "partial_update", 403, *update_args)
        try_action(bob, rust_remote_api_client, "partial_update", 202, *update_args)
        try_action(charlie, rust_remote_api_client, "partial_update", 404, *update_args)

        # Delete
        try_action(alice, rust_remote_api_client, "delete", 403, remote.pulp_href)
        try_action(charlie, rust_remote_api_client, "delete", 404, remote.pulp_href)
        try_action(bob, rust_remote_api_client, "delete", 202, remote.pulp_href)


class TestDistributionRBAC:
    """Test RBAC for Rust distributions."""

    @pytest.mark.parallel
    def test_basic_crud(self, gen_users, rust_distro_api_client, rust_repo_factory, try_action):
        alice, bob, charlie = gen_users(["rustdistribution", "rustrepository"])

        with bob:
            repo = rust_repo_factory()

        # Create
        distro_body = {
            "name": str(uuid.uuid4()),
            "base_path": str(uuid.uuid4()),
            "repository": repo.pulp_href,
        }
        try_action(alice, rust_distro_api_client, "create", 403, distro_body)

        distro_body["name"] = str(uuid.uuid4())
        distro_body["base_path"] = str(uuid.uuid4())
        distro = try_action(bob, rust_distro_api_client, "create", 202, distro_body)
        distro_href = distro.created_resources[0]

        distro_body["name"] = str(uuid.uuid4())
        distro_body["base_path"] = str(uuid.uuid4())
        try_action(charlie, rust_distro_api_client, "create", 403, distro_body)

        # Read
        try_action(alice, rust_distro_api_client, "read", 200, distro_href)
        try_action(bob, rust_distro_api_client, "read", 200, distro_href)
        try_action(charlie, rust_distro_api_client, "read", 404, distro_href)

        # Update
        update_args = [distro_href, {"name": str(uuid.uuid4())}]
        try_action(alice, rust_distro_api_client, "partial_update", 403, *update_args)
        try_action(bob, rust_distro_api_client, "partial_update", 202, *update_args)
        try_action(charlie, rust_distro_api_client, "partial_update", 404, *update_args)

        # Delete
        try_action(alice, rust_distro_api_client, "delete", 403, distro_href)
        try_action(charlie, rust_distro_api_client, "delete", 404, distro_href)
        try_action(bob, rust_distro_api_client, "delete", 202, distro_href)

    @pytest.mark.parallel
    def test_cross_resource_permissions(
        self, gen_users, rust_distro_api_client, rust_repo_factory, try_action
    ):
        """Creating a distribution linked to a repo requires view permission on that repo."""
        _alice, bob, _charlie = gen_users(["rustdistribution", "rustrepository"])

        # Admin creates a repo that bob doesn't own
        admin_repo = rust_repo_factory()

        with bob:
            bob_repo = rust_repo_factory()

        # Bob can create a distribution linked to his own repo
        body = {
            "name": str(uuid.uuid4()),
            "base_path": str(uuid.uuid4()),
            "repository": bob_repo.pulp_href,
        }
        try_action(bob, rust_distro_api_client, "create", 202, body)

        # Bob cannot create a distribution linked to admin's repo
        body = {
            "name": str(uuid.uuid4()),
            "base_path": str(uuid.uuid4()),
            "repository": admin_repo.pulp_href,
        }
        try_action(bob, rust_distro_api_client, "create", 403, body)


class TestObjectLevelPermissions:
    """Test object-level role assignments."""

    def test_object_role_grants_access(self, gen_user, rust_repo_factory, rust_repo_api_client):
        """A user with an object-level role can access that specific object."""
        repo = rust_repo_factory()

        user = gen_user()

        # No access without roles
        with user:
            with pytest.raises(ApiException) as exc:
                rust_repo_api_client.read(repo.pulp_href)
            assert exc.value.status == 404

        # Grant object-level viewer role
        rust_repo_api_client.add_role(
            repo.pulp_href,
            {"role": "rust.rustrepository_viewer", "users": [user.username]},
        )

        # Now the user can read
        with user:
            result = rust_repo_api_client.read(repo.pulp_href)
            assert result.pulp_href == repo.pulp_href

        # But still can't modify
        with user:
            with pytest.raises(ApiException) as exc:
                rust_repo_api_client.modify(repo.pulp_href, {})
            assert exc.value.status == 403

    def test_owner_has_full_access(
        self, gen_user, rust_repo_factory, rust_repo_api_client, monitor_task
    ):
        """A user with the owner role can perform all actions on the object."""
        creator = gen_user(model_roles=["rust.rustrepository_creator"])

        with creator:
            repo = rust_repo_factory()

        # Creator gets owner role via creation hook — can do everything
        with creator:
            rust_repo_api_client.read(repo.pulp_href)
            monitor_task(
                rust_repo_api_client.partial_update(
                    repo.pulp_href, {"name": str(uuid.uuid4())}
                ).task
            )
            monitor_task(rust_repo_api_client.modify(repo.pulp_href, {}).task)
            rust_repo_api_client.list_roles(repo.pulp_href)
            monitor_task(rust_repo_api_client.delete(repo.pulp_href).task)

    def test_no_cross_object_access(self, gen_user, rust_repo_factory, rust_repo_api_client):
        """Object-level permission on repo A does not grant access to repo B."""
        repo_a = rust_repo_factory()
        repo_b = rust_repo_factory()

        user = gen_user(object_roles=[("rust.rustrepository_viewer", repo_a.pulp_href)])

        with user:
            rust_repo_api_client.read(repo_a.pulp_href)
            with pytest.raises(ApiException) as exc:
                rust_repo_api_client.read(repo_b.pulp_href)
            assert exc.value.status == 404
