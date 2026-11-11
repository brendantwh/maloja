import re
import os
import io
import csv
import contextvars
from dataclasses import dataclass

from .pkg_global.conf import data_dir, malojaconfig

# rule types and how many columns each needs after the type
RULE_COLUMNS = {
	"notanartist": 1,
	"belongtogether": 1,
	"ignoreartist": 1,
	"replacetitle": 2,
	"replacealbumtitle": 2,
	"replaceartist": 2,
	"countas": 2,
	"artistintitle": 2,
	"fixartists": 2,
	"addartists": 3,
}


# rules that can be limited to an artist ("artist=Name" column in a rule file)
SCOPABLE = {"replacetitle", "replacealbumtitle", "addartists", "fixartists", "artistintitle"}
SCOPE_PREFIX = "artist="
# the column a rule is matched by (the first one, unless listed here)
MATCH_COLUMN = {"addartists": 1, "fixartists": 1}
# these match the exact spelling, all others ignore case
CASE_SENSITIVE = {"belongtogether", "notanartist"}


@dataclass(frozen=True)
class Rule:
	type: str
	fields: tuple
	scope: str = None
	id: int = None
	source: str = None


def parse_rules_tsv(text, source=None):
	"""Parse rules in the TSV format (see rules.info). Returns (rules, errors)."""
	rules, errors = [], []
	for number, entry in enumerate(csv.reader(io.StringIO(text), delimiter="\t"), start=1):
		# empty columns are dropped, so columns can be lined up with several tabs
		entry = [col for col in entry if col]
		if not entry or entry[0].startswith('#'):
			continue
		ruletype, rest = entry[0], entry[1:]
		if ruletype not in RULE_COLUMNS:
			errors.append(f"line {number}: unknown rule type {ruletype!r}")
			continue
		needed = RULE_COLUMNS[ruletype]
		if len(rest) < needed:
			errors.append(f"line {number}: {ruletype} needs {needed} columns, got {len(rest)}")
			continue
		# additional columns (often trailing comments) are ignored, except for the artist scope
		scopes = [col[len(SCOPE_PREFIX):].strip() for col in rest[needed:] if col.startswith(SCOPE_PREFIX)]
		if scopes and ruletype not in SCOPABLE:
			errors.append(f"line {number}: {ruletype} can't be limited to an artist")
			continue
		rules.append(Rule(ruletype, tuple(rest[:needed]), scopes[0] if scopes else None, source=source))
	return rules, errors


def read_rule_files(folder):
	rules = []
	try:
		files = sorted(os.listdir(folder))
	except FileNotFoundError:
		return rules
	for f in files:
		if f.split('.')[-1].lower() != 'tsv': continue
		with open(os.path.join(folder, f), 'r') as filed:
			rules += parse_rules_tsv(filed.read(), source=f"file:{f}")[0]
	return rules


class RuleBook:
	"""The rules in the shape the parser looks them up.
	Later rules win over earlier ones, and a rule limited to an artist wins over one that isn't."""

	def __init__(self, rules):
		self.rules = list(rules)
		self.tables = {}  # type -> {(scope, match): rule}
		for rule in self.rules:
			scope = rule.scope.casefold() if rule.scope else None
			self.tables.setdefault(rule.type, {})[(scope, self.matchkey(rule.type, rule.fields[MATCH_COLUMN.get(rule.type, 0)]))] = rule

	@staticmethod
	def matchkey(ruletype, value):
		return value if ruletype in CASE_SENSITIVE else value.lower()

	def find(self, ruletype, value, artists=()):
		"""The rule for this value, preferring one limited to the first matching artist."""
		table = self.tables.get(ruletype, {})
		key = self.matchkey(ruletype, value)
		for artist in artists:
			rule = table.get((artist.casefold(), key))
			if rule:
				return rule
		return table.get((None, key))

	def all(self, ruletype, artists=()):
		"""All rules of a type that apply with these artists, one per match value."""
		scopes = [a.casefold() for a in artists]
		found = {}
		for (scope, key), rule in self.tables.get(ruletype, {}).items():
			if scope is None:
				found.setdefault(key, rule)
			elif scope in scopes:
				found[key] = rule
		return list(found.values())


# rules that fired during CleanerAgent.explain, per thread or task
_fired = contextvars.ContextVar("fired_rules", default=None)

def fired(rule):
	trace = _fired.get()
	if trace is not None and rule not in trace:
		trace.append(rule)
	return rule


class CleanerAgent:

	def __init__(self, rules=None, rules_loader=None, known_artist=None):
		# optional callback: is this exact name already a known artist? (see sqldb.is_known_artist)
		self.known_artist = known_artist
		# rules are either given, or loaded on first use (by default from the rule files)
		self.rules_loader = rules_loader or (lambda: read_rule_files(data_dir["rules"]()))
		self._book = None
		if rules is not None:
			self.set_rules(rules)

	def set_rules(self, rules):
		# built completely before it's swapped in, so a parse running at the same time never sees half of it
		self._book = RuleBook(rules)

	@property
	def book(self):
		if self._book is None:
			self.set_rules(self.rules_loader())
		return self._book

	@property
	def rules(self):
		return self.book.rules


	def explain(self,artist,title,album=None):
		"""Like fullclean, but also returns the rules that fired, in the order they did."""
		token = _fired.set([])
		try:
			artists,title,album = self.fullclean(artist,title,album)
			return {'artists':artists,'title':title,'album':album,'rules':_fired.get()}
		finally:
			_fired.reset(token)

	def fullclean(self,artist,title,album):
		artists = self.parseArtists(self.removespecial(artist))
		title = self.parseTitle(self.removespecial(title),artists)
		(stripped,moreartists) = self.parseTitleForArtists(title,artists)
		# off by default: the full title, including "(feat. X)", is kept on purpose
		if malojaconfig["TITLE_STRIP_FEAT"]:
			title = stripped
		artists += moreartists
		addrule = self.book.find("addartists",title,artists)
		fixrule = self.book.find("fixartists",title,artists)
		if addrule:
			reqartists, _, allartists = addrule.fields
			reqartists = reqartists.lower().split("␟")
			allartists = allartists.split("␟")
			if set(reqartists).issubset({a.lower() for a in artists}):
				fired(addrule)
				artists += allartists
		elif fixrule:
			allartists = fixrule.fields[0]
			allartists = allartists.split("␟")
			if len({a.lower() for a in allartists} & {a.lower() for a in artists}) > 0:
				fired(fixrule)
				artists = allartists
		# main artist first, in the order they were credited
		artists = list(dict.fromkeys(artists))
		if not malojaconfig["ARTIST_CREDIT_ORDER"]:
			artists.sort()

		if album is not None:
			album = self.parseAlbumtitle(album,artists)
			album = album.strip()

		return (artists,title.strip(),album)

	def removespecial(self,s):
		if isinstance(s,list):
			return [self.removespecial(se) for se in s]
		s = s.replace("\t","").replace("␟","").replace("\n","")
		s = re.sub(" +"," ",s)
		return s


	#Delimiters used for extra artists, even when in the title field
	#delimiters_feat = ["ft.","ft","feat.","feat","featuring","Ft.","Ft","Feat.","Feat","Featuring"]
	delimiters_feat = malojaconfig["DELIMITERS_FEAT"]
	#Delimiters in informal artist strings, spaces expected around them
	#delimiters = ["vs.","vs","&"]
	delimiters = malojaconfig["DELIMITERS_INFORMAL"]
	#Delimiters used specifically to tag multiple artists when only one tag field is available, no spaces used
	#delimiters_formal = ["; ",";","/"]
	delimiters_formal = malojaconfig["DELIMITERS_FORMAL"]

	def parseArtists(self,a):

		if isinstance(a,list) or isinstance(a,tuple):
			res = [self.parseArtists(art) for art in a]
			return [a for group in res for a in group]

		if a.strip() in malojaconfig["INVALID_ARTISTS"]:
			return []

		if rule := self.book.find("ignoreartist",a.strip()):
			fired(rule)
			return []

		if a.strip() == "":
			return []

		if rule := self.book.find("notanartist",a.strip()):
			fired(rule)
			return []

		if " performing " in a.lower():
			return self.parseArtists(re.split(" [Pp]erforming",a)[0])

		if rule := self.book.find("belongtogether",a.strip()):
			fired(rule)
			return [a.strip()]
		replacement = self.book.find("replaceartist",a.strip())
		if replacement:
			return fired(replacement).fields[1].split("␟")



		for d in self.delimiters_feat:
			match = re.match(r"(.*) [\(\[]" + re.escape(d) + r" (.*)[\)\]]",a,flags=re.IGNORECASE)
			if match is not None:
				rest = a[match.end():]
				return self.parseArtists(match.group(1) + rest) + self.parseArtists(match.group(2) + rest)



		for d in self.delimiters_feat:
			if ((" " + d + " ") in a):
				ls = []
				for i in a.split(" " + d + " "):
					ls += self.parseArtists(i)
				return ls

		# names like "Simon & Garfunkel" or "AC/DC" stay whole once they are known
		# (featuring is never protected, those are not band names)
		for d in self.delimiters:
			if ((" " + d + " ") in a):
				if self.isKnownArtist(a):
					return [a.strip()]
				ls = []
				for i in a.split(" " + d + " "):
					ls += self.parseArtists(i)
				return ls

		for d in self.delimiters_formal:
			if (d in a):
				if self.isKnownArtist(a):
					return [a.strip()]
				ls = []
				for i in a.split(d):
					ls += self.parseArtists(i)
				return ls

		# commas and " x " are often part of a name ("Tyler, The Creator"), so these only
		# split when every part is already a known artist ("Lady Gaga, Bradley Cooper")
		if self.known_artist and malojaconfig["ARTIST_SPLIT_KNOWN_LISTS"]:
			parts = [p.strip() for p in re.split(r", | x ",a,flags=re.IGNORECASE)]
			if len(parts) > 1 and all(p and self.known_artist(p) for p in parts):
				return [artist for p in parts for artist in self.parseArtists(p)]





		return [a.strip()]

	def isKnownArtist(self,a):
		return bool(self.known_artist and malojaconfig["ARTIST_KEEP_KNOWN"] and self.known_artist(a.strip()))

	def parseTitle(self,t,artists=()):
		replacement = self.book.find("replacetitle",t.strip(),artists)
		if replacement:
			return fired(replacement).fields[1]

		t = t.replace("[","(").replace("]",")")

		# we'll leave these matching all bracket types so future changes
		# won't require readaption
		# [^()\[\]] keeps a match inside one bracket group, so it can't eat a (feat. X) before it
		t = re.sub(r" [\(\[]as made famous by [^()\[\]]*[\)\]]","",t)
		t = re.sub(r" [\(\[]originally by [^()\[\]]*[\)\]]","",t)
		t = self.stripVariants(t)

		for s in malojaconfig["REMOVE_FROM_TITLE"]:
			if s in t:
				t = t.replace(s,"")

		t = t.strip()
		#for p in self.plugin_titleparsers:
		#	t = p(t).strip()
		return t

	# versions of a song that should count as the same track, each behind its own setting
	# (live versions and remixes are deliberately not here, they stay separate tracks)
	title_variants = [
		("TITLE_MERGE_REMASTERS", r".*\bremaster(ed)?\b.*"),
		("TITLE_MERGE_EDITS", r"(\d{4} )?(radio|single) (edit|version)"),
		("TITLE_MERGE_MONO_STEREO", r"(\d{4} )?(mono|stereo)( (version|mix))?"),
	]

	def stripVariants(self,t):
		patterns = [p for setting,p in self.title_variants if malojaconfig[setting]]
		if not patterns:
			return t

		# a tag can combine several things, e.g. "Live Aid / Remastered": only drop the variant parts
		def strip_parts(segment):
			parts = [p for p in segment.split(" / ") if not any(re.fullmatch(pat,p.strip(),flags=re.IGNORECASE) for pat in patterns)]
			return " / ".join(parts)

		def strip_bracket(match):
			rest = strip_parts(match.group(1))
			return f" ({rest})" if rest else ""

		stripped = re.sub(r" \(([^()]*)\)",strip_bracket,t)

		# Spotify style suffix: "Let It Be - Remastered 2009"
		head,dash,tail = stripped.rpartition(" - ")
		if head and "(" not in tail:
			rest = strip_parts(tail)
			stripped = head + (dash + rest if rest else "")

		return stripped if stripped.strip() else t

	def parseTitleForArtists(self,title,known=()):
		artists = []
		patterns = []
		for delimiter in malojaconfig["DELIMITERS_FEAT"]:
			patterns += [
				r" [\(\[]" + re.escape(delimiter) + r" (.*?)[\)\]]",
				r" - " + re.escape(delimiter) + r" (.*)",
			]
			# without brackets or a dash, bare words like "ft" or "feat" are too often part of the title
			if delimiter.endswith(".") or delimiter.lower() == "featuring":
				patterns.append(r" " + re.escape(delimiter) + r" (.*)")
		# words like "with" are only a delimiter in brackets, e.g. Spotify's "One Kiss (with Dua Lipa)"
		for delimiter in malojaconfig["DELIMITERS_FEAT_TITLE"]:
			patterns.append(r" [\(\[]" + re.escape(delimiter) + r" (.*?)[\)\]]")
		original = title
		found = []
		for pattern in patterns:
			matches = re.finditer(pattern,title,flags=re.IGNORECASE)
			for match in matches:
				title = match.re.sub('',match.string) # Remove matched part
				found.append((original.find(match.group(0)),self.parseArtists(match.group(1)))) # Parse matched artist string
		# credit order: as they appear in the title, not in the order of the patterns
		for _,matched in sorted(found,key=lambda f: f[0]):
			artists += matched



		if malojaconfig["PARSE_REMIX_ARTISTS"]:
			for filter in malojaconfig["FILTERS_REMIX"]:
				for pattern in [
					r" [\(\[](.*)" + re.escape(filter) + r"[\)\]]", # match remix in brackets
					r" - (.*)" + re.escape(filter) # match remix split with "-"
				]:
					match = re.search(pattern,title,flags=re.IGNORECASE)
					if match:
						# title stays the same
						artists += self.parseArtists(match.group(1))



		for rule in self.book.all("artistintitle",known):
			if rule.fields[0].lower() in title.lower(): artists += fired(rule).fields[1].split("␟")
		return (title,artists)

	def parseAlbumtitle(self,t,artists=()):
		replacement = self.book.find("replacealbumtitle",t.strip(),artists)
		if replacement:
			return fired(replacement).fields[1]

		t = t.replace("[","(").replace("]",")")

		if malojaconfig["ALBUM_TRIM"]:
			# only strip the suffix Apple Music appends, not matches elsewhere in the title
			t = re.sub(r"\s+-\s+(EP|Single)\s*$", "", t)

		t = t.strip()
		return t

