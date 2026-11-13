import os
import sqlite3
import threading

import pytest

from maloja.database import reparse, rules, sqldb
from maloja.database.reparse import apply_reparse
from maloja.database.reparse_report import build_report


# (stored artists, stored title, raw artists, raw title)
CASES = [
	(["Gamma"], "Song", ["Gamma"], "Song"),
	(["Gamma"], "Song (2009 Remaster)", ["Gamma"], "Song (2009 Remaster)"),
	(["Delta"], "Track X", ["Delta"], "Track X"),
	(["Delta"], "Track X", ["Delta"], "Track X (feat. Epsilon)"),
	(["Omega", "Psi"], "Order", ["Psi & Omega"], "Order"),
	(["Zeta Old"], "Tune", ["Zeta Old"], "Tune"),
	(["Eta"], "Imported", None, None),
]


def track_id(artists, title):
	return sqldb.get_track_id({"artists": artists, "title": title}, create_new=False)


@pytest.fixture
def seeded(db):
	sqldb.add_scrobbles([
		{
			"time": 1000 + i,
			"track": {"artists": artists, "title": title},
			"origin": "client:test",
			"rawscrobble": {"track_artists": raw_artists, "track_title": raw_title, "scrobble_time": 1000 + i} if raw_artists else None,
		}
		for i, (artists, title, raw_artists, raw_title) in enumerate(CASES)
	])
	rules.add_rule("replaceartist", ["Zeta Old", "Zeta"])
	db.cla.set_rules(rules.load_rules())
	remaster = track_id(["Gamma"], "Song (2009 Remaster)")
	sqldb.add_alias("track", "Song (Remastered)", remaster, artists=["Gamma"])
	return db


def test_apply_does_what_the_report_shows(seeded):
	zeta = track_id(["Zeta Old"], "Tune")
	before = build_report()
	assert (before["scrobbles"]["moved"], before["tracks"]["reordered"]["count"]) == (3, 1)

	result = apply_reparse(skip_track_ids=[zeta], reorder=True)
	os.remove(result["snapshot"])
	assert {k: result[k] for k in ("total", "moved", "unchanged", "reordered", "skipped", "no_raw")} == {
		"total": 7, "moved": 2, "unchanged": 3, "reordered": 1, "skipped": 1, "no_raw": 1,
	}

	after = build_report()
	# only the skipped track would still move
	assert (after["scrobbles"]["moved"], after["tracks"]["reordered"]["count"]) == (1, 0)
	assert after["tracks"]["renames"]["examples"][0]["track_ids"] == [zeta]
	assert sqldb.get_scrobble(timestamp=1001)["track"] == sqldb.get_scrobble(timestamp=1000)["track"]
	assert sqldb.get_scrobble(timestamp=1003)["track"]["artists"] == ["Delta", "Epsilon"]
	assert sqldb.get_scrobble(timestamp=1004)["track"]["artists"] == ["Psi", "Omega"]
	# the emptied remaster track is gone, and its alias moved to the track it merged into
	assert track_id(["Gamma"], "Song (2009 Remaster)") is None
	assert sqldb.get_track_id({"artists": ["Gamma"], "title": "Song (Remastered)"}, create_new=False) == track_id(["Gamma"], "Song")


def test_apply_takes_a_snapshot_first(seeded):
	result = apply_reparse()
	with sqlite3.connect(result["snapshot"]) as snapshot:
		titles = {row[0] for row in snapshot.execute("SELECT title FROM tracks")}
	os.remove(result["snapshot"])
	assert "Song (2009 Remaster)" in titles


def test_background_job(seeded):
	reparse.start_preview()
	reparse.wait_for_job(5)
	status = reparse.job_status(include_report=True)
	assert status["state"] == "ready" and status["report"]["scrobbles"]["moved"] == 3
	assert "report" not in reparse.job_status()

	zeta = track_id(["Zeta Old"], "Tune")
	reparse.start_apply(skip_track_ids=[zeta])
	reparse.wait_for_job(5)
	status = reparse.job_status()
	os.remove(status["result"]["snapshot"])
	assert (status["state"], status["result"]["moved"], status["result"]["skipped"], status["skip_track_ids"]) == ("done", 2, 1, [zeta])
	assert status["progress"] == [7, 7]


def test_only_one_job_at_a_time(seeded):
	release = threading.Event()
	reparse._start("applying", lambda: release.wait(5) and {"state": "done"})
	with pytest.raises(reparse.JobRunning):
		reparse.start_preview()
	release.set()
	reparse.wait_for_job(5)
	assert reparse.job_status()["state"] == "done"


def test_apply_needs_a_preview(seeded):
	reparse._start("applying", lambda: {"state": "done"})
	reparse.wait_for_job(5)
	with pytest.raises(reparse.PreviewOutdated):
		reparse.start_apply()


def test_apply_refuses_outdated_preview_after_rule_change(seeded, db):
	reparse.start_preview()
	reparse.wait_for_job(5)
	rules.add_rule("replacetitle", ["Tune", "Tune!"])
	db.cla.set_rules(rules.load_rules())
	with pytest.raises(reparse.PreviewOutdated):
		reparse.start_apply()
	assert reparse.job_status()["state"] == "ready"


def test_apply_refuses_outdated_preview_after_setting_change(seeded, settings):
	reparse.start_preview()
	reparse.wait_for_job(5)
	settings(TITLE_STRIP_FEAT=True)
	with pytest.raises(reparse.PreviewOutdated):
		reparse.start_apply()
