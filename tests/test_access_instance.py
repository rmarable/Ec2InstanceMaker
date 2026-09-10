"""Unit tests for access_instance.py -- extracted into testable functions
(each taking refer_to_docs_and_quit as an explicit argument, same
dependency-injection pattern as instance_builder.py/manage_instance.py)
specifically so this script could be unit tested at all; it used to be a
flat top-level script with no importable functions, like make_instance.py
still is.
"""

from unittest.mock import MagicMock, patch

import pytest

import access_instance


def _quitting_mock():
    return MagicMock(side_effect=SystemExit(1))


class TestBuildAccessCommand:
    def test_default_menu_index_omits_flag(self):
        cmd = access_instance.build_access_command("dev01", 0)
        assert cmd == ["python3", "access_instance.dev01.py"]

    def test_nonzero_menu_index_appends_flag(self):
        cmd = access_instance.build_access_command("fam01", 2)
        assert cmd == ["python3", "access_instance.fam01.py", "--menu_index=2"]


class TestDispatchToInstanceAccessScript:
    def test_missing_instance_quits(self):
        quit_fn = _quitting_mock()
        with patch("os.path.exists", return_value=False), pytest.raises(SystemExit):
            access_instance.dispatch_to_instance_access_script("dev01", 0, quit_fn)
        quit_fn.assert_called_once()
        assert "dev01" in quit_fn.call_args.args[0]

    def test_runs_script_in_instance_data_dir_and_returns_exit_code(self):
        quit_fn = _quitting_mock()
        run_mock = MagicMock(return_value=MagicMock(returncode=0))
        with patch("os.path.exists", return_value=True):
            result = access_instance.dispatch_to_instance_access_script("dev01", 0, quit_fn, run_access_script=run_mock)
        run_mock.assert_called_once_with(["python3", "access_instance.dev01.py"], cwd="instance_data/dev01")
        assert result == 0
        quit_fn.assert_not_called()

    def test_menu_index_forwarded_to_command(self):
        quit_fn = _quitting_mock()
        run_mock = MagicMock(return_value=MagicMock(returncode=0))
        with patch("os.path.exists", return_value=True):
            access_instance.dispatch_to_instance_access_script("fam01", 3, quit_fn, run_access_script=run_mock)
        run_mock.assert_called_once_with(["python3", "access_instance.fam01.py", "--menu_index=3"], cwd="instance_data/fam01")

    def test_keyboard_interrupt_returns_1(self, capsys):
        quit_fn = _quitting_mock()
        run_mock = MagicMock(side_effect=KeyboardInterrupt)
        with patch("os.path.exists", return_value=True):
            result = access_instance.dispatch_to_instance_access_script("dev01", 0, quit_fn, run_access_script=run_mock)
        assert result == 1
        assert "Interrupted." in capsys.readouterr().out

    def test_invalid_instance_name_rejected_before_touching_filesystem_or_subprocess(self):
        # Defense-in-depth: main() already validates instance_name first,
        # but this proves the function is safe even called directly,
        # bypassing main() -- same path-traversal class of bug an
        # adversarial review found and fixed in mcp_server.py.
        quit_fn = _quitting_mock()
        run_mock = MagicMock()
        with patch("os.path.exists") as exists_mock, pytest.raises(SystemExit):
            access_instance.dispatch_to_instance_access_script("../../etc/passwd", 0, quit_fn, run_access_script=run_mock)
        quit_fn.assert_called_once()
        exists_mock.assert_not_called()
        run_mock.assert_not_called()


class TestParseArgs:
    def test_menu_index_defaults_to_zero(self):
        args = access_instance.parse_args(["-N", "dev01"])
        assert args.instance_name == "dev01"
        assert args.menu_index == 0

    def test_menu_index_is_parsed_as_an_integer(self):
        assert access_instance.parse_args(["-N", "dev01", "-m", "3"]).menu_index == 3

    def test_instance_name_is_required(self):
        with pytest.raises(SystemExit) as exc:
            access_instance.parse_args([])
        assert exc.value.code == 2

    def test_a_non_numeric_menu_index_is_an_argparse_error(self):
        with pytest.raises(SystemExit) as exc:
            access_instance.parse_args(["-N", "dev01", "-m", "three"])
        assert exc.value.code == 2


class TestMain:
    """main() is the wiring between parse_args, the name validation and the
    dispatch. It had no coverage: the pieces were tested, the order they
    run in was not -- and the order is the security-relevant part, since
    validation has to happen before instance_name reaches a filesystem path
    or a subprocess.
    """

    def test_validates_the_name_before_dispatching(self, monkeypatch, capsys):
        dispatch = MagicMock()
        monkeypatch.setattr(access_instance, "dispatch_to_instance_access_script", dispatch)

        with pytest.raises(SystemExit):
            access_instance.main(["-N", "../../etc/passwd"])

        dispatch.assert_not_called()
        assert "instance_name" in capsys.readouterr().out

    def test_propagates_the_dispatch_exit_code(self, monkeypatch):
        monkeypatch.setattr(access_instance, "dispatch_to_instance_access_script", MagicMock(return_value=7))

        with pytest.raises(SystemExit) as exc:
            access_instance.main(["-N", "dev01"])

        assert exc.value.code == 7

    def test_a_successful_dispatch_exits_zero(self, monkeypatch):
        monkeypatch.setattr(access_instance, "dispatch_to_instance_access_script", MagicMock(return_value=0))

        with pytest.raises(SystemExit) as exc:
            access_instance.main(["-N", "dev01"])

        assert exc.value.code == 0

    def test_menu_index_is_forwarded_to_the_dispatch(self, monkeypatch):
        dispatch = MagicMock(return_value=0)
        monkeypatch.setattr(access_instance, "dispatch_to_instance_access_script", dispatch)

        with pytest.raises(SystemExit):
            access_instance.main(["-N", "dev01", "-m", "2"])

        assert dispatch.call_args.args[0] == "dev01"
        assert dispatch.call_args.args[1] == 2
