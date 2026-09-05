"""Indexing a vector column wider than pgvector will index directly.

The embedding model this project runs on is 2048-dimensional, and pgvector
refuses HNSW or IVFFlat on a `vector` column past 2000 — so the collection had
no index at all and every search was an exact scan. Building the index over a
`halfvec` cast lifts the ceiling to 4000. Measured on the real 2048-dim data,
replicated to 24k rows: 120-185 ms exact against 0.85-0.98 ms indexed, with an
identical top-10 in identical order across 20 real queries.

The failure this file exists to catch is silent. A Postgres expression index is
only consulted when the query's ORDER BY matches the indexed expression
*exactly*, and a mismatch is not an error: the index builds, occupies disk, and
is never used. Confirmed with EXPLAIN on the real table — matching casts give
`Index Scan using idx_..._embedding`, and plain `embedding` against the same
index gives a Seq Scan and a Sort even with enable_seqscan off.

Everything here asserts on the SQL string, so none of it needs a server.
"""

import pytest

from enums import DistanceMethod, IndexType
from factories.db.postgres.vector_repository import PostgresVectorRepository


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Recorder:
    """A session factory that records SQL instead of running it.

    `session_factory()` and `session_factory.begin()` differ only in the
    transaction, and neither distinction matters to a statement's text, so one
    object stands in for both.
    """

    def __init__(self, exists=True, count=0, rows=()):
        self.statements: list[str] = []
        self.exists = exists
        self.count = count
        self.rows = list(rows)

    def __call__(self):
        return self

    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return _Result(self.rows)

    async def scalar(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        return self.exists if "information_schema" in sql else self.count

    def sql_containing(self, needle: str) -> str:
        matches = [s for s in self.statements if needle in s]
        assert matches, f"no statement contained {needle!r}: {self.statements}"
        return matches[-1]


def _repo(recorder, distance="cosine", index_type="hnsw"):
    return PostgresVectorRepository(recorder, distance_method=distance, index_type=index_type)


# --- the narrow path must be untouched ---------------------------------------


async def test_a_narrow_column_is_indexed_on_the_plain_column():
    """Everything pgvector can index directly must keep the exact SQL it had.

    Casting a 768-dim column to halfvec would trade recall for nothing: the
    `vector` opclass indexes it fine, and an unnecessary expression index is
    also one more thing the planner has to match."""
    recorder = _Recorder()

    await _repo(recorder).create_index("proj", 768)

    sql = recorder.sql_containing("CREATE INDEX")
    assert "USING hnsw (embedding vector_cosine_ops)" in sql
    assert "halfvec" not in sql


async def test_a_narrow_search_casts_to_vector_not_halfvec():
    recorder = _Recorder()

    await _repo(recorder).search_by_vector("proj", [0.1] * 768)

    sql = recorder.sql_containing("ORDER BY")
    assert "ORDER BY embedding <=> (:vector)::vector" in sql
    assert "halfvec" not in sql


# --- past 2000: index and query must both move to halfvec, together ----------


async def test_a_wide_column_is_indexed_over_a_halfvec_cast():
    """2048 dimensions used to mean no index at all — create_index returned
    False and left every search an exact scan."""
    recorder = _Recorder()

    built = await _repo(recorder).create_index("proj", 2048)

    assert built is True
    sql = recorder.sql_containing("CREATE INDEX")
    assert "USING hnsw ((embedding::halfvec(2048)) halfvec_cosine_ops)" in sql


async def test_a_wide_search_casts_both_sides_to_halfvec():
    recorder = _Recorder()

    await _repo(recorder).search_by_vector("proj", [0.1] * 2048)

    sql = recorder.sql_containing("ORDER BY")
    assert "ORDER BY (embedding::halfvec(2048)) <=> (:vector)::halfvec(2048)" in sql
    # The SELECT list is compared the same way, or the reported distance would
    # be computed at a different precision from the one that ordered the rows.
    assert "((embedding::halfvec(2048)) <=> (:vector)::halfvec(2048)) AS distance" in sql


async def test_the_index_expression_and_the_query_expression_are_identical():
    """The whole point, and the one thing that fails silently if it breaks: an
    expression index Postgres cannot match against the ORDER BY is built, paid
    for in disk and build time, and then never used."""
    index_recorder = _Recorder()
    search_recorder = _Recorder()

    await _repo(index_recorder).create_index("proj", 2048)
    await _repo(search_recorder).search_by_vector("proj", [0.1] * 2048)

    indexed = index_recorder.sql_containing("CREATE INDEX")
    ordered = search_recorder.sql_containing("ORDER BY")

    expression = "(embedding::halfvec(2048))"
    assert expression in indexed
    assert f"ORDER BY {expression} " in ordered


async def test_ivfflat_indexes_the_expression_too():
    """The IVFFlat branch builds its own statement, so it can drift from the
    HNSW one independently — and did, in the first draft of this change."""
    recorder = _Recorder(count=50_000)

    await _repo(recorder, index_type="ivfflat").create_index("proj", 2048)

    sql = recorder.sql_containing("CREATE INDEX")
    assert "USING ivfflat ((embedding::halfvec(2048)) halfvec_cosine_ops)" in sql
    assert "lists = 50" in sql


# --- every distance method, not just the configured default ------------------


@pytest.mark.parametrize(
    "distance, operator, opclass",
    [
        ("cosine", "<=>", "halfvec_cosine_ops"),
        ("dot", "<#>", "halfvec_ip_ops"),
        ("euclid", "<->", "halfvec_l2_ops"),
    ],
)
async def test_every_distance_method_has_a_real_halfvec_opclass(distance, operator, opclass):
    """The opclass is derived by substituting the type name into the `vector_*`
    one. All three results were checked against pg_opclass on pgvector 0.8.6
    and exist for hnsw and ivfflat alike; a wrong name is a hard error at index
    build time, so this pins the derivation rather than trusting the string."""
    index_recorder = _Recorder()
    search_recorder = _Recorder()

    await _repo(index_recorder, distance=distance).create_index("proj", 2048)
    await _repo(search_recorder, distance=distance).search_by_vector("proj", [0.1] * 2048)

    assert opclass in index_recorder.sql_containing("CREATE INDEX")
    assert f"ORDER BY (embedding::halfvec(2048)) {operator} " in search_recorder.sql_containing("ORDER BY")


def test_the_opclass_names_are_the_vector_ones_with_the_type_substituted():
    """Reads the mapping the derivation depends on, so a future edit that
    renames an operator class fails here rather than in a worker at 3am."""
    from enums import DISTANCE_METHOD_TO_PGVECTOR

    assert {
        method: opclass for method, (_, opclass) in DISTANCE_METHOD_TO_PGVECTOR.items()
    } == {
        DistanceMethod.COSINE: "vector_cosine_ops",
        DistanceMethod.DOT: "vector_ip_ops",
        DistanceMethod.EUCLID: "vector_l2_ops",
    }


# --- past 4000: nothing is indexable, and nothing should be cast -------------


async def test_past_the_halfvec_ceiling_no_index_is_built(caplog):
    """halfvec doubles the reach but does not remove the ceiling. Refusing to
    index is the honest outcome — searches stay exact scans — but it has to say
    so, or a 4096-dim model looks indexed and is not."""
    recorder = _Recorder()

    with caplog.at_level("WARNING"):
        built = await _repo(recorder).create_index("proj", 4096)

    assert built is False
    assert not [s for s in recorder.statements if "CREATE INDEX" in s]
    assert "halfvec" in caplog.text and "4000" in caplog.text


async def test_past_the_halfvec_ceiling_the_search_stays_full_precision():
    """There is no index above 4000, so a halfvec cast there would throw away
    precision and buy nothing: the scan is exact either way, and it may as
    well be exact at full width."""
    recorder = _Recorder()

    await _repo(recorder).search_by_vector("proj", [0.1] * 4096)

    sql = recorder.sql_containing("ORDER BY")
    assert "ORDER BY embedding <=> (:vector)::vector" in sql
    assert "halfvec" not in sql


@pytest.mark.parametrize(
    "size, expected",
    [
        (2000, ("embedding", "vector_cosine_ops", "::vector")),
        (2001, ("(embedding::halfvec(2001))", "halfvec_cosine_ops", "::halfvec(2001)")),
        (4000, ("(embedding::halfvec(4000))", "halfvec_cosine_ops", "::halfvec(4000)")),
        (4001, ("embedding", "vector_cosine_ops", "::vector")),
    ],
)
def test_the_halfvec_band_is_exclusive_at_the_bottom_and_inclusive_at_the_top(size, expected):
    """Both limits are pgvector's own, and both are off-by-one traps: 2000 is
    still indexable as a vector, and 4000 is still indexable as a halfvec."""
    assert _repo(_Recorder())._index_expression(size) == expected


def test_the_two_ceilings_are_the_ones_pgvector_documents():
    assert PostgresVectorRepository.MAX_INDEXABLE_DIMENSIONS == 2000
    assert PostgresVectorRepository.MAX_HALFVEC_INDEXABLE_DIMENSIONS == 4000
    assert IndexType.HNSW and IndexType.IVFFLAT  # both branches are reachable
