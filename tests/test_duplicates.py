import pytest

from maloja.database import dbcache, duplicates, sqldb


@pytest.mark.parametrize("a,b,same", [
	("Ke$ha", "Kesha", True),
	("P!nk", "Pink", True),
	("A$AP Rocky", "ASAP Rocky", True),
	("Simon & Garfunkel", "Simon and Garfunkel", True),
	("Mumford + Sons", "Mumford & Sons", True),
	("The Beatles", "Beatles", True),
	("AC/DC", "ACDC", True),
	("Beyoncé", "Beyonce", True),
	("Pink", "Pinky", False),
	("!!!", "Chk Chk Chk", False),
])
def test_loose_key(a, b, same):
	assert (duplicates.loose_key(a) == duplicates.loose_key(b)) == same


@pytest.mark.parametrize("a,b,same", [
	("Help!", "Help", True),
	("Get Lucky (feat. Pharrell Williams)", "Get Lucky", True),
	("One Kiss (with Dua Lipa)", "One Kiss", True),
	("Song - feat. B", "Song", True),
	("Let It Be - Remastered 2009", "Let It Be", True),
	("Song [2011 Remaster]", "Song", True),
	("Come As You Are - Live", "Come As You Are", False),
	("Song (X Remix)", "Song", False),
])
def test_loose_title_key(a, b, same):
	assert (duplicates.loose_title_key(a) == duplicates.loose_title_key(b)) == same


def add(time, artists, title, origin="client:test", length=None):
	sqldb.add_scrobble({"time": time, "track": {"artists": artists, "title": title, "length": length}, "origin": origin, "rawscrobble": None})


def test_suggestions(db):
	add(1, ["Ke$ha"], "Tik Tok", origin="client:spotify")
	add(2, ["Ke$ha"], "Tik Tok", origin="client:spotify")
	add(3, ["Kesha"], "Praying", origin="client:apple")
	add(4, ["Daft Punk", "Pharrell Williams"], "Get Lucky (feat. Pharrell Williams)", length=369)
	add(5, ["Daft Punk"], "Get Lucky", length=367)
	add(6, ["Daft Punk"], "Get Lucky - Radio Edit", length=248)
	add(7, ["Nirvana"], "Come As You Are")
	add(8, ["Nirvana"], "Come As You Are - Live")

	found = db.get_duplicates()
	(artist,) = found["artists"]
	assert [(m["name"], m["scrobbles"], m["origins"]) for m in artist["members"]] == [
		("Ke$ha", 2, {"client:spotify": 2}),
		("Kesha", 1, {"client:apple": 1}),
	]
	# the feat. version only has the same artists once Pharrell is on both, so it's not suggested;
	# the radio edit is clearly shorter, and live versions stay apart
	assert found["tracks"] == []

	add(9, ["Daft Punk", "Pharrell Williams"], "Get Lucky")
	(track,) = db.get_duplicates()["tracks"]
	assert sorted(m["title"] for m in track["members"]) == ["Get Lucky", "Get Lucky (feat. Pharrell Williams)"]


def test_dismissed_until_a_new_member_appears(db):
	add(1, ["Ke$ha"], "Tik Tok")
	add(2, ["Kesha"], "Praying")
	(artist,) = db.get_duplicates()["artists"]
	db.dismiss_duplicate("artist", artist["ids"])
	assert db.get_duplicates()["artists"] == []
	add(3, ["Ke-sha"], "Blow")
	(artist,) = db.get_duplicates()["artists"]
	assert len(artist["members"]) == 3


def test_counts_are_kept_until_the_database_changes(db):
	add(1, ["Ke$ha"], "Tik Tok")
	add(2, ["Kesha"], "Praying")
	assert db.get_duplicate_counts() == {"artists": 1, "tracks": 0}
	# writing to sqldb directly doesn't clear any caches, so these aren't counted yet
	add(3, ["P!nk"], "So What")
	add(4, ["Pink"], "Trouble")
	assert db.get_duplicate_counts()["artists"] == 1
	dbcache.invalidate_caches()
	assert db.get_duplicate_counts()["artists"] == 2
