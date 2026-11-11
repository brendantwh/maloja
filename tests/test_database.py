from maloja.database import sqldb


def scrobble(time, artists, title, album=None):
	track = {"artists": artists, "title": title}
	if album:
		track["album"] = album
	sqldb.add_scrobble({"time": time, "track": track, "origin": "client:test", "rawscrobble": None})
	return sqldb.get_scrobble(timestamp=time)["track"]


def test_new_track_keeps_artist_order(db):
	# Alpha exists first, so it has the lower id
	scrobble(1, ["Alpha"], "Solo")
	track = scrobble(2, ["Zed", "Alpha"], "Duet", album={"albumtitle": "Record", "artists": ["Zed", "Alpha"]})
	assert track["artists"] == ["Zed", "Alpha"]
	assert track["album"]["artists"] == ["Zed", "Alpha"]
