"""Unit tests for manage_instance.py -- extracted into testable functions
(each taking refer_to_docs_and_quit as an explicit argument, same
dependency-injection pattern as instance_builder.py) specifically so this
script could be unit tested at all; it used to be a flat top-level script
with no importable functions, like make_instance.py/access_instance.py
still are.
"""

import contextlib
from unittest.mock import MagicMock, mock_open, patch

import pytest
from botocore.exceptions import ClientError

import manage_instance


def _quitting_mock():
    return MagicMock(side_effect=SystemExit(1))


def _client_error(code, message="error"):
    return ClientError({"Error": {"Code": code, "Message": message}}, "SomeOperation")


def _ec2_client(instances, pages=None):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = pages if pages is not None else [{"Reservations": [{"Instances": instances}]}]
    return client


def _paginate_call(client):
    return client.get_paginator.return_value.paginate


class TestTagValue:
    def test_returns_matching_tag_value(self):
        instance = {"Tags": [{"Key": "Name", "Value": "dev01"}, {"Key": "OperatingSystem", "Value": "al2023"}]}
        assert manage_instance.tag_value(instance, "Name") == "dev01"
        assert manage_instance.tag_value(instance, "OperatingSystem") == "al2023"

    def test_missing_tag_returns_default(self):
        instance = {"Tags": [{"Key": "Name", "Value": "dev01"}]}
        assert manage_instance.tag_value(instance, "InstanceOwner") == "?"
        assert manage_instance.tag_value(instance, "InstanceOwner", default="unknown") == "unknown"

    def test_no_tags_key_at_all_returns_default(self):
        assert manage_instance.tag_value({}, "Name") == "?"


class TestResolveRegion:
    def test_explicit_region_wins_without_reading_vars_file(self):
        quit_fn = _quitting_mock()
        with patch("os.path.exists") as mock_exists:
            result = manage_instance.resolve_region("dev01", "us-west-2", quit_fn)
        mock_exists.assert_not_called()
        assert result == "us-west-2"
        quit_fn.assert_not_called()

    def test_falls_back_to_vars_file_region(self):
        quit_fn = _quitting_mock()
        with patch("os.path.exists", return_value=True), patch("builtins.open", mock_open(read_data="region: us-east-1\n")):
            result = manage_instance.resolve_region("dev01", None, quit_fn)
        assert result == "us-east-1"
        quit_fn.assert_not_called()

    def test_missing_vars_file_and_no_region_quits(self):
        quit_fn = _quitting_mock()
        with patch("os.path.exists", return_value=False), pytest.raises(SystemExit):
            manage_instance.resolve_region("dev01", None, quit_fn)
        quit_fn.assert_called_once()

    def test_vars_file_without_region_field_quits(self):
        quit_fn = _quitting_mock()
        with patch("os.path.exists", return_value=True), patch("builtins.open", mock_open(read_data="base_os: al2023\n")), pytest.raises(SystemExit):
            manage_instance.resolve_region("dev01", None, quit_fn)
        quit_fn.assert_called_once()


class TestFindManagedInstances:
    def test_returns_matched_instances(self):
        instances = [{"InstanceId": "i-abc123", "State": {"Name": "running"}, "Tags": [{"Key": "Name", "Value": "dev01"}]}]
        client = _ec2_client(instances)
        quit_fn = _quitting_mock()

        result = manage_instance.find_managed_instances(client, "dev01", "us-east-1", quit_fn)

        assert result == instances
        client.get_paginator.assert_called_once_with("describe_instances")
        called_filters = _paginate_call(client).call_args.kwargs["Filters"]
        name_filter = next(f for f in called_filters if f["Name"] == "tag:Name")
        assert name_filter["Values"] == ["dev01", "dev01-*"]
        managed_by_filter = next(f for f in called_filters if f["Name"] == "tag:ManagedBy")
        assert managed_by_filter["Values"] == ["Ec2InstanceMaker"]
        quit_fn.assert_not_called()

    def test_paginates_across_multiple_pages(self):
        # Real describe_instances results always carry the Name tag, since
        # the tag:Name filter is what selected them.
        page_one = [{"InstanceId": "i-abc123", "Tags": [{"Key": "Name", "Value": "dev01-0"}]}]
        page_two = [{"InstanceId": "i-def456", "Tags": [{"Key": "Name", "Value": "dev01-1"}]}]
        client = _ec2_client(None, pages=[{"Reservations": [{"Instances": page_one}]}, {"Reservations": [{"Instances": page_two}]}])
        quit_fn = _quitting_mock()

        result = manage_instance.find_managed_instances(client, "dev01", "us-east-1", quit_fn)

        assert result == page_one + page_two
        quit_fn.assert_not_called()

    def test_an_unrelated_family_sharing_the_name_prefix_is_excluded(self):
        # EC2's tag filter only does trailing-wildcard matching, and
        # instance_name legitimately contains hyphens, so the "<name>-*"
        # filter needed for family members ("<name>-${count.index}") also
        # matches a separately-built "web-prod-critical". Both are
        # Ec2InstanceMaker-managed, so the ManagedBy filter does not
        # separate them -- a stop or terminate aimed at "web" could take
        # out an unrelated production family.
        returned = [
            {"InstanceId": "i-self", "Tags": [{"Key": "Name", "Value": "web"}]},
            {"InstanceId": "i-family", "Tags": [{"Key": "Name", "Value": "web-1"}]},
            {"InstanceId": "i-unrelated", "Tags": [{"Key": "Name", "Value": "web-prod-critical"}]},
            {"InstanceId": "i-unrelated2", "Tags": [{"Key": "Name", "Value": "web-prod-critical-0"}]},
        ]
        client = _ec2_client(returned)
        quit_fn = _quitting_mock()

        result = manage_instance.find_managed_instances(client, "web", "us-east-1", quit_fn)

        assert [i["InstanceId"] for i in result] == ["i-self", "i-family"]
        quit_fn.assert_not_called()

    def test_only_prefix_collisions_are_dropped_not_the_whole_family(self):
        returned = [{"InstanceId": f"i-{n}", "Tags": [{"Key": "Name", "Value": f"dev01-{n}"}]} for n in range(3)]
        client = _ec2_client(returned)

        result = manage_instance.find_managed_instances(client, "dev01", "us-east-1", _quitting_mock())

        assert len(result) == 3

    def test_no_matches_quits(self):
        client = _ec2_client([])
        quit_fn = _quitting_mock()

        with pytest.raises(SystemExit):
            manage_instance.find_managed_instances(client, "dev01", "us-east-1", quit_fn)
        quit_fn.assert_called_once()
        assert "dev01" in quit_fn.call_args.args[0]

    def test_api_error_quits(self):
        client = MagicMock()
        client.get_paginator.return_value.paginate.side_effect = _client_error("AccessDeniedException")
        quit_fn = _quitting_mock()

        with pytest.raises(SystemExit):
            manage_instance.find_managed_instances(client, "dev01", "us-east-1", quit_fn)
        quit_fn.assert_called_once()


class TestListAllManagedInstances:
    def test_returns_all_managed_instances_with_no_name_filter(self):
        instances = [{"InstanceId": "i-abc123"}, {"InstanceId": "i-def456"}]
        client = _ec2_client(instances)
        quit_fn = _quitting_mock()

        result = manage_instance.list_all_managed_instances(client, "us-east-1", quit_fn)

        assert result == instances
        client.get_paginator.assert_called_once_with("describe_instances")
        called_filters = _paginate_call(client).call_args.kwargs["Filters"]
        assert all(f["Name"] != "tag:Name" for f in called_filters)
        managed_by_filter = next(f for f in called_filters if f["Name"] == "tag:ManagedBy")
        assert managed_by_filter["Values"] == ["Ec2InstanceMaker"]
        quit_fn.assert_not_called()

    def test_paginates_across_multiple_pages(self):
        page_one = [{"InstanceId": "i-abc123"}]
        page_two = [{"InstanceId": "i-def456"}]
        client = _ec2_client(None, pages=[{"Reservations": [{"Instances": page_one}]}, {"Reservations": [{"Instances": page_two}]}])
        quit_fn = _quitting_mock()

        result = manage_instance.list_all_managed_instances(client, "us-east-1", quit_fn)

        assert result == page_one + page_two
        quit_fn.assert_not_called()

    def test_empty_result_is_not_a_failure(self):
        client = _ec2_client([])
        quit_fn = _quitting_mock()

        result = manage_instance.list_all_managed_instances(client, "us-east-1", quit_fn)

        assert result == []
        quit_fn.assert_not_called()

    def test_api_error_quits(self):
        client = MagicMock()
        client.get_paginator.return_value.paginate.side_effect = _client_error("AccessDeniedException")
        quit_fn = _quitting_mock()

        with pytest.raises(SystemExit):
            manage_instance.list_all_managed_instances(client, "us-east-1", quit_fn)
        quit_fn.assert_called_once()


class TestPrintStatusTable:
    def test_reports_spot_yes_for_spot_instance(self, capsys):
        instances = [{"InstanceId": "i-abc123", "InstanceLifecycle": "spot", "State": {"Name": "running"}, "Tags": [{"Key": "Name", "Value": "dev01"}]}]
        manage_instance.print_status_table(instances)
        captured = capsys.readouterr()
        assert "i-abc123" in captured.out
        assert "Spot: Yes" in captured.out

    def test_reports_spot_no_for_ondemand_instance(self, capsys):
        instances = [{"InstanceId": "i-abc123", "State": {"Name": "running"}, "Tags": [{"Key": "Name", "Value": "dev01"}]}]
        manage_instance.print_status_table(instances)
        captured = capsys.readouterr()
        assert "Spot: No" in captured.out


class TestPrintAllInstancesTable:
    def test_no_instances_prints_message(self, capsys):
        manage_instance.print_all_instances_table([])
        captured = capsys.readouterr()
        assert "No Ec2InstanceMaker-managed instances" in captured.out

    def test_spot_column_omitted_when_no_spot_instances(self, capsys):
        instances = [
            {
                "InstanceId": "i-abc123",
                "InstanceType": "t3.micro",
                "Tags": [{"Key": "Name", "Value": "dev01"}, {"Key": "OperatingSystem", "Value": "al2023"}, {"Key": "InstanceOwner", "Value": "alice"}],
            }
        ]
        manage_instance.print_all_instances_table(instances)
        captured = capsys.readouterr()
        assert "Spot" not in captured.out

    def test_spot_column_included_when_any_instance_is_spot(self, capsys):
        instances = [
            {
                "InstanceId": "i-abc123",
                "InstanceType": "t3.micro",
                "Tags": [{"Key": "Name", "Value": "dev01"}, {"Key": "OperatingSystem", "Value": "al2023"}, {"Key": "InstanceOwner", "Value": "alice"}],
            },
            {
                "InstanceId": "i-def456",
                "InstanceType": "t3.micro",
                "InstanceLifecycle": "spot",
                "Tags": [{"Key": "Name", "Value": "dev02"}, {"Key": "OperatingSystem", "Value": "al2023"}, {"Key": "InstanceOwner", "Value": "bob"}],
            },
        ]
        manage_instance.print_all_instances_table(instances)
        captured = capsys.readouterr()
        assert "Spot" in captured.out
        assert "Yes" in captured.out
        assert "No" in captured.out


class TestCheckSpotLifecycleConflict:
    def test_reboot_is_never_blocked(self):
        quit_fn = _quitting_mock()
        instances = [{"InstanceId": "i-abc123", "InstanceLifecycle": "spot"}]
        manage_instance.check_spot_lifecycle_conflict(instances, "reboot", quit_fn)
        quit_fn.assert_not_called()

    def test_stop_against_ondemand_is_fine(self):
        quit_fn = _quitting_mock()
        instances = [{"InstanceId": "i-abc123"}]
        manage_instance.check_spot_lifecycle_conflict(instances, "stop", quit_fn)
        quit_fn.assert_not_called()

    def test_stop_against_spot_quits(self):
        quit_fn = _quitting_mock()
        instances = [{"InstanceId": "i-abc123", "InstanceLifecycle": "spot"}]
        with pytest.raises(SystemExit):
            manage_instance.check_spot_lifecycle_conflict(instances, "stop", quit_fn)
        quit_fn.assert_called_once()
        assert "i-abc123" in quit_fn.call_args.args[0]

    def test_start_against_spot_quits(self):
        quit_fn = _quitting_mock()
        instances = [{"InstanceId": "i-abc123", "InstanceLifecycle": "spot"}]
        with pytest.raises(SystemExit):
            manage_instance.check_spot_lifecycle_conflict(instances, "start", quit_fn)
        quit_fn.assert_called_once()

    def test_mixed_family_with_one_spot_member_still_quits(self):
        quit_fn = _quitting_mock()
        instances = [{"InstanceId": "i-ondemand"}, {"InstanceId": "i-spot", "InstanceLifecycle": "spot"}]
        with pytest.raises(SystemExit):
            manage_instance.check_spot_lifecycle_conflict(instances, "stop", quit_fn)


class TestTerminateViaKillScript:
    def test_missing_kill_script_quits(self):
        quit_fn = _quitting_mock()
        with patch("os.path.exists", return_value=False), pytest.raises(SystemExit):
            manage_instance.terminate_via_kill_script("dev01", True, quit_fn)
        quit_fn.assert_called_once()

    def test_auto_confirm_skips_prompt_and_runs_kill_script(self):
        quit_fn = _quitting_mock()
        run_mock = MagicMock(return_value=MagicMock(returncode=0))
        confirm_mock = MagicMock()
        with patch("os.path.exists", return_value=True), patch("manage_instance.instance_lock", return_value=contextlib.nullcontext()):
            result = manage_instance.terminate_via_kill_script("dev01", True, quit_fn, run_kill_script=run_mock, confirm_input=confirm_mock)
        confirm_mock.assert_not_called()
        run_mock.assert_called_once_with(["bash", "kill-instance.dev01.sh"])
        assert result == 0

    def test_confirmation_declined_aborts_without_running(self):
        quit_fn = _quitting_mock()
        run_mock = MagicMock()
        confirm_mock = MagicMock(return_value="no")
        with patch("os.path.exists", return_value=True), pytest.raises(SystemExit):
            manage_instance.terminate_via_kill_script("dev01", False, quit_fn, run_kill_script=run_mock, confirm_input=confirm_mock)
        run_mock.assert_not_called()

    def test_confirmation_accepted_runs_kill_script(self):
        quit_fn = _quitting_mock()
        run_mock = MagicMock(return_value=MagicMock(returncode=0))
        confirm_mock = MagicMock(return_value="yes")
        with patch("os.path.exists", return_value=True), patch("manage_instance.instance_lock", return_value=contextlib.nullcontext()):
            manage_instance.terminate_via_kill_script("dev01", False, quit_fn, run_kill_script=run_mock, confirm_input=confirm_mock)
        run_mock.assert_called_once_with(["bash", "kill-instance.dev01.sh"])

    def test_lock_held_elsewhere_quits_without_running_kill_script(self):
        quit_fn = _quitting_mock()
        run_mock = MagicMock()
        with patch("os.path.exists", return_value=True), patch("manage_instance.instance_lock", side_effect=SystemExit(1)) as lock_mock, pytest.raises(SystemExit):
            manage_instance.terminate_via_kill_script("dev01", True, quit_fn, run_kill_script=run_mock)
        lock_mock.assert_called_once_with("dev01", quit_fn)
        run_mock.assert_not_called()


class TestSpotLifecycleDoesNotFailOpen:
    """InstanceLifecycle is simply absent for on-demand instances, so
    checking only that key meant anything unexpected -- a missing field, a
    value AWS changes later -- read as on-demand and the stop was allowed.
    A one-time Spot Instance that gets stopped can never be restarted, so
    failing open here is the expensive direction.
    """

    def _instance(self, instance_id, lifecycle=None, request_type=None):
        instance = {"InstanceId": instance_id, "Tags": [{"Key": "Name", "Value": "dev01"}]}
        if lifecycle:
            instance["InstanceLifecycle"] = lifecycle
        if request_type:
            instance["Tags"].append({"Key": "EC2RequestType", "Value": request_type})
        return instance

    def test_lifecycle_field_still_blocks(self):
        with pytest.raises(SystemExit):
            manage_instance.check_spot_lifecycle_conflict([self._instance("i-1", lifecycle="spot")], "stop", _quitting_mock())

    def test_the_tag_blocks_even_when_the_lifecycle_field_is_missing(self):
        # DEFAULT_EC2_TEMPLATE.j2 sets EC2RequestType=spot via create-tags on
        # the spot path only, so its presence is a positive spot signal.
        with pytest.raises(SystemExit):
            manage_instance.check_spot_lifecycle_conflict([self._instance("i-1", request_type="spot")], "stop", _quitting_mock())

    def test_a_genuine_ondemand_instance_is_unaffected(self):
        manage_instance.check_spot_lifecycle_conflict([self._instance("i-1", request_type="ondemand")], "stop", _quitting_mock())

    def test_reboot_is_still_allowed_against_spot(self):
        manage_instance.check_spot_lifecycle_conflict([self._instance("i-1", lifecycle="spot")], "reboot", _quitting_mock())


ONDEMAND_INSTANCE = {
    "InstanceId": "i-ondemand01",
    "InstanceType": "t3.micro",
    "State": {"Name": "running"},
    "Tags": [{"Key": "Name", "Value": "dev01"}],
}
SPOT_INSTANCE = {
    "InstanceId": "i-spot01",
    "InstanceType": "t3.micro",
    "State": {"Name": "running"},
    "InstanceLifecycle": "spot",
    "Tags": [{"Key": "Name", "Value": "dev01"}],
}


class TestMainCliWiring:
    """main() is 89 lines of CLI wiring and had no direct coverage at all.
    Every function it calls was well tested; the dispatch between them was
    not -- so the -c bypass, the --region requirement for --list-all, and
    the terminate delegation were only ever exercised through
    mcp_server.py's parallel wrappers, which do not share this code.
    """

    def _patch(self, monkeypatch, instances=None, client=None):
        ec2_client = client or MagicMock()
        monkeypatch.setattr(manage_instance.boto3, "client", MagicMock(return_value=ec2_client))
        monkeypatch.setattr(manage_instance, "resolve_region", MagicMock(return_value="us-east-1"))
        monkeypatch.setattr(manage_instance, "find_managed_instances", MagicMock(return_value=instances if instances is not None else [ONDEMAND_INSTANCE]))
        return ec2_client

    def test_terminate_delegates_to_the_kill_script_and_returns_its_code(self, monkeypatch):
        terminate = MagicMock(return_value=3)
        monkeypatch.setattr(manage_instance, "terminate_via_kill_script", terminate)

        with pytest.raises(SystemExit) as exc:
            manage_instance.main(["-N", "dev01", "-A", "terminate"])

        assert exc.value.code == 3
        terminate.assert_called_once_with("dev01", False, manage_instance.refer_to_docs_and_quit)

    def test_terminate_passes_the_auto_confirm_bypass_through(self, monkeypatch):
        terminate = MagicMock(return_value=0)
        monkeypatch.setattr(manage_instance, "terminate_via_kill_script", terminate)

        with pytest.raises(SystemExit):
            manage_instance.main(["-N", "dev01", "-A", "terminate", "-c"])

        assert terminate.call_args.args[1] is True

    def test_list_all_requires_a_region(self, capsys):
        with pytest.raises(SystemExit):
            manage_instance.main(["-l"])
        assert "--region/-r is required" in capsys.readouterr().out

    def test_list_all_lists_without_an_instance_name(self, monkeypatch):
        self._patch(monkeypatch)
        listed = MagicMock(return_value=[ONDEMAND_INSTANCE])
        monkeypatch.setattr(manage_instance, "list_all_managed_instances", listed)

        with pytest.raises(SystemExit) as exc:
            manage_instance.main(["-l", "-r", "us-east-1"])

        assert exc.value.code == 0
        listed.assert_called_once()

    def test_status_prints_and_exits_without_touching_power_state(self, monkeypatch):
        ec2_client = self._patch(monkeypatch)

        with pytest.raises(SystemExit) as exc:
            manage_instance.main(["-N", "dev01", "-S"])

        assert exc.value.code == 0
        ec2_client.start_instances.assert_not_called()
        ec2_client.stop_instances.assert_not_called()

    def test_instance_name_is_required_for_a_power_action(self, capsys):
        with pytest.raises(SystemExit):
            manage_instance.main(["-A", "start"])
        assert "--instance_name/-N is required" in capsys.readouterr().out

    def test_action_is_required(self):
        # -A/-S/-l are a required mutually exclusive group, so argparse
        # exits 2 rather than running anything.
        with pytest.raises(SystemExit) as exc:
            manage_instance.main(["-N", "dev01"])
        assert exc.value.code == 2

    def test_start_with_auto_confirm_skips_the_prompt(self, monkeypatch):
        ec2_client = self._patch(monkeypatch)
        # input() would block forever if the bypass did not work.
        monkeypatch.setattr("builtins.input", MagicMock(side_effect=AssertionError("prompted despite -c")))

        with pytest.raises(SystemExit) as exc:
            manage_instance.main(["-N", "dev01", "-A", "start", "-c"])

        assert exc.value.code == 0
        ec2_client.start_instances.assert_called_once_with(InstanceIds=["i-ondemand01"])

    def test_declining_the_prompt_aborts_without_calling_aws(self, monkeypatch):
        ec2_client = self._patch(monkeypatch)
        monkeypatch.setattr("builtins.input", MagicMock(return_value="no"))

        with pytest.raises(SystemExit) as exc:
            manage_instance.main(["-N", "dev01", "-A", "stop"])

        assert exc.value.code == 1
        ec2_client.stop_instances.assert_not_called()

    def test_confirming_the_prompt_proceeds(self, monkeypatch):
        ec2_client = self._patch(monkeypatch)
        monkeypatch.setattr("builtins.input", MagicMock(return_value="  YES  "))

        with pytest.raises(SystemExit) as exc:
            manage_instance.main(["-N", "dev01", "-A", "stop"])

        assert exc.value.code == 0
        ec2_client.stop_instances.assert_called_once()

    def test_reboot_is_dispatched_to_reboot_instances(self, monkeypatch):
        ec2_client = self._patch(monkeypatch)

        with pytest.raises(SystemExit):
            manage_instance.main(["-N", "dev01", "-A", "reboot", "-c"])

        ec2_client.reboot_instances.assert_called_once_with(InstanceIds=["i-ondemand01"])

    def test_an_aws_error_during_the_action_is_reported_cleanly(self, monkeypatch, capsys):
        ec2_client = MagicMock()
        ec2_client.start_instances.side_effect = ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "nope"}}, "StartInstances")
        self._patch(monkeypatch, client=ec2_client)

        with pytest.raises(SystemExit):
            manage_instance.main(["-N", "dev01", "-A", "start", "-c"])
        # The raw botocore error must be surfaced, not swallowed.
        out = capsys.readouterr().out
        assert "AWS API error" in out
        assert "UnauthorizedOperation" in out

    def test_a_spot_instance_blocks_start_before_any_aws_call(self, monkeypatch, capsys):
        ec2_client = self._patch(monkeypatch, instances=[SPOT_INSTANCE])

        with pytest.raises(SystemExit):
            manage_instance.main(["-N", "dev01", "-A", "start", "-c"])

        ec2_client.start_instances.assert_not_called()
        assert "Spot Instances" in capsys.readouterr().out

    def test_an_invalid_instance_name_is_refused_before_any_aws_call(self, monkeypatch, capsys):
        find = MagicMock()
        monkeypatch.setattr(manage_instance, "find_managed_instances", find)

        with pytest.raises(SystemExit):
            manage_instance.main(["-N", "../etc/passwd", "-A", "start", "-c"])

        find.assert_not_called()
        assert "instance_name" in capsys.readouterr().out
