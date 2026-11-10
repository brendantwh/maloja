import tarfile
import time
import glob
import os
import sqlite3
import tempfile
from ...pkg_global.conf import dir_settings
from pathlib import PurePath

from doreah.logging import log
from doreah.io import col


basic_files = [
	('config',	['rules/*.tsv','settings.ini','apikeys.yml','custom_css/*.css']),
	('state',	['auth/auth.ddb','malojadb.sqlite'])
]
expanded_files = [
	('state',	['images'])
]

def backup(targetfolder=None,include_images=False):

	if targetfolder is None:
		targetfolder = os.getcwd()

	if include_images:
		file_patterns = basic_files + expanded_files
	else:
		file_patterns = basic_files

	real_files = {}
	for category,patterns in file_patterns:
		real_files.setdefault(category,[])
		for pattern in patterns:
			real_files[category] += glob.glob(os.path.join(dir_settings[category],pattern))

	log("Creating backup...")


	timestr = time.strftime("%Y_%m_%d_%H_%M_%S")
	filename = f"maloja_backup_{timestr}.tar.gz"
	outputfile = os.path.join(targetfolder,filename)
	assert not os.path.exists(outputfile)

	with tarfile.open(name=outputfile,mode="x:gz") as archive, tempfile.TemporaryDirectory() as tmpdir:
		for category, filelist in real_files.items():
			for f in filelist:
				p = PurePath(f)
				r = p.relative_to(dir_settings[category])
				source = f
				if is_sqlite_database(f):
					# copying a database file that's in use can produce an inconsistent copy
					# (and misses anything still in the WAL file), so archive a snapshot instead
					source = os.path.join(tmpdir,"snapshot.sqlite")
					snapshot_sqlite_database(f,source)
				archive.add(source,arcname=os.path.join(category,r))
				if source != f:
					os.remove(source)
	log("Backup created: " + col['yellow'](outputfile))
	return outputfile


SQLITE_HEADER = b"SQLite format 3\x00"

def is_sqlite_database(path):
	if not os.path.isfile(path):
		return False
	with open(path,'rb') as fd:
		return fd.read(len(SQLITE_HEADER)) == SQLITE_HEADER

def snapshot_sqlite_database(path,target):
	# the backup api gives a consistent copy even while the server is writing
	src = sqlite3.connect(path)
	dst = sqlite3.connect(target)
	try:
		with dst:
			src.backup(dst)
	finally:
		dst.close()
		src.close()
