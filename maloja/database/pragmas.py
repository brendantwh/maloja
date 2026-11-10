# connection settings shared by all of Maloja's SQLite databases
# kept separate from sqldb so modules that are imported early (like images) can use it

import sqlalchemy as sql

from ..pkg_global.conf import malojaconfig


def apply_sqlite_pragmas(engine):
	# runs once for every new connection the pool opens
	wal = malojaconfig["DB_WAL_MODE"]

	@sql.event.listens_for(engine, "connect")
	def set_pragmas(dbapi_connection, connection_record):
		cursor = dbapi_connection.cursor()
		# WAL lets readers and writers work at the same time
		# the mode is stored in the file, so it has to be switched back explicitly when disabled
		cursor.execute(f"PRAGMA journal_mode={'WAL' if wal else 'DELETE'}")
		if wal:
			# safe in WAL mode, and much faster than the default FULL
			cursor.execute("PRAGMA synchronous=NORMAL")
		cursor.execute("PRAGMA busy_timeout=5000")
		cursor.execute("PRAGMA cache_size=-32000") # 32 MB
		cursor.execute("PRAGMA temp_store=MEMORY")
		cursor.close()
