from ._base import APIHandler
from ._exceptions import *
from .. import database
from ._apikeys import apikeystore
from . import _sessions
import time

from bottle import request

class AudioscrobblerLegacy(APIHandler):
	__apiname__ = "Legacy Audioscrobbler"
	__doclink__ = "https://web.archive.org/web/20190531021725/https://www.last.fm/api/submissions"
	__aliases__ = [
		"audioscrobbler_legacy",
		"audioscrobbler/1.2"
	]

	def init(self):

		self.seen_tokens = set()
		self.methods = {
			"handshake":self.handshake,
			"nowplaying":self.now_playing,
			"scrobble":self.submit_scrobble
		}
		self.errors = {
			BadAuthException: (403, "BADAUTH\n"),
			InvalidAuthException: (403, "BADAUTH\n"),
			InvalidMethodException: (400, "FAILED\n"),
			InvalidSessionKey: (403, "BADSESSION\n"),
			Exception: (500, "FAILED\n")
		}

	def get_method(self,pathnodes,keys):
		if keys.get("hs") == 'true': return 'handshake'
		else: return pathnodes[0]

	def handshake(self,pathnodes,keys):
		auth = keys.get("a")
		timestamp = keys.get("t")
		apikey = keys.get("api_key")
		host = keys.get("Host")
		protocol = 'http' if (keys.get("u") == 'nossl') else request.urlparts.scheme

		if auth is not None:
			for client in apikeystore:
				key = apikeystore[client]
				if self.check_token(auth,key,timestamp):
					sessionkey = self.generate_key(client)
					return 200, (
						"OK\n"
						f"{sessionkey}\n"
						f"{protocol}://{host}/apis/audioscrobbler_legacy/nowplaying\n"
						f"{protocol}://{host}/apis/audioscrobbler_legacy/scrobble\n"
						)
			else:
				raise InvalidAuthException()
		else:
			raise BadAuthException()

	def now_playing(self,pathnodes,keys):
		# I see no implementation in the other compatible APIs, so I have just
		# created a route that always says it was successful except if the
		# session is invalid
		self.get_client(keys)
		return 200,"OK\n"

	def get_client(self,keys):
		client = _sessions.get_client(keys.get("s"),api="audioscrobbler_legacy")
		if not client:
			raise InvalidSessionKey()
		return client

	def submit_scrobble(self,pathnodes,keys):
		client = self.get_client(keys)
		for count in range(50):
			artist_key = f"a[{count}]"
			album_key = f"b[{count}]"
			length_key = f"l[{count}]"
			track_key = f"t[{count}]"
			time_key = f"i[{count}]"
			if artist_key not in keys or track_key not in keys:
				return 200,"OK\n"
			artiststr,titlestr = keys[artist_key], keys[track_key]
			try:
				timestamp = int(keys[time_key])
			except Exception:
				timestamp = None

			scrobble = {
				'track_artists':[artiststr],
				'track_title':titlestr,
				'scrobble_time':timestamp,
			}
			if keys.get(album_key):
				scrobble['album_title'] = keys[album_key]
			try:
				scrobble['track_length'] = int(keys[length_key])
			except (KeyError, ValueError):
				pass

			self.scrobble(scrobble, client=client)
		return 200,"OK\n"


	def check_token(self, received_token, expected_key, ts):
		# reject old timestamps to minimize replay attack vector
		try:
			ts_int = int(ts)
		except (ValueError, TypeError):
			return False
		if abs(int(time.time()) - ts_int) > 180:
			return False
		# reject recently seen, this is ok to do in memory since attack surface
		# is minimal
		if (expected_key, ts_int) in self.seen_tokens:
			return False
		else:
			self.seen_tokens.add((expected_key, ts_int))

		expected_token = md5(md5(expected_key) + ts)
		return received_token == expected_token

	def generate_key(self,client):
		return _sessions.create_session(client,api="audioscrobbler_legacy")


import hashlib

def md5(input):
	m = hashlib.md5()
	m.update(bytes(input,encoding="utf-8"))
	return m.hexdigest()
