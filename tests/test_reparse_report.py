import os

import pytest
import sqlalchemy as sql

from maloja import database
from maloja.cleanup import CleanerAgent
from maloja.database import sqldb
from maloja.database.reparse_report import build_report
from maloja.pkg_global.conf import data_dir


# each case: what an older parser or rule set stored, and the raw input it got
# (stored artists, stored title, raw artists, raw title, origin)
CASES = [
	(["Alpha"], "Steady", ["Alpha"], "Steady", "client:test"),
	(["Alpha"], "Steady", ["Alpha"], "Steady", "client:test"),
	# stored before '&' was a delimiter: a known artist now, so it stays whole
	(["Alpha & Beta"], "Duet", ["Alpha & Beta"], "Duet", "client:test"),
	# stored under another spelling, so the raw name is not known and gets split
	(["Mu Nu"], "Pair", ["Mu & Nu"], "Pair", "client:test"),
	# stored before remaster tags were stripped
	(["Gamma"], "Song", ["Gamma"], "Song", "client:test"),
	(["Gamma"], "Song (2009 Remaster)", ["Gamma"], "Song (2009 Remaster)", "client:test"),
	# stored before featured artists were extracted
	(["Delta"], "Track X", ["Delta"], "Track X", "client:test"),
	(["Delta"], "Track X", ["Delta"], "Track X (feat. Epsilon)", "client:test"),
	# renamed by a rule added since
	(["Zeta Old"], "Tune", ["Zeta Old"], "Tune", "client:test"),
	# imports don't keep raw data
	(["Eta"], "Imported", None, None, "import:lastfm"),
	# would be rejected today, since Spotify is an invalid artist
	(["Spotify"], "Ad", ["Spotify"], "Ad", "client:test"),
	# old spellings that aliases now map to the stored entities
	(["Iota"], "Hymn", ["Iota Old"], "Hymn", "client:test"),
	(["Kappa"], "Anthem", ["Kappa"], "Anthem (feat. Lambda)", "client:test"),
	# stored in alphabetical order, credited the other way round
	(["Omega", "Psi"], "Order", ["Psi & Omega"], "Order", "client:test"),
]


@pytest.fixture(scope="module")
def report(module_db):
	_, mp = module_db
	rulesfile = data_dir["rules"]("reparse_report_tests.tsv")
	with open(rulesfile, "w") as f:
		f.write("replaceartist\tZeta Old\tZeta\n")
	mp.setattr(database, "cla", CleanerAgent(known_artist=sqldb.is_known_artist))
	sqldb.add_scrobbles([
		{
			"time": 1000 + i,
			"track": {"artists": artists, "title": title},
			"origin": origin,
			"rawscrobble": {"track_artists": raw_artists, "track_title": raw_title, "scrobble_time": 1000 + i} if raw_artists else None,
		}
		for i, (artists, title, raw_artists, raw_title, origin) in enumerate(CASES)
	])
	sqldb.add_alias("artist", "Iota Old", sqldb.get_artist_id("Iota"))
	sqldb.add_alias("track", "Anthem (feat. Lambda)", sqldb.get_track_id({"artists": ["Kappa"], "title": "Anthem"}), artists=["Kappa", "Lambda"])
	yield build_report()
	os.remove(rulesfile)


def test_coverage(report):
	scrobbles = report["scrobbles"]
	assert (scrobbles["total"], scrobbles["reparsed"], scrobbles["unchanged"], scrobbles["moved"]) == (14, 12, 8, 4)
	assert scrobbles["no_raw"] == {"total": 1, "by_origin": {"import:lastfm": 1}}
	assert (scrobbles["discarded"]["count"], scrobbles["errors"]["count"]) == (1, 0)


def test_track_changes(report):
	tracks = report["tracks"]
	assert (tracks["merges"]["count"], tracks["splits"]["count"], tracks["renames"]["count"], tracks["new"]) == (1, 1, 2, 3)

	merge, = tracks["merges"]["examples"]
	assert merge["into"] == {"artists": ["Gamma"], "title": "Song"} and merge["existing"]
	assert merge["from"] == [{"track": {"artists": ["Gamma"], "title": "Song (2009 Remaster)"}, "scrobbles": 1}]

	assert tracks["reordered"]["examples"] == [{"track": {"artists": ["Omega", "Psi"], "title": "Order"}, "credited": ["Psi", "Omega"], "scrobbles": 1}]

	split, = tracks["splits"]["examples"]
	assert split["track"]["title"] == "Track X"
	assert [i["track"]["title"] for i in split["into"]] == ["Track X", "Track X (feat. Epsilon)"]

	assert {(r["track"]["title"], tuple(r["to"]["artists"]), tuple(r["changed"])) for r in tracks["renames"]["examples"]} == {
		("Pair", ("Mu", "Nu"), ("artists",)),
		("Tune", ("Zeta",), ("artists",)),
	}


def test_artist_changes(report):
	artists = report["artists"]
	assert {(tuple(c["from"]), tuple(c["to"]), c["kind"]) for c in artists["changes"]["examples"]} == {
		(("Mu Nu",), ("Mu", "Nu"), "split"),
		(("Zeta Old",), ("Zeta",), "rename"),
		((), ("Epsilon",), "added"),
	}
	assert {v["artist"] for v in artists["vanishing"]["examples"]} == {"Mu Nu", "Zeta Old"}
	assert {v["artist"] for v in artists["new"]["examples"]} == {"Mu", "Nu", "Epsilon", "Zeta"}


def test_report_writes_nothing(report):
	def snapshot():
		with sqldb.engine.connect() as conn:
			return {name: conn.execute(sql.select(table)).all() for name, table in sqldb.DB.items()}

	before = snapshot()
	build_report()
	assert snapshot() == before
