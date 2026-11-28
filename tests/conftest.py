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


@pytest.fixture
def api(db, monkeypatch):
	# the scrobbler apis on a fresh bottle app, called directly through wsgi
	import io
	import json as jsonlib
	from urllib.parse import urlencode
	from wsgiref.util import setup_testing_defaults

	import bottle
	from maloja.apis import apikeystore, init_apis

	monkeypatch.setattr(apikeystore, "keys", {"testclient": "testkey"})
	monkeypatch.setattr(apikeystore, "write_to_file", lambda: None)
	app = bottle.Bottle()
	init_apis(app)

	def call(method, path, query=None, form=None, json=None, headers=None):
		body = b""
		environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": urlencode(query or {})}
		if form is not None:
			body = urlencode(form).encode()
			environ["CONTENT_TYPE"] = "application/x-www-form-urlencoded"
		elif json is not None:
			body = jsonlib.dumps(json).encode()
			environ["CONTENT_TYPE"] = "application/json"
		for name, value in (headers or {}).items():
			environ["HTTP_" + name.upper().replace("-", "_")] = value
		environ["CONTENT_LENGTH"] = str(len(body))
		environ["wsgi.input"] = io.BytesIO(body)
		setup_testing_defaults(environ)

		result = {}

		def start_response(status, response_headers, exc_info=None):
			result["status"] = int(status.split()[0])
			result["headers"] = dict(response_headers)

		text = b"".join(app(environ, start_response)).decode()
		return result["status"], result["headers"], text

	return call
