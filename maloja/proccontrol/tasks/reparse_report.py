import json as jsonlib

from doreah.io import col


def _track(t):
	return f"{', '.join(t['artists'])} - {t['title']}"


def _num(n):
	return col['yellow'](f"{n:,}")


def reparse_report(examples=10, json=None):

	from ...database.reparse_report import build_report

	report = build_report(examples=int(examples))
	s, t, a = report['scrobbles'], report['tracks'], report['artists']

	print(col['green']("Reparse dry run - nothing has been changed."))
	print()
	print(f"Scrobbles:     {_num(s['total'])}")
	print(f"  reparsed     {_num(s['reparsed'])} ({_num(s['unchanged'])} unchanged, {_num(s['moved'])} would move to another track)")
	origins = ", ".join(f"{o} {n:,}" for o, n in s['no_raw']['by_origin'].items())
	print(f"  no raw data  {_num(s['no_raw']['total'])}" + (f" ({origins})" if origins else ""))
	print(f"  rejected     {_num(s['discarded']['count'])} (current rules would discard them; a reparse leaves them alone)")
	print(f"  errors       {_num(s['errors']['count'])}")
	print()
	print(f"Tracks:   {_num(t['merges']['count'])} merges, {_num(t['splits']['count'])} splits, {_num(t['renames']['count'])} renames, {_num(t['new'])} new")
	print(f"Artists:  {_num(a['changes']['count'])} changes ({', '.join(f'{k} {n}' for k, n in a['changes']['by_kind'].items()) or 'none'}), "
		f"{_num(a['vanishing']['count'])} would lose all scrobbles, {_num(a['new']['count'])} new")

	def section(title, lines):
		lines = list(lines)
		if lines:
			print()
			print(col['orange'](title))
			for line in lines:
				print("  " + line)

	section("Track merges", (
		f"{' + '.join(_track(f['track']) for f in m['from'])} -> {_track(m['into'])}{'' if m['existing'] else ' (new)'}  [{m['scrobbles']:,}]"
		for m in t['merges']['examples']
	))
	section("Track splits", (
		f"{_track(x['track'])} -> {' | '.join(_track(i['track']) + f' [{i['scrobbles']:,}]' for i in x['into'])}"
		for x in t['splits']['examples']
	))
	section("Track renames", (
		f"{_track(r['track'])} -> {_track(r['to'])}  ({', '.join(r['changed'])}) [{r['scrobbles']:,}]"
		for r in t['renames']['examples']
	))
	section("Artist changes", (
		f"{c['kind']:<8} {', '.join(c['from']) or '-'} -> {', '.join(c['to']) or '-'}  [{c['scrobbles']:,}]"
		for c in a['changes']['examples']
	))
	section("Artists that would lose all scrobbles", (f"{v['artist']}  [{v['scrobbles']:,}]" for v in a['vanishing']['examples']))
	section("New artists", (f"{v['artist']}  [{v['scrobbles']:,}]" for v in a['new']['examples']))
	section("Errors", (f"{e['timestamp']}: {e['error']}" for e in s['errors']['examples']))

	print()
	for note in report['notes']:
		print(note)
	print(f"Showing up to {examples} examples per section.")

	if json:
		with open(json, 'w') as f:
			jsonlib.dump(report, f, indent=2, ensure_ascii=False)
		print(f"Full report written to {col['yellow'](json)}")

	return report
