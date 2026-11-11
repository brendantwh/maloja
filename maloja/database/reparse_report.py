# read-only dry run of reparsing every scrobble from its stored rawscrobble
# nothing in here writes to the database, uses the entity caches or takes SCROBBLE_LOCK
# identity is compared the same way get_track_id does it (normalized title + set of
# normalized artist names, with aliases applied), but in memory, so tracks that don't
# exist yet can be grouped too

import json
from collections import Counter, defaultdict

import sqlalchemy as sql

from . import sqldb
from .sqldb import DB, normalize_name
from ..pkg_global.conf import malojaconfig


def _identity(artists, title):
	return (normalize_name(title), frozenset(normalize_name(a) for a in artists))


def _trackname(track):
	return f"{', '.join(track['artists'])} - {track['title']}"


def _top(entries, examples, count=lambda e: e['scrobbles'], name=lambda e: ''):
	return sorted(entries, key=lambda e: (-count(e), name(e)))[:examples]


@sqldb.connection_provider
def build_report(examples=10, dbconn=None):
	from . import rawscrobble_to_scrobbledict

	# current state
	artistnames = {}
	for row in dbconn.execute(sql.select(DB['artists'].c.id, DB['artists'].c.name)):
		artistnames[row.id] = row.name
	known_artists = {}
	for artist_id in sorted(artistnames):
		known_artists.setdefault(normalize_name(artistnames[artist_id]), artistnames[artist_id])

	trackartists = defaultdict(list)
	for row in dbconn.execute(sql.select(DB['trackartists']).order_by(DB['trackartists'].c.id)):
		trackartists[row.track_id].append(artistnames[row.artist_id])

	tracks, track_key, key_to_track = {}, {}, {}
	for row in dbconn.execute(sql.select(DB['tracks'].c.id, DB['tracks'].c.title).order_by(DB['tracks'].c.id)):
		tracks[row.id] = {'artists': trackartists[row.id], 'title': row.title}
		track_key[row.id] = _identity(tracks[row.id]['artists'], row.title)
		key_to_track.setdefault(track_key[row.id], row.id)

	# aliases, applied like the lookups do: a real entity with that name wins over an alias
	artist_alias, track_alias = {}, {}
	alias_rows = dbconn.execute(sql.select(DB['aliases']).order_by(DB['aliases'].c.id)).all() if malojaconfig['LEARN_ALIASES'] else []
	for row in alias_rows:
		if row.entity_type == 'artist' and row.name_normalized not in known_artists and row.target_id in artistnames:
			artist_alias.setdefault(row.name_normalized, normalize_name(artistnames[row.target_id]))

	def resolve_artist(name):
		n = normalize_name(name)
		return n if n in known_artists else artist_alias.get(n, n)

	for row in alias_rows:
		if row.entity_type == 'track' and row.target_id in tracks:
			key = (row.name_normalized, frozenset(resolve_artist(a) for a in json.loads(row.artists_normalized or '[]')))
			track_alias.setdefault(key, track_key[row.target_id])

	def new_identity(artists, title):
		key = (normalize_name(title), frozenset(resolve_artist(a) for a in artists))
		if key not in key_to_track and key in track_alias:
			return track_alias[key]
		return key

	# walk all scrobbles
	total, unchanged, moved = 0, 0, 0
	no_raw = Counter()
	discarded, errors = [], []
	parsed = {}
	key_display = {}
	artist_display = {}
	flows = defaultdict(Counter)    # current track id -> new identity -> scrobbles
	inflows = defaultdict(Counter)  # new identity -> current track id -> scrobbles
	artist_changes = Counter()      # (removed names, added names) -> scrobbles
	reordered = {}                  # track id -> [credited order, scrobbles]
	artists_before, artists_after = Counter(), Counter()

	op = sql.select(
		DB['scrobbles'].c.timestamp, DB['scrobbles'].c.track_id,
		DB['scrobbles'].c.origin, DB['scrobbles'].c.rawscrobble
	).order_by(DB['scrobbles'].c.timestamp)
	for row in dbconn.execute(op):
		total += 1
		old_key = track_key[row.track_id]
		old_artists = old_key[1]
		artists_before.update(old_artists)

		raw = json.loads(row.rawscrobble) if row.rawscrobble else None
		if not raw:
			no_raw[row.origin or 'unknown'] += 1
			artists_after.update(old_artists)
			continue

		memokey = json.dumps({k: v for k, v in raw.items() if k not in ('scrobble_time', 'scrobble_duration')}, sort_keys=True)
		if memokey not in parsed:
			try:
				result = rawscrobble_to_scrobbledict(raw)
				parsed[memokey] = ('ok', result and {'artists': result['track']['artists'], 'title': result['track']['title']})
			except Exception as e:
				parsed[memokey] = ('error', repr(e))
		status, newtrack = parsed[memokey]

		if status == 'error':
			errors.append({'timestamp': row.timestamp, 'error': newtrack, 'raw': raw})
			artists_after.update(old_artists)
			continue
		if newtrack is None:
			# reparse_scrobble leaves these alone, so they keep their current artists
			discarded.append({'timestamp': row.timestamp, 'raw': raw})
			artists_after.update(old_artists)
			continue

		new_key = new_identity(newtrack['artists'], newtrack['title'])
		key_display.setdefault(new_key, newtrack)
		for a in newtrack['artists']:
			artist_display.setdefault(resolve_artist(a), known_artists.get(resolve_artist(a), a))
		flows[row.track_id][new_key] += 1
		inflows[new_key][row.track_id] += 1
		artists_after.update(new_key[1])

		if new_key == old_key:
			unchanged += 1
			# same artists, but maybe credited in another order than the track shows them
			credited = list(dict.fromkeys(resolve_artist(a) for a in newtrack['artists']))
			if set(credited) == old_artists and credited != [normalize_name(a) for a in tracks[row.track_id]['artists']]:
				reordered.setdefault(row.track_id, [[artist_display[n] for n in credited], 0])[1] += 1
			continue
		moved += 1
		old_names = {normalize_name(a): a for a in tracks[row.track_id]['artists']}
		removed = tuple(sorted(old_names[n] for n in old_artists - new_key[1]))
		added = tuple(sorted(artist_display.get(n, n) for n in new_key[1] - old_artists))
		if removed or added:
			artist_changes[(removed, added)] += 1

	def display(key):
		return tracks[key_to_track[key]] if key in key_to_track else key_display[key]

	# tracks
	splits = []
	for track_id, targets in flows.items():
		if len(targets) > 1:
			splits.append({
				'track': tracks[track_id],
				'scrobbles': sum(targets.values()),
				'into': [{'track': display(k), 'scrobbles': n} for k, n in sorted(targets.items(), key=lambda kv: (-kv[1], _trackname(display(kv[0]))))]
			})

	merges, new_tracks = [], 0
	for key, sources in inflows.items():
		target = key_to_track.get(key)
		if target is None:
			new_tracks += 1
		movers = [t for t in sources if track_key[t] != key]
		if movers and (target is not None or len(movers) > 1):
			merges.append({
				'into': display(key),
				'existing': target is not None,
				'scrobbles': sum(sources[t] for t in movers),
				'from': [{'track': tracks[t], 'scrobbles': sources[t]} for t in sorted(movers, key=lambda t: (-sources[t], _trackname(tracks[t])))]
			})

	renames = []
	for track_id, targets in flows.items():
		if len(targets) != 1:
			continue
		(key, n), = targets.items()
		if key == track_key[track_id] or key in key_to_track or len(inflows[key]) > 1:
			continue
		changed = []
		if key[0] != track_key[track_id][0]:
			changed.append('title')
		if key[1] != track_key[track_id][1]:
			changed.append('artists')
		renames.append({'track': tracks[track_id], 'to': key_display[key], 'changed': changed, 'scrobbles': n})

	# artists
	def artist_kind(removed, added):
		if len(removed) == 1 and len(added) > 1:
			return 'split'
		if removed and not added:
			return 'dropped'
		if added and not removed:
			return 'added'
		if len(added) == 1 and (len(removed) > 1 or normalize_name(added[0]) in known_artists):
			return 'merge'
		if len(removed) == 1 and len(added) == 1:
			return 'rename'
		return 'other'

	changes = [
		{'from': list(r), 'to': list(a), 'kind': artist_kind(r, a), 'scrobbles': n}
		for (r, a), n in artist_changes.items()
	]
	vanishing = [
		{'artist': known_artists[n], 'scrobbles': c}
		for n, c in artists_before.items() if artists_after[n] == 0
	]
	new_artist_names = {}
	for key in inflows:
		for n in key[1]:
			if n not in known_artists:
				new_artist_names.setdefault(n, artist_display.get(n, n))
	new_artists = [{'artist': new_artist_names[n], 'scrobbles': artists_after[n]} for n in new_artist_names]

	return {
		'scrobbles': {
			'total': total,
			'reparsed': unchanged + moved,
			'unchanged': unchanged,
			'moved': moved,
			'no_raw': {'total': sum(no_raw.values()), 'by_origin': dict(sorted(no_raw.items(), key=lambda kv: (-kv[1], kv[0])))},
			'discarded': {'count': len(discarded), 'examples': discarded[:examples]},
			'errors': {'count': len(errors), 'examples': errors[:examples]},
		},
		'tracks': {
			'merges': {'count': len(merges), 'scrobbles': sum(m['scrobbles'] for m in merges), 'examples': _top(merges, examples, name=lambda m: _trackname(m['into']))},
			'splits': {'count': len(splits), 'scrobbles': sum(s['scrobbles'] for s in splits), 'examples': _top(splits, examples, name=lambda s: _trackname(s['track']))},
			'renames': {'count': len(renames), 'scrobbles': sum(r['scrobbles'] for r in renames), 'examples': _top(renames, examples, name=lambda r: _trackname(r['track']))},
			'new': new_tracks,
			'reordered': {
				'count': len(reordered),
				'scrobbles': sum(n for _, n in reordered.values()),
				'examples': _top(
					[{'track': tracks[t], 'credited': credited, 'scrobbles': n} for t, (credited, n) in reordered.items()],
					examples, name=lambda r: _trackname(r['track'])
				)
			},
		},
		'artists': {
			'changes': {
				'count': len(changes),
				'by_kind': dict(sorted(Counter(c['kind'] for c in changes).items())),
				'examples': _top(changes, examples, name=lambda c: ', '.join(c['from'] + c['to']))
			},
			'vanishing': {'count': len(vanishing), 'examples': _top(vanishing, examples, name=lambda v: v['artist'])},
			'new': {'count': len(new_artists), 'examples': _top(new_artists, examples, name=lambda v: v['artist'])},
		},
		'notes': [
			'Albums are not compared.',
			'Reordered tracks keep their stored artist order, only new tracks are stored in credited order.'
		],
	}
