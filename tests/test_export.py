import json

from conftest import _empty_db
from maloja.database import rules, sqldb
from maloja.proccontrol.tasks import export, import_scrobbles


def test_export_and_import_keep_rules_and_aliases(db, monkeypatch, tmp_path, capsys):
	sqldb.add_scrobble({"time": 1, "track": {"artists": ["Beyonce Knowles"], "title": "Halo"}, "origin": "client:test", "rawscrobble": None})
	db.edit_artist(sqldb.get_artist_id("Beyonce Knowles"), "Beyoncé")
	rules.add_rule("replacetitle", ["RED", "Red"], scope="HyunA", note="mine")
	rules.set_ruleset_enabled("krateng_kpop", True)

	exported = export(str(tmp_path))
	with open(exported) as f:
		data = json.load(f)
	data["aliases"].append({"entity_type": "artist", "name": "nobody old", "artists": [], "source": "rename", "created_at": 1, "target": "Nobody"})
	with open(exported, "w") as f:
		json.dump(data, f)

	_empty_db(monkeypatch)
	import_scrobbles(exported)
	assert "Imported 1 aliases, skipped 1 whose target isn't in this database" in capsys.readouterr().out
	assert [(r["type"], r["fields"], r["scope"], r["note"]) for r in rules.list_rules()] == [("replacetitle", ["RED", "Red"], "HyunA", "mine")]
	assert rules.get_ruleset_states() == {"krateng_kpop": True}
	# the old spelling still finds the renamed artist in the new database
	assert sqldb.get_artist_id("Beyonce Knowles", create_new=False) == sqldb.get_artist_id("Beyoncé", create_new=False)
