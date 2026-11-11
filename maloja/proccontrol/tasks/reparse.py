from doreah.io import ask, col

from ...pkg_global.conf import malojaconfig


def reparse(apply=False, reorder=False, examples=10):
	"""maloja reparse: dry run. maloja reparse --apply [--reorder]: change the database."""
	from .reparse_report import reparse_report

	if not apply:
		reparse_report(examples=examples)
		print()
		print("To apply this, stop the server and run " + col['yellow']("maloja reparse --apply") + " (add --reorder to also fix the artist order).")
		print("The web interface (Admin > Reparse) can do the same while the server runs, and lets you skip single tracks.")
		return True

	print(col['orange']("This changes your database. Stop the Maloja server first, it would not see the changes."))
	print("A snapshot of the database is saved to the backups folder before anything changes.")
	# without a terminal (e.g. in a container) --apply is the confirmation
	if not malojaconfig["SKIP_SETUP"] and not ask("Apply the current rules to all scrobbles?", default=False):
		return False

	from ...pkg_global import conf
	from ... import database
	from ...database import rules
	from ...database.reparse import apply_reparse

	# the server is stopped, so this process may clean up and invalidate like the server does
	conf.AUX_MODE = False
	database.cla.set_rules(rules.load_rules(import_files=True))

	def progress(done, total):
		print(f"\r{done:,} / {total:,} scrobbles", end="", flush=True)

	result = apply_reparse(reorder=reorder, progress=progress)
	print()
	print(col['green']("Done."), f"{result['moved']:,} scrobbles moved to another track, {result['reordered']:,} tracks reordered.")
	print(f"Unchanged {result['unchanged']:,}, without raw data {result['no_raw']:,}, rejected by the rules {result['discarded']:,}, errors {result['errors']:,}.")
	print("Snapshot from before: " + col['yellow'](result['snapshot']))
	return True
