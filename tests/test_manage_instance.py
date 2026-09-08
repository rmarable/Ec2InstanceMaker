"""Unit tests for manage_instance.py -- extracted into testable functions
(each taking refer_to_docs_and_quit as an explicit argument, same
dependency-injection pattern as instance_builder.py) specifically so this
script could be unit tested at all; it used to be a flat top-level script
with no importable functions, like make_instance.py/access_instance.py
still are.
"""

from unittest.mock import MagicMock, mock_open, patch

import pytest
from botocore.exceptions import ClientError

import manage_instance


def _quitting_mock():
    return MagicMock(side_effect=SystemExit(1))


def _client_error(code, message="error"):
    return ClientError({"Error": {"Code": code, "Message": message}}, "SomeOperation")


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
    def _ec2_client(self, instances, pages=None):
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = pages if pages is not None else [{"Reservations": [{"Instances": instances}]}]
        return client

    def _paginate_call(self, client):
        return client.get_paginator.return_value.paginate

    def test_returns_matched_instances(self):
        instances = [{"InstanceId": "i-abc123", "State": {"Name": "running"}, "Tags": [{"Key": "Name", "Value": "dev01"}]}]
        client = self._ec2_client(instances)
        quit_fn = _quitting_mock()

        result = manage_instance.find_managed_instances(client, "dev01", "us-east-1", quit_fn)

        assert result == instances
        client.get_paginator.assert_called_once_with("describe_instances")
        called_filters = self._paginate_call(client).call_args.kwargs["Filters"]
        name_filter = next(f for f in called_filters if f["Name"] == "tag:Name")
        assert name_filter["Values"] == ["dev01", "dev01-*"]
        managed_by_filter = next(f for f in called_filters if f["Name"] == "tag:ManagedBy")
        assert managed_by_filter["Values"] == ["Ec2InstanceMaker"]
        quit_fn.assert_not_called()

    def test_paginates_across_multiple_pages(self):
        page_one = [{"InstanceId": "i-abc123"}]
        page_two = [{"InstanceId": "i-def456"}]
        client = self._ec2_client(None, pages=[{"Reservations": [{"Instances": page_one}]}, {"Reservations": [{"Instances": page_two}]}])
        quit_fn = _quitting_mock()

        result = manage_instance.find_managed_instances(client, "dev01", "us-east-1", quit_fn)

        assert result == page_one + page_two
        quit_fn.assert_not_called()

    def test_no_matches_quits(self):
        client = self._ec2_client([])
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
    def _ec2_client(self, instances, pages=None):
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = pages if pages is not None else [{"Reservations": [{"Instances": instances}]}]
        return client

    def _paginate_call(self, client):
        return client.get_paginator.return_value.paginate

    def test_returns_all_managed_instances_with_no_name_filter(self):
        instances = [{"InstanceId": "i-abc123"}, {"InstanceId": "i-def456"}]
        client = self._ec2_client(instances)
        quit_fn = _quitting_mock()

        result = manage_instance.list_all_managed_instances(client, "us-east-1", quit_fn)

        assert result == instances
        client.get_paginator.assert_called_once_with("describe_instances")
        called_filters = self._paginate_call(client).call_args.kwargs["Filters"]
        assert all(f["Name"] != "tag:Name" for f in called_filters)
        managed_by_filter = next(f for f in called_filters if f["Name"] == "tag:ManagedBy")
        assert managed_by_filter["Values"] == ["Ec2InstanceMaker"]
        quit_fn.assert_not_called()

    def test_paginates_across_multiple_pages(self):
        page_one = [{"InstanceId": "i-abc123"}]
        page_two = [{"InstanceId": "i-def456"}]
        client = self._ec2_client(None, pages=[{"Reservations": [{"Instances": page_one}]}, {"Reservations": [{"Instances": page_two}]}])
        quit_fn = _quitting_mock()

        result = manage_instance.list_all_managed_instances(client, "us-east-1", quit_fn)

        assert result == page_one + page_two
        quit_fn.assert_not_called()

    def test_empty_result_is_not_a_failure(self):
        client = self._ec2_client([])
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
        with patch("os.path.exists", return_value=True):
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
        with patch("os.path.exists", return_value=True):
            manage_instance.terminate_via_kill_script("dev01", False, quit_fn, run_kill_script=run_mock, confirm_input=confirm_mock)
        run_mock.assert_called_once_with(["bash", "kill-instance.dev01.sh"])
