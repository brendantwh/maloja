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

from maloja.cleanup import CleanerAgent, parse_rules_tsv
from maloja.pkg_global.conf import malojaconfig


@pytest.fixture
def agent():
	return CleanerAgent(rules=[])


@pytest.fixture
def settings(monkeypatch):
	# the environment layer is in memory only, so nothing is written to settings.ini
	def set(**values):
		for key, value in values.items():
			monkeypatch.setitem(malojaconfig.environment, key.lower(), value)

	return set


def _empty_db(monkeypatch):
	# all tests share one data directory, so each database test starts from an empty one
	from maloja import database
	from maloja.database import dbcache, rules, sqldb
	from maloja.pkg_global import conf

	# in AUX_MODE cache invalidation is a no-op, so cached ids would leak between tests
	monkeypatch.setattr(conf, "AUX_MODE", False)
	monkeypatch.setitem(database.dbstatus, "healthy", True)
	with sqldb.engine.begin() as conn:
		for table in reversed(sqldb.meta.sorted_tables):
			conn.execute(table.delete())
	dbcache.invalidate_entity_cache()
	dbcache.invalidate_caches()
	sqldb.forget_known_artists()
	# a fresh parser, so rules loaded by an earlier test don't stick around
	monkeypatch.setattr(database, "cla", CleanerAgent(rules_loader=rules.load_rules, known_artist=sqldb.is_known_artist))
	return database


@pytest.fixture
def db(monkeypatch):
	return _empty_db(monkeypatch)


@pytest.fixture(scope="module")
def module_db():
	with pytest.MonkeyPatch.context() as mp:
		yield _empty_db(mp), mp


@pytest.fixture
def agent_with_rules():
	def make(*rules):
		parsed, errors = parse_rules_tsv("".join("\t".join(rule) + "\n" for rule in rules))
		assert not errors
		return CleanerAgent(rules=parsed)

	return make
