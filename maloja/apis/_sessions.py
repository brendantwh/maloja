# session keys of the last.fm style apis, kept on disk so clients stay logged in across restarts

import json
import os
import secrets
from threading import Lock

from doreah.logging import log

from ..pkg_global.conf import data_dir
from ._apikeys import apikeystore


_lock = Lock()


def _file():
	return data_dir['auth']("scrobbler_sessions.json")


def _read():
	try:
		with open(_file()) as f:
			sessions = json.load(f)
		return sessions if isinstance(sessions, dict) else {}
	except FileNotFoundError:
		return {}
	except (OSError, ValueError) as e:
		log(f"Could not read scrobbler sessions, clients will have to log in again: {e}", color='red')
		return {}


def _write(sessions):
	path = _file()
	tmp = path + ".tmp"
	with open(tmp, "w") as f:
		json.dump(sessions, f)
	os.replace(tmp, path)


def create_session(client, api):
	key = secrets.token_hex(32)
	with _lock:
		sessions = _read()
		# sessions of clients whose api key was deleted can't be used anymore
		sessions = {k: v for k, v in sessions.items() if v.get('client') in apikeystore}
		sessions[key] = {'client': client, 'api': api}
		_write(sessions)
	return key


def get_client(key, api):
	"""Returns the client a session belongs to, or None if the session is unknown or its api key is gone."""
	if not key:
		return None
	session = _read().get(key)
	if not session or session.get('api') != api:
		return None
	client = session.get('client')
	return client if client in apikeystore else None
