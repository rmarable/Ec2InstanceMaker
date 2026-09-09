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
