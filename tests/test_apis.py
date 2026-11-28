import hashlib
import json
import time

import pytest

from maloja.database import sqldb


LB_AUTH = {"Authorization": "Token testkey"}


def md5(text):
	return hashlib.md5(text.encode()).hexdigest()


def stored():
	return [(s["time"], s["track"]["artists"], s["track"]["title"]) for s in sqldb.get_scrobbles()]


def stored_track(timestamp):
	return sqldb.get_scrobble(timestamp=timestamp)["track"]


def listen(ts, artist="Artist", title="Title", **metadata):
	return {"listened_at": ts, "track_metadata": {"artist_name": artist, "track_name": title, **metadata}}


def lastfm_session(api, base="/apis/audioscrobbler/2.0/"):
	status, _, body = api("POST", base, form={
		"method": "auth.getMobileSession", "username": "me", "password": "testkey", "format": "json"
	})
	assert status == 200, body
	return json.loads(body)["session"]["key"]


def legacy_session(api, base="/apis/audioscrobbler_legacy/"):
	ts = str(int(time.time()))
	status, _, body = api("GET", base, query={"hs": "true", "u": "me", "t": ts, "a": md5(md5("testkey") + ts)})
	assert status == 200 and body.startswith("OK"), body
	return body.split("\n")[1]


def lastfm_scrobble(api, sk, fields, base="/apis/audioscrobbler/2.0/"):
	return api("POST", base, form={"method": "track.scrobble", "sk": sk, "format": "json", **fields})


def legacy_scrobble(api, sk, fields):
	return api("POST", "/apis/audioscrobbler_legacy/scrobble", form={"s": sk, **fields})


# each alias must reach its own api and keep the POST body
def _alias_lb(api):
	return api("POST", "/apis/lbrnz/1/submit-listens", json={"listen_type": "single", "payload": [listen(1700000000)]}, headers=LB_AUTH)


def _alias_lastfm(api):
	sk = lastfm_session(api, base="/apis/gnufm/2.0/")
	return lastfm_scrobble(api, sk, {"artist": "Artist", "track": "Title", "timestamp": "1700000000"}, base="/apis/gnufm/2.0/")


def _alias_legacy(api):
	sk = legacy_session(api, base="/apis/audioscrobbler/1.2/")
	return api("POST", "/apis/audioscrobbler/1.2/scrobble", form={"s": sk, "a[0]": "Artist", "t[0]": "Title", "i[0]": "1700000000"})


@pytest.mark.parametrize("send", [_alias_lb, _alias_lastfm, _alias_legacy], ids=["listenbrainz", "lastfm", "legacy"])
def test_scrobble_via_alias_path(api, send):
	status, _, body = send(api)
	assert status == 200, body
	assert stored() == [(1700000000, ["Artist"], "Title")]


def test_lastfm_indexed_batch_is_stored(api):
	sk = lastfm_session(api)
	status, _, body = lastfm_scrobble(api, sk, {
		"artist[0]": "One", "track[0]": "First", "timestamp[0]": "1700000000", "album[0]": "Record",
		"artist[1]": "Two", "track[1]": "Second", "timestamp[1]": "1700000300",
	})
	assert status == 200, body
	assert json.loads(body)["scrobbles"]["@attr"] == {"accepted": 2, "ignored": 0}
	assert stored() == [(1700000000, ["One"], "First"), (1700000300, ["Two"], "Second")]
	assert stored_track(1700000000)["album"]["albumtitle"] == "Record"


def test_listenbrainz_import_continues_after_duplicate(api):
	payload = [listen(1700000000, title="Already there")]
	api("POST", "/apis/listenbrainz/1/submit-listens", json={"listen_type": "single", "payload": payload}, headers=LB_AUTH)

	payload += [listen(1700000300, title="New one"), listen(1700000600, title="New two")]
	status, _, body = api("POST", "/apis/listenbrainz/1/submit-listens", json={"listen_type": "import", "payload": payload}, headers=LB_AUTH)

	assert status == 200, body
	assert [title for _, _, title in stored()] == ["Already there", "New one", "New two"]


def _resubmit_lastfm(api):
	sk = lastfm_session(api)
	fields = {"artist": "Artist", "track": "Title", "timestamp": "1700000000"}
	lastfm_scrobble(api, sk, fields)
	return lastfm_scrobble(api, sk, fields)


def _resubmit_legacy(api):
	sk = legacy_session(api)
	fields = {"a[0]": "Artist", "t[0]": "Title", "i[0]": "1700000000"}
	legacy_scrobble(api, sk, fields)
	return legacy_scrobble(api, sk, fields)


# clients resend cached scrobbles, a 500 makes them retry forever
@pytest.mark.parametrize("resubmit", [_resubmit_lastfm, _resubmit_legacy], ids=["lastfm", "legacy"])
def test_resubmitted_scrobble_is_not_an_error(api, resubmit):
	status, _, body = resubmit(api)
	assert status == 200, body
	assert len(stored()) == 1


def _meta_lastfm(api):
	sk = lastfm_session(api)
	lastfm_scrobble(api, sk, {"artist": "Artist", "track": "Title", "timestamp": "1700000000",
		"album": "Record", "albumArtist": "Album Artist", "duration": "200"})


def _meta_legacy(api):
	sk = legacy_session(api)
	legacy_scrobble(api, sk, {"a[0]": "Artist", "t[0]": "Title", "i[0]": "1700000000",
		"b[0]": "Record", "l[0]": "200"})


def _meta_lb(api):
	api("POST", "/apis/listenbrainz/1/submit-listens", headers=LB_AUTH, json={"listen_type": "single", "payload": [
		listen(1700000000, release_name="Record", additional_info={"duration_ms": 200500, "release_artist_name": "Album Artist"})
	]})


@pytest.mark.parametrize("send,album_artists", [
	(_meta_lastfm, ["Album Artist"]),
	(_meta_legacy, ["Artist"]),
	(_meta_lb, ["Album Artist"]),
], ids=["lastfm", "legacy", "listenbrainz"])
def test_album_and_length_are_kept(api, send, album_artists):
	send(api)
	track = stored_track(1700000000)
	assert (track["album"]["albumtitle"], track["album"]["artists"]) == ("Record", album_artists)
	assert track["length"] == 200


@pytest.mark.parametrize("body", [
	{"listen_type": "import", "payload": [listen(1700000000), listen(1700000300, artist="")]},
	{"listen_type": "import", "payload": [listen(1700000000), {"listened_at": 1700000300, "track_metadata": {"artist_name": "Artist"}}]},
	{"listen_type": "something", "payload": [listen(1700000000)]},
], ids=["empty-artist", "missing-title", "unknown-listen-type"])
def test_listenbrainz_rejects_malformed_listens(api, body):
	status, _, text = api("POST", "/apis/listenbrainz/1/submit-listens", json=body, headers=LB_AUTH)
	assert status == 400, text
	assert stored() == []


def test_listenbrainz_accepts_null_additional_info(api):
	status, _, text = api("POST", "/apis/listenbrainz/1/submit-listens", headers=LB_AUTH,
		json={"listen_type": "single", "payload": [listen(1700000000, additional_info=None)]})
	assert status == 200, text
	assert len(stored()) == 1


def _restart_scrobbler_apis():
	from maloja.apis.audioscrobbler import Audioscrobbler
	from maloja.apis.audioscrobbler_legacy import AudioscrobblerLegacy
	# init() is where the handlers set up their state at startup
	Audioscrobbler().init()
	AudioscrobblerLegacy().init()


@pytest.mark.parametrize("protocol", ["lastfm", "legacy"])
def test_session_survives_restart(api, protocol):
	if protocol == "lastfm":
		sk = lastfm_session(api)
	else:
		sk = legacy_session(api)
	_restart_scrobbler_apis()

	if protocol == "lastfm":
		status, _, body = lastfm_scrobble(api, sk, {"artist": "Artist", "track": "Title", "timestamp": "1700000000"})
	else:
		status, _, body = legacy_scrobble(api, sk, {"a[0]": "Artist", "t[0]": "Title", "i[0]": "1700000000"})
	assert status == 200, body
	assert len(stored()) == 1


def test_session_is_revoked_with_its_api_key(api):
	from maloja.apis import apikeystore
	sk = lastfm_session(api)
	del apikeystore.keys["testclient"]

	status, _, body = lastfm_scrobble(api, sk, {"artist": "Artist", "track": "Title", "timestamp": "1700000000"})
	assert status == 403, body
	assert stored() == []


def test_lastfm_now_playing_is_accepted(api):
	sk = lastfm_session(api)
	status, _, body = api("POST", "/apis/audioscrobbler/2.0/", form={
		"method": "track.updateNowPlaying", "sk": sk, "artist": "Artist", "track": "Title", "format": "json"
	})
	assert status == 200, body
	assert "error" not in json.loads(body)


def test_native_form_artists_are_not_split_into_letters(api):
	status, _, body = api("POST", "/apis/mlj_1/newscrobble", form={
		"key": "testkey", "artists": "Daft Punk", "title": "Title", "time": "1700000000"
	})
	assert status == 200, body
	assert stored() == [(1700000000, ["Daft Punk"], "Title")]
