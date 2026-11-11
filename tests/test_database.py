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


def aliases():
	with sqldb.engine.connect() as conn:
		return {(row.entity_type, row.name_normalized): row.target_id for row in conn.execute(sqldb.DB["aliases"].select())}


def test_renames_and_merges_record_aliases(db):
	scrobble(1, ["Beyonce Knowles"], "Halo", album={"albumtitle": "I Am... Sasha Fierce", "artists": ["Beyonce Knowles"]})
	db.edit_artist(sqldb.get_artist_id("Beyonce Knowles"), "Beyoncé")
	db.edit_album(sqldb.get_album_id({"albumtitle": "I Am... Sasha Fierce", "artists": ["Beyoncé"]}), {"albumtitle": "I Am Sasha Fierce"})
	lucky = scrobble(2, ["Daft Punk"], "Get Lucky")
	feat = scrobble(3, ["Daft Punk", "Pharrell Williams"], "Get Lucky (feat. Pharrell Williams)")
	db.merge_tracks(sqldb.get_track_id(lucky), [sqldb.get_track_id(feat)])

	assert scrobble(4, ["Beyonce Knowles"], "Halo")["artists"] == ["Beyoncé"]
	assert scrobble(5, ["Beyoncé"], "Halo", album={"albumtitle": "I Am... Sasha Fierce", "artists": ["Beyoncé"]})["album"]["albumtitle"] == "I Am Sasha Fierce"
	assert scrobble(6, ["Daft Punk", "Pharrell Williams"], "Get Lucky (feat. Pharrell Williams)") == lucky


def test_aliases_follow_later_merges_and_renames(db):
	for i, name in enumerate(["A", "B", "C"]):
		scrobble(i, [name], f"Song {name}")
	db.merge_artists(sqldb.get_artist_id("B"), [sqldb.get_artist_id("A")])
	db.merge_artists(sqldb.get_artist_id("C"), [sqldb.get_artist_id("B")])
	assert aliases() == {("artist", "a"): sqldb.get_artist_id("C"), ("artist", "b"): sqldb.get_artist_id("C")}
	# naming an artist like an alias drops the alias
	db.edit_artist(sqldb.get_artist_id("C"), "A")
	assert set(aliases()) == {("artist", "b"), ("artist", "c")}


def test_no_aliases_recorded_when_switched_off(db, settings):
	settings(LEARN_ALIASES=False)
	scrobble(1, ["Old"], "Song")
	db.edit_artist(sqldb.get_artist_id("Old"), "New")
	assert aliases() == {}


def test_adding_and_removing_artists_records_aliases(db):
	track = scrobble(1, ["Daft Punk", "Pharrell Williams"], "Get Lucky", album={"albumtitle": "RAM", "artists": ["Daft Punk", "Pharrell Williams"]})
	track_id, album_id = sqldb.get_track_id(track), sqldb.get_album_id(track["album"])
	pharrell = sqldb.get_artist_id("Pharrell Williams")
	db.associate_tracks_to_artist(pharrell, [track_id], remove=True)
	db.associate_albums_to_artist(pharrell, [album_id], remove=True)
	# a player that still credits both artists lands on the edited track and album
	assert sqldb.get_track_id({"artists": ["Daft Punk", "Pharrell Williams"], "title": "Get Lucky"}) == track_id
	assert sqldb.get_album_id({"albumtitle": "RAM", "artists": ["Daft Punk", "Pharrell Williams"]}) == album_id
	nile = sqldb.get_artist_id("Nile Rodgers")
	db.associate_tracks_to_artist(nile, [track_id])
	assert sqldb.get_track_id({"artists": ["Daft Punk"], "title": "Get Lucky"}) == track_id
	# removing an artist that isn't on the track changes nothing, so nothing is recorded
	def rows():
		with sqldb.engine.connect() as conn:
			return conn.execute(sqldb.DB["aliases"].select().with_only_columns(sqldb.DB["aliases"].c.name_normalized, sqldb.DB["aliases"].c.artists_normalized)).all()
	before = rows()
	db.associate_tracks_to_artist(sqldb.get_artist_id("Someone Else"), [track_id], remove=True)
	assert rows() == before


def test_clean_db_keeps_aliases_of_existing_entities(db):
	scrobble(1, ["Kept"], "Song")
	sqldb.add_alias("artist", "Kept Old", sqldb.get_artist_id("Kept"))
	sqldb.add_alias("artist", "Gone Old", sqldb.get_artist_id("Gone"))
	sqldb.clean_db()
	with sqldb.engine.connect() as conn:
		names = [row.name_normalized for row in conn.execute(sqldb.DB["aliases"].select())]
	assert names == ["kept old"]


def test_parser_keeps_known_artists_whole(db):
	def parse(artist):
		return db.rawscrobble_to_scrobbledict({"track_artists": [artist], "track_title": "Song"})["track"]["artists"]

	assert parse("Mumford & Sons") == ["Mumford", "Sons"]
	# becomes known as soon as it exists, without a restart
	scrobble(1, ["Mumford & Sons"], "Little Lion Man")
	assert parse("Mumford & Sons") == ["Mumford & Sons"]
	# and an old name of a known artist counts too
	db.edit_artist(sqldb.get_artist_id("Mumford & Sons"), "Mumford and Sons")
	assert parse("Mumford & Sons") == ["Mumford & Sons"]
