import atexit
import os
import shutil
import tempfile

# maloja resolves its data directory at import time, so this must run before any maloja import
_datadir = tempfile.mkdtemp(prefix="maloja-tests-")
atexit.register(shutil.rmtree, _datadir, ignore_errors=True)
os.environ["MALOJA_DATA_DIRECTORY"] = _datadir
os.environ["MALOJA_SKIP_SETUP"] = "yes"

import pytest

from maloja.cleanup import CleanerAgent
from maloja.pkg_global.conf import data_dir, malojaconfig


@pytest.fixture
def agent():
	return CleanerAgent()


@pytest.fixture
def settings(monkeypatch):
	# the environment layer is in memory only, so nothing is written to settings.ini
	def set(**values):
		for key, value in values.items():
			monkeypatch.setitem(malojaconfig.environment, key.lower(), value)

	return set


@pytest.fixture
def agent_with_rules():
	rulesfile = data_dir["rules"]("tests.tsv")

	def make(*rules):
		with open(rulesfile, "w") as f:
			f.writelines("\t".join(rule) + "\n" for rule in rules)
		return CleanerAgent()

	yield make
	if os.path.exists(rulesfile):
		os.remove(rulesfile)
