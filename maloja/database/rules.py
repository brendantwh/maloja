# parser rules live in the database
# - your own rules are rows in the rules table
# - predefined sets stay in their shipped files and are only switched on (rulesets table),
#   single rules of a set can be switched off (ruleset_overrides table)
# - .tsv files dropped into the rules folder are imported once and renamed to .tsv.imported,
#   symlinks into the predefined folder (the old way to activate a set) switch that set on

import json
import os
import time
from threading import RLock

import sqlalchemy as sql
from doreah.logging import log

from ..cleanup import RULE_COLUMNS, SCOPABLE, SCOPE_PREFIX, Rule, parse_rules_tsv
from ..pkg_global.conf import data_dir
from . import sqldb
from .sqldb import DB


class RuleError(ValueError):
	pass


# for the web interface: what each rule does, and what its fields mean (␟ separates several artists)
RULE_INFO = {
	"replaceartist": ("Replace an artist name (can become several artists)", ["Artist as sent", "Correct artist(s)"]),
	"replacetitle": ("Replace a track title (ignores case)", ["Title as sent", "Correct title"]),
	"replacealbumtitle": ("Replace an album title (ignores case)", ["Album as sent", "Correct album"]),
	"belongtogether": ("Keep an artist name with & or / in it whole (exact spelling)", ["Artist name"]),
	"notanartist": ("Text after 'feat.' that isn't an artist (exact spelling)", ["Text"]),
	"ignoreartist": ("Drop this artist from scrobbles", ["Artist name"]),
	"countas": ("Count an artist towards another one in charts (stored data stays the same)", ["Artist", "Counts as"]),
	"artistintitle": ("Text in a title that means another artist is on the track", ["Text in title", "Artist(s)"]),
	"fixartists": ("For this title, if any of these artists is there, use exactly these artists", ["Artists", "Title"]),
	"addartists": ("For this title, if these artists are all there, add more artists", ["Artists that must be there", "Title", "Artists to add"]),
}
LIST_FIELDS = {("replaceartist", 1), ("artistintitle", 1), ("fixartists", 0), ("addartists", 0), ("addartists", 2)}


def rule_key(rule):
	return json.dumps([rule.type, list(rule.fields), rule.scope or ''], ensure_ascii=False)


def make_rule(ruletype, fields, scope=None):
	if ruletype not in RULE_COLUMNS:
		raise RuleError(f"Unknown rule type {ruletype!r}")
	fields = tuple((f or '').strip() for f in fields)
	if len(fields) != RULE_COLUMNS[ruletype] or not all(fields):
		raise RuleError(f"{ruletype} needs {RULE_COLUMNS[ruletype]} non-empty fields")
	scope = (scope or '').strip() or None
	if any(c in value for value in (*fields, scope or '') for c in "\t\n\r"):
		raise RuleError("Rules can't contain tabs or line breaks")
	if scope and ruletype not in SCOPABLE:
		raise RuleError(f"{ruletype} can't be limited to an artist")
	return Rule(ruletype, fields, scope)


def _row_to_rule(row):
	return Rule(row.type, tuple(json.loads(row.fields)), row.scope or None, row.id, row.origin)


# predefined sets

VALID_SET_CHARS = set("-_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")

def predefined_folder(*path):
	return data_dir['rules']("predefined", *path)


def valid_set_name(name):
	return bool(name) and "_" in name and all(c in VALID_SET_CHARS for c in name)


def read_ruleset(name):
	try:
		with open(predefined_folder(name + ".tsv")) as f:
			text = f.read()
	except (FileNotFoundError, NotADirectoryError):
		return []
	return parse_rules_tsv(text, source=f"set:{name}")[0]


def list_ruleset_files():
	sets = []
	try:
		files = sorted(os.listdir(predefined_folder()))
	except FileNotFoundError:
		return sets
	for f in files:
		name = f[:-len(".tsv")]
		if not f.endswith(".tsv") or not valid_set_name(name):
			continue
		try:
			with open(predefined_folder(f)) as tsvfile:
				line1, line2 = tsvfile.readline().strip(), tsvfile.readline().strip()
		except OSError:
			continue
		sets.append({
			"file": name,
			"name": line1.replace("# NAME: ", "") if "# NAME: " in line1 else name.split("_")[1],
			"author": name.split("_")[0],
			"desc": line2.replace("# DESC: ", "") if "# DESC: " in line2 else "",
		})
	return sets


# files waiting in the rules folder

def pending_files():
	"""Rule files in the rules folder that haven't been imported yet, as (kind, name, path)."""
	folder = data_dir['rules']()
	try:
		files = sorted(os.listdir(folder))
	except FileNotFoundError:
		return []
	predefined = os.path.realpath(predefined_folder())
	pending = []
	for f in files:
		path = os.path.join(folder, f)
		if f.split('.')[-1].lower() != 'tsv' or not os.path.isfile(path):
			continue
		real = os.path.realpath(path)
		if os.path.islink(path) and os.path.dirname(real) == predefined:
			pending.append(('set', os.path.basename(real)[:-len(".tsv")], path))
		else:
			pending.append(('file', f, path))
	return pending


def _read_file(path, name):
	with open(path) as f:
		rules, errors = parse_rules_tsv(f.read(), source=f"file:{name}")
	for e in errors:
		log(f"Rule file {name}, {e}", color='red')
	return rules


@sqldb.connection_provider
def import_rule_files(dbconn=None):
	"""Import waiting rule files into the database and move them out of the way."""
	imported = 0
	for kind, name, path in pending_files():
		if kind == 'set':
			set_ruleset_enabled(name, True, dbconn=dbconn)
			log(f"Predefined rule set {name} is now switched on in the database")
			done = lambda: os.remove(path)
		else:
			for rule in _read_file(path, name):
				imported += bool(add_rule(rule.type, rule.fields, rule.scope, origin=f"file:{name}", dbconn=dbconn))
			log(f"Imported rule file {name}")
			target = path + ".imported"
			if os.path.exists(target):
				target = f"{path}.{int(time.time())}.imported"
			done = lambda: os.rename(path, target)
		try:
			done()
		except OSError as e:
			# e.g. a read-only config folder: the rules are in the database anyway, and importing
			# them again is a no-op, but a rule deleted in the web interface comes back on restart
			log(f"Could not move {path} out of the rules folder ({e}), it will be read again on the next start", color='red')
	return imported


# loading

@sqldb.connection_provider
def load_rules(import_files=False, dbconn=None):
	"""All active rules, in the order they apply (later ones win: your rules over predefined sets).
	Without import_files, waiting rule files are only read, nothing is written or moved."""
	if import_files:
		import_rule_files(dbconn=dbconn)
	pending = [] if import_files else pending_files()

	enabled_sets = {row.name for row in dbconn.execute(sql.select(DB['rulesets']).where(DB['rulesets'].c.enabled))}
	enabled_sets |= {name for kind, name, _ in pending if kind == 'set'}
	overrides = {(row.ruleset, row.rule_key) for row in dbconn.execute(sql.select(DB['ruleset_overrides']))}

	rules = []
	for name in sorted(enabled_sets):
		rules += [r for r in read_ruleset(name) if (name, rule_key(r)) not in overrides]
	rows = dbconn.execute(sql.select(DB['rules']).where(DB['rules'].c.enabled).order_by(DB['rules'].c.id))
	rules += [_row_to_rule(row) for row in rows]
	for kind, name, path in pending:
		if kind == 'file':
			rules += _read_file(path, name)
	return rules


_reload_lock = RLock()

def reload_rules():
	"""Load the rules again and apply them right away, no restart needed."""
	from . import associated, dbcache
	from . import cla
	with _reload_lock:
		loaded = load_rules(import_files=True)
		cla.set_rules(loaded)
		# countas lives in its own table, and its names are resolved now (artists created later need another reload)
		associated.load_associated_rules(loaded)
		dbcache.invalidate_entity_cache()
		dbcache.invalidate_caches()
	return loaded


# changes

@sqldb.connection_provider
def add_rule(ruletype, fields, scope=None, note=None, origin='user', enabled=True, dbconn=None):
	"""Returns the new rule's id, or None if the same rule already exists."""
	rule = make_rule(ruletype, fields, scope)
	op = DB['rules'].insert().values(
		type=rule.type, fields=json.dumps(list(rule.fields), ensure_ascii=False), scope=rule.scope or '',
		enabled=enabled, origin=origin, note=note or '', created_at=int(time.time())
	).prefix_with('OR IGNORE')
	result = dbconn.execute(op)
	return result.inserted_primary_key[0] if result.rowcount else None


def _get_row(rule_id, dbconn):
	row = dbconn.execute(sql.select(DB['rules']).where(DB['rules'].c.id == int(rule_id))).first()
	if row is None:
		raise RuleError(f"There is no rule {rule_id}")
	return row


@sqldb.connection_provider
def edit_rule(rule_id, ruletype, fields, scope=None, note=None, dbconn=None):
	_get_row(rule_id, dbconn)
	rule = make_rule(ruletype, fields, scope)
	values = {'type': rule.type, 'fields': json.dumps(list(rule.fields), ensure_ascii=False), 'scope': rule.scope or ''}
	if note is not None:
		values['note'] = note
	try:
		dbconn.execute(DB['rules'].update().where(DB['rules'].c.id == int(rule_id)).values(**values))
	except sql.exc.IntegrityError:
		raise RuleError("The same rule already exists")


@sqldb.connection_provider
def delete_rule(rule_id, dbconn=None):
	_get_row(rule_id, dbconn)
	dbconn.execute(DB['rules'].delete().where(DB['rules'].c.id == int(rule_id)))


@sqldb.connection_provider
def set_rule_enabled(rule_id, enabled, dbconn=None):
	_get_row(rule_id, dbconn)
	dbconn.execute(DB['rules'].update().where(DB['rules'].c.id == int(rule_id)).values(enabled=bool(enabled)))


@sqldb.connection_provider
def list_rules(dbconn=None):
	"""Your rules, including switched off ones."""
	rows = dbconn.execute(sql.select(DB['rules']).order_by(DB['rules'].c.id))
	return [
		{**rule_to_dict(_row_to_rule(row)), 'enabled': bool(row.enabled), 'note': row.note or '', 'created_at': row.created_at}
		for row in rows
	]


def rule_to_dict(rule):
	return {'id': rule.id, 'type': rule.type, 'fields': list(rule.fields), 'scope': rule.scope, 'source': rule.source, 'key': rule_key(rule)}


# predefined sets

@sqldb.connection_provider
def get_ruleset_states(dbconn=None):
	return {row.name: bool(row.enabled) for row in dbconn.execute(sql.select(DB['rulesets']))}


@sqldb.connection_provider
def set_ruleset_enabled(name, enabled, dbconn=None):
	if not valid_set_name(name):
		raise RuleError(f"Invalid rule set name {name!r}")
	op = sqldb.sqliteinsert(DB['rulesets']).values(name=name, enabled=enabled)
	dbconn.execute(op.on_conflict_do_update(index_elements=['name'], set_={'enabled': enabled}))


def _set_rule(name, key):
	for rule in read_ruleset(name):
		if rule_key(rule) == key:
			return rule
	raise RuleError(f"Rule set {name} has no such rule")


@sqldb.connection_provider
def set_ruleset_rule_enabled(name, key, enabled, dbconn=None):
	_set_rule(name, key)
	table = DB['ruleset_overrides']
	if enabled:
		dbconn.execute(table.delete().where(table.c.ruleset == name, table.c.rule_key == key))
	else:
		dbconn.execute(table.insert().values(ruleset=name, rule_key=key).prefix_with('OR IGNORE'))


@sqldb.connection_provider
def copy_ruleset_rule(name, key, dbconn=None):
	"""Copy a rule of a set into your rules, where you can change it (yours wins on the same match)."""
	rule = _set_rule(name, key)
	return add_rule(rule.type, rule.fields, rule.scope, note=f"copied from {name}", dbconn=dbconn)


@sqldb.connection_provider
def list_rulesets(dbconn=None):
	"""The predefined sets, whether they're on, and their rules with the ones you switched off."""
	states = get_ruleset_states(dbconn=dbconn)
	pending = {name for kind, name, _ in pending_files() if kind == 'set'}
	overrides = {(row.ruleset, row.rule_key) for row in dbconn.execute(sql.select(DB['ruleset_overrides']))}
	return [
		{
			**ruleset,
			'active': states.get(ruleset['file'], False) or ruleset['file'] in pending,
			'rules': [
				{**rule_to_dict(rule), 'enabled': (ruleset['file'], rule_key(rule)) not in overrides}
				for rule in read_ruleset(ruleset['file'])
			],
		}
		for ruleset in list_ruleset_files()
	]


# maloja export / import

@sqldb.connection_provider
def export_data(dbconn=None):
	return {
		'rules': [{k: r[k] for k in ('type', 'fields', 'scope', 'enabled', 'note')} for r in list_rules(dbconn=dbconn)],
		'rulesets': get_ruleset_states(dbconn=dbconn),
		'overrides': [[row.ruleset, row.rule_key] for row in dbconn.execute(sql.select(DB['ruleset_overrides']))],
	}


@sqldb.connection_provider
def import_data(data, dbconn=None):
	added, errors = 0, []
	for r in data.get('rules', []):
		try:
			added += bool(add_rule(r['type'], r['fields'], r.get('scope'), note=r.get('note'), origin='import', enabled=r.get('enabled', True), dbconn=dbconn))
		except (RuleError, KeyError, TypeError) as e:
			errors.append(f"{r!r}: {e}")
	for name, enabled in data.get('rulesets', {}).items():
		if valid_set_name(name):
			set_ruleset_enabled(name, enabled, dbconn=dbconn)
	for name, key in data.get('overrides', []):
		dbconn.execute(DB['ruleset_overrides'].insert().values(ruleset=name, rule_key=key).prefix_with('OR IGNORE'))
	return {'added': added, 'errors': errors}


# rule files

def rule_to_tsv(rule):
	columns = [rule.type, *rule.fields] + ([SCOPE_PREFIX + rule.scope] if rule.scope else [])
	return "\t".join(columns)


@sqldb.connection_provider
def export_tsv(dbconn=None):
	lines = ["# Maloja parser rules, exported " + time.strftime("%Y-%m-%d %H:%M"), "# see rules.info for the format", ""]
	for rule in list_rules(dbconn=dbconn):
		line = rule_to_tsv(Rule(rule['type'], tuple(rule['fields']), rule['scope']))
		lines.append(line if rule['enabled'] else "# (switched off) " + line)
	return "\n".join(lines) + "\n"


@sqldb.connection_provider
def import_tsv(text, origin='import', dbconn=None):
	rules, errors = parse_rules_tsv(text)
	added, existing = 0, 0
	for rule in rules:
		try:
			if add_rule(rule.type, rule.fields, rule.scope, origin=origin, dbconn=dbconn):
				added += 1
			else:
				existing += 1
		except RuleError as e:
			errors.append(f"{rule_to_tsv(rule)!r}: {e}")
	return {'added': added, 'existing': existing, 'errors': errors}
