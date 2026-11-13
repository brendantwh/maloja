# apply the current parser and rules to scrobbles you already have
# preview first with reparse_report.build_report, this changes the database
# - a snapshot of the database is saved to the backups folder before anything changes
# - scrobbles are reparsed from their stored raw data in batches, each batch in one
#   transaction under SCROBBLE_LOCK, so new scrobbles can still come in between batches
# - tracks in skip_track_ids keep all their scrobbles (and their artist order)
# - no aliases are recorded: these changes come from the parser, not from your edits

import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from threading import Lock, Thread

import sqlalchemy as sql
from doreah.logging import log

from ..pkg_global.conf import data_dir, malojaconfig
from . import dbcache, sqldb
from .sqldb import DB


# the settings that change how a scrobble is parsed (plus the TITLE_MERGE_* ones, see parser_fingerprint)
PARSER_SETTINGS = (
	"ARTIST_CREDIT_ORDER", "ARTIST_KEEP_KNOWN", "ARTIST_SPLIT_KNOWN_LISTS",
	"DELIMITERS_FEAT", "DELIMITERS_FEAT_TITLE", "DELIMITERS_FORMAL", "DELIMITERS_INFORMAL",
	"FILTERS_REMIX", "INVALID_ARTISTS", "PARSE_REMIX_ARTISTS", "REMOVE_FROM_TITLE", "TITLE_STRIP_FEAT",
)


def parser_fingerprint():
	"""A hash of the rules and parser settings in use, to tell whether a preview still matches them."""
	from . import cla
	from .rules import rule_key
	settings = PARSER_SETTINGS + tuple(setting for setting, _ in cla.title_variants)
	state = {
		'rules': [rule_key(r) for r in cla.rules],
		'settings': {k: malojaconfig[k] for k in settings},
	}
	text = json.dumps(state, sort_keys=True, default=lambda v: sorted(v) if isinstance(v, (set, frozenset)) else str(v))
	return hashlib.sha256(text.encode()).hexdigest()


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


# one background job at a time for the web interface, the server keeps running meanwhile

class JobRunning(Exception):
	pass


class PreviewOutdated(Exception):
	pass


_job_lock = Lock()
_job = {'state': 'idle'}
_thread = None


def job_status(include_report=False):
	with _job_lock:
		status = {k: v for k, v in _job.items() if include_report or k != 'report'}
	return status


def _start(state, work, check=None, **info):
	global _thread
	with _job_lock:
		if _job['state'] in ('previewing', 'applying'):
			raise JobRunning(f"A reparse job is already running ({_job['state']})")
		if check:
			check(_job)
		_job.clear()
		_job.update(state=state, started=int(time.time()), progress=[0, 0], **info)

	def run():
		try:
			done = work()
			with _job_lock:
				_job.update(done, finished=int(time.time()))
		except Exception as e:
			log(f"Reparse job failed: {e!r}", color='red')
			with _job_lock:
				_job.update(state='error', error=repr(e), finished=int(time.time()))

	_thread = Thread(target=run, daemon=True)
	_thread.start()


def start_preview():
	from .reparse_report import build_report
	# taken before the report is built: a rule changed while it's built makes the preview outdated too
	_start('previewing', lambda: {'state': 'ready', 'report': build_report(examples=None)}, fingerprint=parser_fingerprint())


def _check_preview(job):
	# apply reparses with the rules in use now, so they have to be the ones the preview was made with
	if job['state'] != 'ready':
		raise PreviewOutdated("There is no preview to apply, make a new preview first")
	if job.get('fingerprint') != parser_fingerprint():
		raise PreviewOutdated("The rules or parser settings changed since this preview, make a new preview first")


def start_apply(skip_track_ids=(), reorder=False):
	"""Apply the reviewed preview: refuses if there is none, or it doesn't match the current rules and settings."""
	def progress(done, total):
		with _job_lock:
			_job['progress'] = [done, total]
	_start('applying', lambda: {'state': 'done', 'result': apply_reparse(skip_track_ids, reorder, progress=progress)},
		check=_check_preview, skip_track_ids=sorted({int(t) for t in skip_track_ids}), reorder=bool(reorder))


def wait_for_job(timeout=None):
	if _thread:
		_thread.join(timeout)
