import sqlite3
import tarfile

from maloja.proccontrol.tasks import backup


def test_backup_includes_login_database(db, tmp_path):
	# the login database is created when maloja loads (pkg_global.conf.auth)
	backup(str(tmp_path))
	(archive,) = tmp_path.glob("maloja_backup_*.tar.gz")
	with tarfile.open(archive) as tar:
		assert {"state/auth/auth.sqlite", "state/malojadb.sqlite"} <= set(tar.getnames())
		tar.extract("state/auth/auth.sqlite", tmp_path, filter="data")
	with sqlite3.connect(tmp_path / "state/auth/auth.sqlite") as conn:
		assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] > 0
