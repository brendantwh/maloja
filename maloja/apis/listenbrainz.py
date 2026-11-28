from ._base import APIHandler
from ._exceptions import *
from .. import database
import datetime
from ._apikeys import apikeystore

from ..pkg_global.conf import malojaconfig


class Listenbrainz(APIHandler):
	__apiname__ = "Listenbrainz"
	__doclink__ = "https://listenbrainz.readthedocs.io/en/production/"
	__aliases__ = [
		"listenbrainz/1",
		"lbrnz/1"
	]

	def init(self):
		self.methods = {
			"submit-listens":self.submit,
			"validate-token":self.validate_token
		}
		self.errors = {
			BadAuthException: (401, {"code": 401, "error": "You need to provide an Authorization header."}),
			InvalidAuthException: (401, {"code": 401, "error": "Incorrect Authorization"}),
			InvalidMethodException: (200, {"code": 200, "error": "Invalid Method"}),
			MalformedJSONException: (400, {"code": 400, "error": "Invalid JSON document submitted."}),
			Exception: (500, {"code": 500, "error": "Unspecified server error."})
		}

	def get_method(self,pathnodes,keys):
		if not pathnodes:
			raise InvalidMethodException()
		return pathnodes.pop(0)

	def submit(self,pathnodes,keys):
		try:
			token = self.get_token_from_request_keys(keys)
		except Exception:
			raise BadAuthException()

		client = apikeystore.check_and_identify_key(token)

		if not client:
			raise InvalidAuthException()

		try:
			listentype = keys["listen_type"]
			payload = keys["payload"]
		except Exception:
			raise MalformedJSONException()

		if listentype == "playing_now":
			return 200,{"status":"ok"}
		if listentype not in ["single","import"] or not isinstance(payload,list):
			raise MalformedJSONException()

		# check the whole submission first, so a bad listen doesn't leave half of it written
		rawscrobbles = [listen_to_rawscrobble(listen) for listen in payload]
		for rawscrobble in rawscrobbles:
			try:
				self.scrobble(rawscrobble,client=client)
			except database.exceptions.MissingScrobbleParameters:
				# the parser removed everything, e.g. a rule that ignores the artist
				pass

		return 200,{"status":"ok"}


	def validate_token(self,pathnodes,keys):
		try:
			token = self.get_token_from_request_keys(keys)
		except Exception:
			raise BadAuthException()
		if not apikeystore.check_key(token):
			raise InvalidAuthException()
		else:
			return 200,{"code":200,"message":"Token valid.","valid":True,"user_name":malojaconfig["NAME"]}

	def get_token_from_request_keys(self,keys):
		if 'token' in keys:
			return keys.get("token").strip()
		if 'Authorization' in keys:
			auth = keys.get("Authorization")
			if auth.startswith('token '):
				return auth.replace("token ","",1).strip()
			if auth.startswith('Token '):
				return auth.replace("Token ","",1).strip()
		raise BadAuthException()


def listen_to_rawscrobble(listen):
	try:
		metadata = listen["track_metadata"]
		artiststr, titlestr = metadata["artist_name"], metadata["track_name"]
	except (KeyError, TypeError):
		raise MalformedJSONException()
	if not isinstance(artiststr,str) or not isinstance(titlestr,str) or not artiststr.strip() or not titlestr.strip():
		raise MalformedJSONException()
	additional = metadata.get("additional_info")
	if not isinstance(additional,dict):
		additional = {}

	try:
		timestamp = int(listen["listened_at"])
	except (KeyError, TypeError, ValueError):
		timestamp = None

	rawscrobble = {
		'track_artists':[artiststr],
		'track_title':titlestr,
		'scrobble_time':timestamp,
		# fields that will not be consumed by regular scrobbling will go into 'extra'
		**{k:additional[k] for k in ['track_mbid', 'release_mbid', 'artist_mbids','recording_mbid','tags'] if k in additional}
	}

	if metadata.get("release_name"):
		rawscrobble['album_title'] = metadata["release_name"]
		albumartists = additional.get("release_artist_names") or additional.get("release_artist_name")
		if isinstance(albumartists,str):
			albumartists = [albumartists]
		if albumartists:
			rawscrobble['album_artists'] = albumartists

	# the spec uses milliseconds, some clients send seconds
	length = None
	try:
		if additional.get("duration_ms") is not None:
			length = int(additional["duration_ms"]) // 1000
		elif additional.get("duration") is not None:
			length = int(additional["duration"])
	except (TypeError, ValueError):
		pass
	if length:
		rawscrobble['track_length'] = length

	return rawscrobble
