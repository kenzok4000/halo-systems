"""The command line reports a bad name or a missing key in one line, without a traceback."""

import contextlib
import io
import os
import unittest
from unittest import mock

from halo_systems.cli import main

CLEAN = {k: v for k, v in os.environ.items() if not k.startswith(("HALO_", "SWARMLAB_")) and k != "NEBIUS_API_KEY"}


def run(argv):
    err = io.StringIO()
    with mock.patch.dict(os.environ, CLEAN, clear=True), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, err.getvalue()


class CliErrorsTest(unittest.TestCase):
    def test_unknown_scenario(self):
        code, err = run(["run", "nosuch"])
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("halo: error: unknown scenario 'nosuch'; choose from open_transit"), err)

    def test_unknown_supervisor(self):
        code, err = run(["run", "forest_trail", "--config", "improved+bogus"])
        self.assertEqual(code, 2)
        self.assertIn("halo: error: unknown supervisor 'bogus'", err)

    def test_nemotron_without_a_model_id(self):
        code, err = run(["run", "forest_trail", "--config", "improved+nemotron"])
        self.assertEqual(code, 2)
        self.assertIn("halo: error: no model id", err)

    def test_debug_shows_the_traceback(self):
        with mock.patch.dict(os.environ, {**CLEAN, "HALO_DEBUG": "1"}, clear=True):
            with self.assertRaises(KeyError):
                main(["run", "nosuch"])


if __name__ == "__main__":
    unittest.main()
