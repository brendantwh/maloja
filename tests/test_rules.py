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


def test_own_rule_wins_over_set(folder, db):
	rules.set_ruleset_enabled("test_set", True)
	rules.add_rule("replacetitle", ["red", "Red (mine)"])
	db.cla.set_rules(rules.load_rules())
	assert db.cla.fullclean("A", "RED", None)[1] == "Red (mine)"
