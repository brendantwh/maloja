# parser rules live in the database
# - your own rules are rows in the rules table
# - predefined sets stay in their shipped files and are only switched on (rulesets table),
#   single rules of a set can be switched off (ruleset_overrides table)
# - .tsv files dropped into the rules folder are imported once and renamed to .tsv.imported,
#   symlinks into the predefined folder (the old way to activate a set) switch that set on

import json
import os
import time

import sqlalchemy as sql
from doreah.logging import log

from ..cleanup import RULE_COLUMNS, Rule, parse_rules_tsv
from ..pkg_global.conf import data_dir
from . import sqldb
from .sqldb import DB


class RuleError(ValueError):
	pass


def rule_key(rule):
	return json.dumps([rule.type, list(rule.fields), rule.scope or ''], ensure_ascii=False)


def make_rule(ruletype, fields, scope=None):
	if ruletype not in RULE_COLUMNS:
		raise RuleError(f"Unknown rule type {ruletype!r}")
	fields = tuple((f or '').strip() for f in fields)
	if len(fields) != RULE_COLUMNS[ruletype] or not all(fields):
		raise RuleError(f"{ruletype} needs {RULE_COLUMNS[ruletype]} non-empty fields")
	return Rule(ruletype, fields, (scope or '').strip() or None)


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


@sqldb.connection_provider
def get_ruleset_states(dbconn=None):
	return {row.name: bool(row.enabled) for row in dbconn.execute(sql.select(DB['rulesets']))}


@sqldb.connection_provider
def set_ruleset_enabled(name, enabled, dbconn=None):
	if not valid_set_name(name):
		raise RuleError(f"Invalid rule set name {name!r}")
	op = sqldb.sqliteinsert(DB['rulesets']).values(name=name, enabled=enabled)
	dbconn.execute(op.on_conflict_do_update(index_elements=['name'], set_={'enabled': enabled}))
