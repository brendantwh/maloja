## dealing with loading the associated artists rules into a database
## right now this is kind of absurd because we're storing it in a db while not
## actually using its permanence, but this makes it possible to use the information
## directly in sql


from . import sqldb


def load_associated_rules(rules=None):
	# the countas rules of the shared parser, unless given
	# delete old
	with sqldb.engine.begin() as conn:
		op = sqldb.DB['associated_artists'].delete().where()
		conn.execute(op)

	if rules is None:
		from . import cla
		rules = cla.rules
	rules = [{'source_artist':r.fields[0],'target_artist':r.fields[1]} for r in rules if r.type=="countas"]

	#for rule in rules:
	#	print(f"Rule to replace {rule['source_artist']} with {rule['target_artist']}:")
	#	test = {k:sqldb.get_artist_id(rule[k],create_new=False) for k in rule}
	#	if test['source_artist'] is None: print("axed")

	#allartists = set([*[r['source_artist'] for r in rules],*[r['target_artist'] for r in rules]])

	# find ids
	rules = [{k:sqldb.get_artist_id(rule[k],create_new=False) for k in rule} for rule in rules]
	rules = [r for r in rules if r['source_artist'] is not None]

	# write to db
	ops = [
		sqldb.DB['associated_artists'].insert().values(**r).prefix_with('OR IGNORE')
		for r in rules
	]

	with sqldb.engine.begin() as conn:
		for op in ops:
			conn.execute(op)
