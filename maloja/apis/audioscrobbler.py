from ._base import APIHandler
from ._exceptions import *
from .. import database
from ._apikeys import apikeystore

from bottle import response

class Audioscrobbler(APIHandler):
	__apiname__ = "Audioscrobbler"
	__doclink__ = "https://www.last.fm/api/scrobbling"
	__aliases__ = [
		"audioscrobbler/2.0",
		"gnufm/2.0",
		"gnukebox/2.0",
	]

	def init(self):

		# no need to save these on disk, clients can always request a new session
		self.mobile_sessions = {}
		self.methods = {
			"auth.getMobileSession":self.authmobile,
			"track.scrobble":self.submit_scrobble,
			"track.updateNowPlaying":self.now_playing
		}
		self.errors = {
			BadAuthException: (400, {"error": 6, "message": "Requires authentication"}),
			InvalidAuthException: (401, {"error": 4, "message": "Invalid credentials"}),
			InvalidMethodException: (200, {"error": 3, "message": "Invalid method"}),
			InvalidSessionKey: (403, {"error": 9, "message": "Invalid session key"}),
			Exception: (500, {"error": 8, "message": "Operation failed"})
		}

	# xml string escaping: https://stackoverflow.com/a/28703510
	def xml_escape(self, str_xml: str):
		str_xml = str_xml.replace("&", "&amp;")
		str_xml = str_xml.replace("<", "&lt;")
		str_xml = str_xml.replace(">", "&gt;")
		str_xml = str_xml.replace("\"", "&quot;")
		str_xml = str_xml.replace("'", "&apos;")
		return str_xml

	def get_method(self,pathnodes,keys):
		return keys.get("method")

	def generate_key(self,client):
		key = "".join(
		    str(
		        random.choice(
		            list(range(10)) + list("abcdefghijklmnopqrstuvwxyz") +
		            list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))) for _ in range(64))

		self.mobile_sessions[key] = client
		return key

	def authmobile(self,pathnodes,keys):
		token = keys.get("authToken")
		user = keys.get("username")
		password = keys.get("password")
		format = keys.get("format") or "xml" # Audioscrobbler 2.0 uses XML by default
		# either username and password
		if user is not None and password is not None:
			client = apikeystore.check_and_identify_key(password)
			if client:
				sessionkey = self.generate_key(client)
				if format == "json":
					return 200,{"session":{"key":sessionkey}}
				else:
					return 200,"""<lfm status="ok">
	<session>
		<name>%s</name>
		<key>%s</key>
		<subscriber>0</subscriber>
	</session>
</lfm>""" % (self.xml_escape(user), self.xml_escape(sessionkey))
			else:
				raise InvalidAuthException()
		# or username and token (deprecated by lastfm)
		elif user is not None and token is not None:
			for client in apikeystore:
				key = apikeystore[client]
				if md5(user + md5(key)) == token:
					sessionkey = self.generate_key(client)
					if format == "json":
						return 200,{"session":{"key":sessionkey}}
					else:
						return 200,"""<lfm status="ok">
	<session>
		<name>%s</name>
		<key>%s</key>
		<subscriber>0</subscriber>
	</session>
</lfm>""" % (self.xml_escape(user), self.xml_escape(sessionkey))
			raise InvalidAuthException()
		else:
			raise BadAuthException()

	def get_client(self,keys):
		key = keys.get("sk")
		if key is None:
			raise InvalidSessionKey()
		client = self.mobile_sessions.get(key)
		if not client:
			raise InvalidSessionKey()
		return client

	def now_playing(self,pathnodes,keys):
		# we don't track what is playing, but clients expect this to succeed
		self.get_client(keys)
		if keys.get("format") == "json":
			return 200,{"nowplaying":{}}
		return self.xml_response("<nowplaying></nowplaying>")

	def submit_scrobble(self,pathnodes,keys):
		client = self.get_client(keys)

		# a single scrobble may come without an index, a batch uses artist[0], track[0] etc
		suffixes = [""] + [f"[{num}]" for num in range(50)]
		accepted = ignored = 0
		for suffix in suffixes:
			artiststr,titlestr = keys.get("artist" + suffix), keys.get("track" + suffix)
			if artiststr is None and titlestr is None:
				continue
			rawscrobble = {
				'track_artists':[artiststr] if artiststr else [],
				'track_title':titlestr or "",
				'scrobble_time':to_int(keys.get("timestamp" + suffix))
			}
			if keys.get("album" + suffix):
				rawscrobble['album_title'] = keys["album" + suffix]
				if keys.get("albumArtist" + suffix):
					rawscrobble['album_artists'] = [keys["albumArtist" + suffix]]
			if to_int(keys.get("duration" + suffix)):
				rawscrobble['track_length'] = to_int(keys["duration" + suffix])

			try:
				result = self.scrobble(rawscrobble,client=client)
			except database.exceptions.MissingScrobbleParameters:
				result = None
			if result is None:
				ignored += 1
			else:
				accepted += 1

		if keys.get("format") == "json":
			return 200,{"scrobbles":{"@attr":{"accepted":accepted,"ignored":ignored}}}
		return self.xml_response(f'<scrobbles accepted="{accepted}" ignored="{ignored}"></scrobbles>')

	def xml_response(self,content):
		response.content_type = "application/xml; charset=utf-8"
		return 200,f'<?xml version="1.0" encoding="utf-8"?>\n<lfm status="ok">{content}</lfm>'


import hashlib
import random

def to_int(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return None

def md5(input):
	m = hashlib.md5()
	m.update(bytes(input,encoding="utf-8"))
	return m.hexdigest()
