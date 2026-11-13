import os
import shutil

import pytest
import sqlalchemy as sql

from maloja.database import rules, sqldb
from maloja.pkg_global.conf import data_dir


SET_RULES = "# NAME: Test Set\n# DESC: for tests\nreplacetitle\tRED\tRed (set)\nreplaceartist\tSet Old\tSet New\n"


@pytest.fixture
def folder(db):
	# the rules folder is shared by all tests, so it's emptied before and after
	def clear():
		shutil.rmtree(data_dir["rules"](), ignore_errors=True)
		os.makedirs(data_dir["rules"]("predefined"))

	clear()
	with open(data_dir["rules"]("predefined", "test_set.tsv"), "w") as f:
		f.write(SET_RULES)

	def write(name, text):
		with open(data_dir["rules"](name), "w") as f:
			f.write(text)

	yield write
	clear()


def fields(loaded):
	return [(r.type, r.fields) for r in loaded]


def test_rule_file_is_imported_once(folder):
	folder("mine.tsv", "replacetitle\tRED\tRed\n")
	assert fields(rules.load_rules(import_files=True)) == [("replacetitle", ("RED", "Red"))]
	assert sorted(os.listdir(data_dir["rules"]())) == ["mine.tsv.imported", "predefined"]
	# the same file dropped in again doesn't double the rule
	folder("mine.tsv", "replacetitle\tRED\tRed\n")
	assert fields(rules.load_rules(import_files=True)) == [("replacetitle", ("RED", "Red"))]


def test_bad_line_in_rule_file_is_skipped(folder):
	# a column of only spaces used to be accepted, now it fails validation
	folder("mine.tsv", "replacetitle\tFoo\t \tBar\nreplacetitle\tRED\tRed\n")
	assert fields(rules.load_rules(import_files=True)) == [("replacetitle", ("RED", "Red"))]
	assert sorted(os.listdir(data_dir["rules"]())) == ["mine.tsv.imported", "predefined"]


def test_failed_import_leaves_files_in_place(folder):
	folder("a.tsv", "replacetitle\tRED\tRed\n")
	with open(data_dir["rules"]("b.tsv"), "wb") as f:
		f.write(b"replacetitle\t\xff\tRed\n")
	with pytest.raises(UnicodeDecodeError):
		rules.load_rules(import_files=True)
	# nothing was committed, so the files must still be there to be read again
	assert sorted(os.listdir(data_dir["rules"]())) == ["a.tsv", "b.tsv", "predefined"]
	with sqldb.engine.connect() as conn:
		assert conn.execute(sql.select(sqldb.DB["rules"])).all() == []


def test_symlink_switches_set_on(folder):
	os.symlink(data_dir["rules"]("predefined", "test_set.tsv"), data_dir["rules"]("test_set.tsv"))
	loaded = rules.load_rules(import_files=True)
	assert [r.source for r in loaded] == ["set:test_set", "set:test_set"]
	assert rules.get_ruleset_states() == {"test_set": True}
	assert not os.path.lexists(data_dir["rules"]("test_set.tsv"))


def test_lazy_load_changes_nothing(folder):
	folder("mine.tsv", "replacetitle\tRED\tRed\n")
	os.symlink(data_dir["rules"]("predefined", "test_set.tsv"), data_dir["rules"]("test_set.tsv"))
	assert len(rules.load_rules()) == 3
	assert sorted(os.listdir(data_dir["rules"]())) == ["mine.tsv", "predefined", "test_set.tsv"]
	assert rules.get_ruleset_states() == {}
	with sqldb.engine.connect() as conn:
		assert conn.execute(sql.select(sqldb.DB["rules"])).all() == []


def test_reload_applies_without_restart(folder, db):
	raw = {"track_artists": ["A"], "track_title": "RED", "scrobble_time": 1}
	assert db.rawscrobble_to_scrobbledict(raw)["track"]["title"] == "RED"
	rules.add_rule("replacetitle", ["RED", "Red"])
	rules.reload_rules()
	assert db.rawscrobble_to_scrobbledict(raw)["track"]["title"] == "Red"


def test_reload_updates_countas(folder, db):
	sqldb.add_scrobble({"time": 1, "track": {"artists": ["Member"], "title": "Song"}, "origin": "client:test", "rawscrobble": None})
	sqldb.add_scrobble({"time": 2, "track": {"artists": ["Group"], "title": "Hit"}, "origin": "client:test", "rawscrobble": None})
	rules.add_rule("countas", ["Member", "Group"])
	rules.reload_rules()
	assert sqldb.get_associated_artists("Group") == ["Member"]


def test_own_rule_wins_over_set(folder, db):
	rules.set_ruleset_enabled("test_set", True)
	rules.add_rule("replacetitle", ["red", "Red (mine)"])
	db.cla.set_rules(rules.load_rules())
	assert db.cla.fullclean("A", "RED", None)[1] == "Red (mine)"


def test_set_rule_switched_off_and_copied(folder):
	rules.set_ruleset_enabled("test_set", True)
	(ruleset,) = rules.list_rulesets()
	red = next(r for r in ruleset["rules"] if r["type"] == "replacetitle")
	rules.set_ruleset_rule_enabled("test_set", red["key"], False)
	assert fields(rules.load_rules()) == [("replaceartist", ("Set Old", "Set New"))]
	assert [r["enabled"] for r in rules.list_rulesets()[0]["rules"]] == [False, True]
	rules.copy_ruleset_rule("test_set", red["key"])
	assert fields(rules.load_rules()) == [("replaceartist", ("Set Old", "Set New")), ("replacetitle", ("RED", "Red (set)"))]
	rules.set_ruleset_rule_enabled("test_set", red["key"], True)
	assert len(rules.load_rules()) == 3


def test_tsv_round_trip(folder):
	rules.add_rule("replacetitle", ["RED", "Red"], scope="HyunA")
	off = rules.add_rule("belongtogether", ["AC/DC"])
	rules.set_rule_enabled(off, False)
	text = rules.export_tsv()
	for rule in rules.list_rules():
		rules.delete_rule(rule["id"])
	assert rules.import_tsv(text + "replacetitle\tbroken\n") == {"added": 1, "existing": 0, "errors": ["line 6: replacetitle needs 2 columns, got 1"]}
	assert [(r.type, r.fields, r.scope) for r in rules.load_rules()] == [("replacetitle", ("RED", "Red"), "HyunA")]
	assert rules.import_tsv(text)["existing"] == 1


def test_invalid_rules_are_refused(folder):
	with pytest.raises(rules.RuleError):
		rules.add_rule("replaceartist", ["A", "B"], scope="C")
	with pytest.raises(rules.RuleError):
		rules.add_rule("replacetitle", ["A\tB", "C"])
	first = rules.add_rule("replacetitle", ["A", "B"])
	second = rules.add_rule("replacetitle", ["C", "D"])
	with pytest.raises(rules.RuleError):
		rules.edit_rule(second, "replacetitle", ["A", "B"])
	assert rules.add_rule("replacetitle", ["A", "B"]) is None and first
