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


def test_merging_artists_keeps_artist_order(db):
	scrobble(1, ["Alpha", "Beta Old", "Gamma"], "Trio")
	scrobble(2, ["Beta Old", "Beta"], "Both")
	scrobble(3, ["Beta"], "Solo")
	db.merge_artists(sqldb.get_artist_id("Beta"), [sqldb.get_artist_id("Beta Old")])
	assert sqldb.get_scrobble(timestamp=1)["track"]["artists"] == ["Alpha", "Beta", "Gamma"]
	assert sqldb.get_scrobble(timestamp=2)["track"]["artists"] == ["Beta"]
