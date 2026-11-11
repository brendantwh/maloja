# likely duplicate artists and tracks in your own database, for you to approve or dismiss
# - nothing here changes anything: approving a suggestion is a normal merge (which records aliases)
# - names are compared more loosely than the lookups do (normalize_name): see loose_key
# - no external services are asked

import json
import re
import time
from collections import Counter, defaultdict

import sqlalchemy as sql

from ..cleanup import CleanerAgent
from ..pkg_global.conf import malojaconfig
from . import sqldb
from .sqldb import DB, normalize_name

class DuplicateError(ValueError):
	pass


LETTER = r"[^\W\d_]"
LENGTH_TOLERANCE = 5  # seconds


def loose_key(name):
	n = normalize_name(name)
	n = re.sub(r"\s*[&+]\s*", " and ", n)
	n = re.sub(r"^the\s+", "", n)
	# Ke$ha, A$AP, P!nk: symbols standing in for letters
	n = re.sub(rf"(?<={LETTER})\$(?={LETTER})", "s", n)
	n = re.sub(rf"(?<={LETTER})!(?={LETTER})", "i", n)
	key = re.sub(r"[\W_]+", "", n)
	return key or normalize_name(name)


def _feat_patterns():
	delimiters = [re.escape(d) for d in [*malojaconfig["DELIMITERS_FEAT"], *malojaconfig["DELIMITERS_FEAT_TITLE"]]]
	alternatives = "|".join(sorted(delimiters, key=len, reverse=True))
	return [
		re.compile(rf" [\(\[](?:{alternatives}) [^\)\]]*[\)\]]", re.IGNORECASE),
		re.compile(rf" - (?:{alternatives}) .*$", re.IGNORECASE),
	]


_variants = CleanerAgent(rules=[])
VARIANT_PATTERNS = [p for _, p in CleanerAgent.title_variants]

def loose_title_key(title, feat_patterns=None):
	# featured artists are on the track anyway, and remaster, edit and mono/stereo tags are the same
	# recording; live versions and remixes are different recordings and stay apart
	for pattern in feat_patterns or _feat_patterns():
		title = pattern.sub("", title)
	title = _variants.stripVariants(title.replace("[", "(").replace("]", ")"), patterns=VARIANT_PATTERNS)
	return loose_key(title)


def _dismissed(entity_type, dbconn):
	table = DB['dismissed_duplicates']
	rows = dbconn.execute(sql.select(table.c.member_ids).where(table.c.entity_type == entity_type))
	return [set(json.loads(r.member_ids)) for r in rows]


def _origins(column, ids, dbconn):
	# scrobbles per entity and origin, so you can see which player sends which spelling
	scrobbles = DB['scrobbles']
	if column == 'artist':
		ta = DB['trackartists']
		op = sql.select(ta.c.artist_id, scrobbles.c.origin, sql.func.count()).select_from(
			scrobbles.join(ta, ta.c.track_id == scrobbles.c.track_id)
		).where(ta.c.artist_id.in_(ids)).group_by(ta.c.artist_id, scrobbles.c.origin)
	else:
		op = sql.select(scrobbles.c.track_id, scrobbles.c.origin, sql.func.count()).where(
			scrobbles.c.track_id.in_(ids)
		).group_by(scrobbles.c.track_id, scrobbles.c.origin)
	result = defaultdict(Counter)
	for entity_id, origin, n in dbconn.execute(op):
		result[entity_id][origin or 'unknown'] += n
	return result


def _suggestions(entity_type, groups, describe, dbconn):
	dismissed = _dismissed(entity_type, dbconn)
	groups = [g for g in groups if len(g) > 1 and not any(set(g) <= d for d in dismissed)]
	origins = _origins(entity_type, [i for g in groups for i in g], dbconn)
	suggestions = []
	for group in groups:
		members = [
			{**describe(i), 'id': i, 'scrobbles': sum(origins[i].values()), 'origins': dict(origins[i].most_common())}
			for i in group
		]
		members.sort(key=lambda m: (-m['scrobbles'], m['id']))
		suggestions.append({'entity_type': entity_type, 'ids': sorted(group), 'members': members, 'scrobbles': sum(m['scrobbles'] for m in members)})
	suggestions.sort(key=lambda s: (-s['scrobbles'], s['ids']))
	return suggestions


@sqldb.connection_provider
def find_duplicates(dbconn=None):
	artists = {r.id: r.name for r in dbconn.execute(sql.select(DB['artists'].c.id, DB['artists'].c.name))}
	by_key = defaultdict(list)
	for artist_id, name in sorted(artists.items()):
		by_key[loose_key(name)].append(artist_id)
	artist_suggestions = _suggestions('artist', by_key.values(), lambda i: {'name': artists[i]}, dbconn)

	trackartists = defaultdict(list)
	for r in dbconn.execute(sql.select(DB['trackartists']).order_by(DB['trackartists'].c.id)):
		trackartists[r.track_id].append(r.artist_id)
	tracks = {r.id: r for r in dbconn.execute(sql.select(DB['tracks'].c.id, DB['tracks'].c.title, DB['tracks'].c.length))}
	feat = _feat_patterns()
	by_key = defaultdict(list)
	for track_id, row in sorted(tracks.items()):
		by_key[(frozenset(trackartists[track_id]), loose_title_key(row.title, feat))].append(track_id)
	groups = []
	for group in by_key.values():
		# a known length that is clearly different means a different recording
		lengths = [tracks[t].length for t in group if tracks[t].length]
		if lengths:
			reference = Counter(lengths).most_common(1)[0][0]
			group = [t for t in group if not tracks[t].length or abs(tracks[t].length - reference) <= LENGTH_TOLERANCE]
		groups.append(group)
	describe = lambda t: {'title': tracks[t].title, 'artists': [artists[a] for a in trackartists[t]], 'length': tracks[t].length}
	track_suggestions = _suggestions('track', groups, describe, dbconn)

	return {'artists': artist_suggestions, 'tracks': track_suggestions}


@sqldb.connection_provider
def dismiss_duplicate(entity_type, ids, dbconn=None):
	if entity_type not in ('artist', 'track'):
		raise DuplicateError(f"Can't dismiss {entity_type} suggestions")
	ids = sorted({int(i) for i in ids})
	dbconn.execute(DB['dismissed_duplicates'].insert().values(
		entity_type=entity_type, member_ids=json.dumps(ids), created_at=int(time.time())
	).prefix_with('OR IGNORE'))
