import warnings

import pytest

from maloja import cleanup


def phase2(reason):
	return pytest.mark.xfail(strict=True, reason=f"phase 2: {reason}")


# characterisation: pins current behaviour that is still an open decision
# (see _artifacts/2026-11-10_parser-and-rules-improvements.md, section 7)
PARSER_CASES = [
	pytest.param("A feat. B", "Song", ["A", "B"], "Song", id="split-feat"),
	pytest.param("A & B", "Song", ["A", "B"], "Song", id="split-ampersand"),
	pytest.param("A; B", "Song", ["A", "B"], "Song", id="split-formal"),
	pytest.param("Simon & Garfunkel", "The Sound of Silence", ["Garfunkel", "Simon"], "The Sound of Silence", id="char-band-ampersand"),
	pytest.param("AC/DC", "Back In Black", ["AC", "DC"], "Back In Black", id="char-band-slash"),
	pytest.param("Lady Gaga, Bradley Cooper", "Shallow", ["Lady Gaga, Bradley Cooper"], "Shallow", id="char-comma"),
	pytest.param("B & A", "Song", ["A", "B"], "Song", id="char-alphabetical-order"),
	pytest.param("Daft Punk", "Get Lucky (feat. Pharrell Williams)", ["Daft Punk", "Pharrell Williams"], "Get Lucky (feat. Pharrell Williams)", id="title-feat-kept"),
	pytest.param("Calvin Harris", "One Kiss (with Dua Lipa)", ["Calvin Harris", "Dua Lipa"], "One Kiss (with Dua Lipa)", id="title-with", marks=phase2("'with' as a featuring delimiter")),
	pytest.param("A", "Song (2011 Remaster)", ["A"], "Song", id="remaster-bracket"),
	pytest.param("The Beatles", "Let It Be - Remastered 2009", ["The Beatles"], "Let It Be", id="remaster-dash", marks=phase2("dash remaster suffix")),
	pytest.param("A", "Song - Radio Edit", ["A"], "Song", id="radio-edit-dash", marks=phase2("dash radio edit suffix")),
	pytest.param("A", "Song - Mono", ["A"], "Song", id="mono-dash", marks=phase2("dash mono suffix")),
	pytest.param("Nirvana", "Come As You Are - Live", ["Nirvana"], "Come As You Are - Live", id="live-kept"),
	pytest.param("A", "Song (X Remix)", ["A"], "Song (X Remix)", id="remix-kept"),
	pytest.param("A (ftX B)", "Song", ["A (ftX B)"], "Song", id="artist-delimiter-escaped"),
]


@pytest.mark.parametrize("artists_in,title_in,artists_out,title_out", PARSER_CASES)
def test_parser(agent, artists_in, title_in, artists_out, title_out):
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
		"Main", "Collab Song", ["Guest", "Main"], "Collab Song",
		id="fixartists-by-title",
	),
]


@pytest.mark.parametrize("rules,artists_in,title_in,artists_out,title_out", RULES_CASES)
def test_rules(agent_with_rules, rules, artists_in, title_in, artists_out, title_out):
	artists, title, _ = agent_with_rules(*rules).fullclean(artists_in, title_in, None)
	assert (artists, title) == (artists_out, title_out)


def test_cleanup_compiles_without_warnings():
	with open(cleanup.__file__) as f:
		source = f.read()
	with warnings.catch_warnings():
		warnings.simplefilter("error")
		compile(source, cleanup.__file__, "exec")
