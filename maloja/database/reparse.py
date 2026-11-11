# apply the current parser and rules to scrobbles you already have
# preview first with reparse_report.build_report, this changes the database
# - a snapshot of the database is saved to the backups folder before anything changes
# - scrobbles are reparsed from their stored raw data in batches, each batch in one
#   transaction under SCROBBLE_LOCK, so new scrobbles can still come in between batches
# - tracks in skip_track_ids keep all their scrobbles (and their artist order)
# - no aliases are recorded: these changes come from the parser, not from your edits

import json
import os
import time
from collections import Counter, defaultdict

import sqlalchemy as sql
from doreah.logging import log

from ..pkg_global.conf import data_dir
from . import dbcache, sqldb
from .sqldb import DB


def snapshot_database():
	from ..proccontrol.tasks.backup import snapshot_sqlite_database
	os.makedirs(data_dir['backups'](), exist_ok=True)
	target = data_dir['backups'](time.strftime("pre-reparse_%Y_%m_%d_%H_%M_%S.sqlite"))
	snapshot_sqlite_database(data_dir['scrobbles']('malojadb.sqlite'), target)
	return target


def _parse(raw, memo):
	from . import rawscrobble_to_scrobbledict
	# scrobbles of the same song only differ in time, so each raw input is parsed once
	key = json.dumps({k: v for k, v in raw.items() if k not in ('scrobble_time', 'scrobble_duration')}, sort_keys=True)
	if key not in memo:
		try:
			result = rawscrobble_to_scrobbledict(raw)
			memo[key] = ('ok', result and result['track'])
		except Exception as e:
			memo[key] = ('error', repr(e))
	return memo[key]


def _credited_artist_ids(track, dbconn):
	ids = [sqldb.get_artist_id(a, create_new=False, dbconn=dbconn) for a in track['artists']]
	return list(dict.fromkeys(i for i in ids if i is not None))


def apply_reparse(skip_track_ids=(), reorder=False, progress=None, batch_size=500):
	"""Returns counts of what changed and the path of the snapshot taken first."""
	skip = {int(t) for t in skip_track_ids}
	snapshot = snapshot_database()
	log(f"Reparsing scrobbles, database snapshot saved to {snapshot}")

	with sqldb.engine.connect() as conn:
		timestamps = [r.timestamp for r in conn.execute(sql.select(DB['scrobbles'].c.timestamp).order_by(DB['scrobbles'].c.timestamp))]

	counts = Counter()
	memo = {}
	reordered = set()
	flows = defaultdict(Counter)  # old track -> new track -> scrobbles
	table = DB['scrobbles']

	for start in range(0, len(timestamps), batch_size):
		batch = timestamps[start:start + batch_size]
		try:
			with sqldb.SCROBBLE_LOCK, sqldb.engine.begin() as conn:
				rows = conn.execute(sql.select(table.c.timestamp, table.c.track_id, table.c.rawscrobble).where(table.c.timestamp.in_(batch))).all()
				for row in rows:
					counts['total'] += 1
					raw = json.loads(row.rawscrobble) if row.rawscrobble else None
					if not raw:
						counts['no_raw'] += 1
						continue
					if row.track_id in skip:
						counts['skipped'] += 1
						continue
					status, newtrack = _parse(raw, memo)
					if status == 'error':
						counts['errors'] += 1
						continue
					if newtrack is None:
						# same as reparse_scrobble: scrobbles the rules would now reject stay as they are
						counts['discarded'] += 1
						continue

					new_id = sqldb.get_track_id(newtrack, dbconn=conn)
					if new_id != row.track_id:
						conn.execute(table.update().where(table.c.timestamp == row.timestamp).values(track_id=new_id))
						flows[row.track_id][new_id] += 1
						counts['moved'] += 1
						continue
					counts['unchanged'] += 1
					if reorder and new_id not in reordered:
						reordered.add(new_id)
						credited = _credited_artist_ids(newtrack, conn)
						try:
							counts['reordered'] += sqldb.set_track_artist_order(new_id, credited, dbconn=conn)
						except ValueError:
							pass
		except Exception:
			# ids created in the failed batch may be cached
			dbcache.invalidate_entity_cache()
			dbcache.invalidate_caches()
			raise
		if progress:
			progress(min(start + batch_size, len(timestamps)), len(timestamps))

	# a track whose scrobbles all went to one other track was merged: its aliases move along
	with sqldb.engine.begin() as conn:
		for old, targets in flows.items():
			left = conn.execute(sql.select(sql.func.count()).select_from(table).where(table.c.track_id == old)).scalar()
			if len(targets) == 1 and not left:
				(new,) = targets
				sqldb.repoint_aliases('track', new, [old], dbconn=conn)

	sqldb.clean_db()
	sqldb.forget_known_artists()
	dbcache.invalidate_entity_cache()
	dbcache.invalidate_caches()
	result = {'snapshot': snapshot, **{k: counts[k] for k in ('total', 'moved', 'unchanged', 'reordered', 'skipped', 'no_raw', 'discarded', 'errors')}}
	log(f"Reparse done: {result}")
	return result
