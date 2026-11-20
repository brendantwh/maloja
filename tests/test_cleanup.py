import warnings

import pytest

from maloja import cleanup


# characterisation: pins current behaviour that is still an open decision
# (see _artifacts/2026-11-10_parser-and-rules-improvements.md, section 7)
PARSER_CASES = [
	pytest.param("A feat. B", "Song", ["A", "B"], "Song", id="split-feat"),
	pytest.param("A & B", "Song", ["A", "B"], "Song", id="split-ampersand"),
	pytest.param("A; B", "Song", ["A", "B"], "Song", id="split-formal"),
	pytest.param("Simon & Garfunkel", "The Sound of Silence", ["Simon", "Garfunkel"], "The Sound of Silence", id="char-band-ampersand"),
	pytest.param("AC/DC", "Back In Black", ["AC", "DC"], "Back In Black", id="char-band-slash"),
	pytest.param("Lady Gaga, Bradley Cooper", "Shallow", ["Lady Gaga, Bradley Cooper"], "Shallow", id="char-comma"),
	pytest.param("B & A", "Song", ["B", "A"], "Song", id="credit-order"),
	pytest.param("Daft Punk", "Get Lucky (feat. Pharrell Williams)", ["Daft Punk", "Pharrell Williams"], "Get Lucky (feat. Pharrell Williams)", id="title-feat-kept"),
	pytest.param("Calvin Harris", "One Kiss (with Dua Lipa)", ["Calvin Harris", "Dua Lipa"], "One Kiss (with Dua Lipa)", id="title-with"),
	pytest.param("A", "Dancing with Myself", ["A"], "Dancing with Myself", id="title-with-bare-kept"),
	pytest.param("A", "Song (2011 Remaster)", ["A"], "Song", id="remaster-bracket"),
	pytest.param("The Beatles", "Let It Be - Remastered 2009", ["The Beatles"], "Let It Be", id="remaster-dash"),
	pytest.param("A", "Song - 2011 Remaster", ["A"], "Song", id="remaster-dash-year-first"),
	pytest.param("Queen", "Bohemian Rhapsody - Live Aid / Remastered", ["Queen"], "Bohemian Rhapsody - Live Aid", id="live-remaster-combined"),
	pytest.param("A", "Song - Radio Edit", ["A"], "Song", id="radio-edit-dash"),
	pytest.param("A", "Song (Single Version)", ["A"], "Song", id="single-version"),
	pytest.param("A", "Song - Mono", ["A"], "Song", id="mono-dash"),
	pytest.param("A", "Song (2009 Stereo Mix)", ["A"], "Song", id="stereo-mix-bracket"),
	pytest.param("Nirvana", "Come As You Are - Live", ["Nirvana"], "Come As You Are - Live", id="live-kept"),
	pytest.param("A", "Song (X Remix)", ["A"], "Song (X Remix)", id="remix-kept"),
	pytest.param("A (ftX B)", "Song", ["A (ftX B)"], "Song", id="artist-delimiter-escaped"),
	pytest.param("A", "Song (feat. B) (Remastered)", ["A", "B"], "Song (feat. B)", id="feat-before-remaster"),
	pytest.param("A", "A Feat of Strength", ["A"], "A Feat of Strength", id="feat-word-not-delimiter"),
	pytest.param("A", "Song feat. B", ["A", "B"], "Song feat. B", id="feat-dotted-bare"),
]


@pytest.mark.parametrize("artists_in,title_in,artists_out,title_out", PARSER_CASES)
def test_parser(agent, artists_in, title_in, artists_out, title_out):
	artists, title, _ = agent.fullclean(artists_in, title_in, None)
	assert (artists, title) == (artists_out, title_out)


# each behaviour change can be switched off (or on) with its own setting
SETTINGS_CASES = [
	pytest.param({"DELIMITERS_FEAT_TITLE": []}, "Calvin Harris", "One Kiss (with Dua Lipa)", ["Calvin Harris"], "One Kiss (with Dua Lipa)", id="title-with-off"),
	pytest.param({"TITLE_MERGE_REMASTERS": False}, "A", "Song - Remastered 2009 (2011 Remaster)", ["A"], "Song - Remastered 2009 (2011 Remaster)", id="remasters-off"),
	pytest.param({"TITLE_MERGE_EDITS": False}, "A", "Song - Single Edit", ["A"], "Song - Single Edit", id="edits-off"),
	pytest.param({"TITLE_MERGE_MONO_STEREO": False}, "A", "Song - Mono", ["A"], "Song - Mono", id="mono-stereo-off"),
	pytest.param({"TITLE_STRIP_FEAT": True}, "Daft Punk", "Get Lucky (feat. Pharrell Williams)", ["Daft Punk", "Pharrell Williams"], "Get Lucky", id="strip-feat-on"),
	pytest.param({"TITLE_STRIP_FEAT": True}, "A", "Song (with B) - feat. C", ["A", "B", "C"], "Song", id="strip-feat-on-with-and-dash"),
	pytest.param({"ARTIST_CREDIT_ORDER": False}, "B & A", "Song", ["A", "B"], "Song", id="credit-order-off"),
]


@pytest.mark.parametrize("values,artists_in,title_in,artists_out,title_out", SETTINGS_CASES)
def test_parser_settings(agent, settings, values, artists_in, title_in, artists_out, title_out):
	settings(**values)
	artists, title, _ = agent.fullclean(artists_in, title_in, None)
	assert (artists, title) == (artists_out, title_out)


RULES_CASES = [
	pytest.param(
		[("belongtogether", "Simon & Garfunkel")],
		"Simon & Garfunkel", "The Boxer", ["Simon & Garfunkel"], "The Boxer",
		id="belongtogether",
	),
	pytest.param(
		[("replaceartist", "Dual Artist", "First␟Second")],
		"Dual Artist", "Song", ["First", "Second"], "Song",
		id="replaceartist-multiple",
	),
	pytest.param(
		# replacetitle is global: it renames this title for every artist
		[("replacetitle", "RED", "Red")],
		"Some Other Band", "RED", ["Some Other Band"], "Red",
		id="replacetitle-global",
	),
	pytest.param(
		# fixartists is keyed by title alone
		[("fixartists", "Main␟Guest", "Collab Song")],
		"Main", "Collab Song", ["Main", "Guest"], "Collab Song",
		id="fixartists-by-title",
	),
	# rules limited to an artist
	pytest.param(
		[("replacetitle", "RED", "Red", "artist=HyunA")],
		"HyunA", "RED", ["HyunA"], "Red",
		id="scoped-replacetitle",
	),
	pytest.param(
		[("replacetitle", "RED", "Red", "artist=HyunA")],
		"Taylor Swift", "RED", ["Taylor Swift"], "RED",
		id="scoped-replacetitle-other-artist",
	),
	pytest.param(
		[("replacetitle", "RED", "Red (global)"), ("replacetitle", "RED", "Red", "artist=hyuna")],
		"Guest & HyunA", "RED", ["Guest", "HyunA"], "Red",
		id="scoped-beats-global",
	),
	pytest.param(
		[("fixartists", "Main␟Guest", "Collab Song", "artist=Main")],
		"Other & Guest", "Collab Song", ["Other", "Guest"], "Collab Song",
		id="scoped-fixartists-other-artist",
	),
	pytest.param(
		[("artistintitle", "Areia Remix", "Areia", "artist=A")],
		"A", "Song (Areia Remix)", ["A", "Areia"], "Song (Areia Remix)",
		id="scoped-artistintitle",
	),
	pytest.param(
		[("artistintitle", "Areia Remix", "Areia", "artist=A")],
		"B", "Song (Areia Remix)", ["B"], "Song (Areia Remix)",
		id="scoped-artistintitle-other-artist",
	),
]


@pytest.mark.parametrize("rules,artists_in,title_in,artists_out,title_out", RULES_CASES)
def test_rules(agent_with_rules, rules, artists_in, title_in, artists_out, title_out):
	artists, title, _ = agent_with_rules(*rules).fullclean(artists_in, title_in, None)
	assert (artists, title) == (artists_out, title_out)


# the database tells the parser which full names already exist (sqldb.is_known_artist)
KNOWN_ARTISTS_CASES = [
	pytest.param({}, "Simon & Garfunkel", ["Simon & Garfunkel"], id="known-kept-whole"),
	pytest.param({}, "Simon & Paul", ["Simon", "Paul"], id="unknown-split"),
	pytest.param({}, "AC/DC", ["AC/DC"], id="known-formal-kept-whole"),
	pytest.param({}, "A feat. B", ["A", "B"], id="known-feat-still-split"),
	pytest.param({"ARTIST_KEEP_KNOWN": False}, "Simon & Garfunkel", ["Simon", "Garfunkel"], id="keep-known-off"),
	pytest.param({}, "Lady Gaga, Bradley Cooper", ["Lady Gaga", "Bradley Cooper"], id="comma-known-parts-split"),
	pytest.param({}, "Lady Gaga, Someone New", ["Lady Gaga, Someone New"], id="comma-unknown-part-whole"),
	pytest.param({}, "Tyler, The Creator", ["Tyler, The Creator"], id="comma-band-whole"),
	pytest.param({}, "Marshmello x Bastille", ["Marshmello", "Bastille"], id="x-known-parts-split"),
	pytest.param({}, "Malcolm X", ["Malcolm X"], id="x-in-name-whole"),
	pytest.param({"ARTIST_SPLIT_KNOWN_LISTS": False}, "Lady Gaga, Bradley Cooper", ["Lady Gaga, Bradley Cooper"], id="split-known-lists-off"),
]


@pytest.mark.parametrize("values,artists_in,artists_out", KNOWN_ARTISTS_CASES)
def test_known_artists(settings, values, artists_in, artists_out):
	known = {"Simon & Garfunkel", "AC/DC", "A feat. B", "Simon", "Garfunkel", "Paul", "A", "B", "Lady Gaga", "Bradley Cooper", "The Creator", "Marshmello", "Bastille", "Malcolm"}
	settings(**values)
	artists, _, _ = cleanup.CleanerAgent(known_artist=lambda name: name in known).fullclean(artists_in, "Song", None)
	assert artists == artists_out


def test_parse_rules_tsv():
	text = "# a comment\nreplacetitle\t\tRED\t\tRed\t# trailing comment\nreplacetitle\tonly one\nregexartist\ta\tb\n"
	rules, errors = cleanup.parse_rules_tsv(text, source="file:x.tsv")
	assert rules == [cleanup.Rule("replacetitle", ("RED", "Red"), source="file:x.tsv")]
	assert errors == ["line 3: replacetitle needs 2 columns, got 1", "line 4: unknown rule type 'regexartist'"]

	rules, errors = cleanup.parse_rules_tsv("replacetitle\tRED\tRed\tartist=HyunA\nreplaceartist\tA\tB\tartist=C\n")
	assert rules == [cleanup.Rule("replacetitle", ("RED", "Red"), "HyunA")]
	assert errors == ["line 2: replaceartist can't be limited to an artist"]


def test_explain_lists_fired_rules(agent_with_rules):
	agent = agent_with_rules(
		("replaceartist", "Old", "New"),
		("replacetitle", "RED", "Red"),
		("addartists", "Nobody", "Red", "Extra"),
	)
	result = agent.explain("Old", "RED")
	assert (result["artists"], result["title"]) == (["New"], "Red")
	assert [(r.type, r.fields) for r in result["rules"]] == [("replaceartist", ("Old", "New")), ("replacetitle", ("RED", "Red"))]
	# nothing is recorded outside explain
	assert agent.fullclean("Old", "RED", None) == (["New"], "Red", None)


def test_scoped_album_rule(agent_with_rules):
	agent = agent_with_rules(("replacealbumtitle", "Greatest Hits", "Greatest Hits (2001)", "artist=A"))
	assert agent.fullclean("A", "Song", "Greatest Hits")[2] == "Greatest Hits (2001)"
	assert agent.fullclean("B", "Song", "Greatest Hits")[2] == "Greatest Hits"


def test_cleanup_compiles_without_warnings():
	with open(cleanup.__file__) as f:
		source = f.read()
	with warnings.catch_warnings():
		warnings.simplefilter("error")
		compile(source, cleanup.__file__, "exec")
