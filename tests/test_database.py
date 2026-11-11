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


def test_aliases_resolve_lookups(db):
	track = scrobble(1, ["Daft Punk"], "Get Lucky", album={"albumtitle": "Random Access Memories", "artists": ["Daft Punk"]})
	sqldb.add_alias("artist", "Daft Pnuk", sqldb.get_artist_id("Daft Punk"))
	sqldb.add_alias("track", "Get Lucky (feat. Pharrell Williams)", sqldb.get_track_id(track), artists=["Daft Punk", "Pharrell Williams"])
	sqldb.add_alias("album", "RAM", sqldb.get_album_id(track["album"]), artists=["Daft Punk"])

	assert scrobble(2, ["Daft Pnuk"], "Get Lucky") == track
	assert scrobble(3, ["Daft Punk", "Pharrell Williams"], "Get Lucky (feat. Pharrell Williams)") == track
	assert sqldb.get_album_id({"albumtitle": "RAM", "artists": ["Daft Pnuk"]}) == sqldb.get_album_id(track["album"])
	# the track alias needs the same artists
	assert scrobble(4, ["Pharrell Williams"], "Get Lucky (feat. Pharrell Williams)") != track


def test_aliases_can_be_switched_off(db, settings):
	sqldb.add_alias("artist", "Old Name", sqldb.get_artist_id("New Name"))
	settings(LEARN_ALIASES=False)
	assert sqldb.get_artist_id("Old Name") != sqldb.get_artist_id("New Name")


def test_clean_db_keeps_aliases_of_existing_entities(db):
	scrobble(1, ["Kept"], "Song")
	sqldb.add_alias("artist", "Kept Old", sqldb.get_artist_id("Kept"))
	sqldb.add_alias("artist", "Gone Old", sqldb.get_artist_id("Gone"))
	sqldb.clean_db()
	with sqldb.engine.connect() as conn:
		names = [row.name_normalized for row in conn.execute(sqldb.DB["aliases"].select())]
	assert names == ["kept old"]
