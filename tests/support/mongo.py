"""A motor-shaped collection, in memory.

Only the slice of the driver the repositories actually use. It records the
filters and update documents it was handed, which is the point: the Mongo
repositories are mostly query construction, and that is what can silently rot.
"""

from pymongo.errors import PyMongoError


class FakeCollection:
    def __init__(self, docs=None):
        self.docs: list[dict] = list(docs or [])
        self.calls: list[tuple] = []
        self.fail_with: Exception | None = None

    # --- helpers ---------------------------------------------------------

    def _boom(self):
        if self.fail_with:
            raise self.fail_with

    @staticmethod
    def _matches(doc, filt):
        return all(doc.get(k) == v for k, v in filt.items())

    def _find(self, filt):
        return [d for d in self.docs if self._matches(d, filt)]

    # --- the driver surface ----------------------------------------------

    async def insert_one(self, document):
        self._boom()
        self.calls.append(("insert_one", document))
        self.docs.append(dict(document))

        class _Result:
            inserted_id = document.get("_id")

        return _Result()

    async def insert_many(self, documents):
        self._boom()
        documents = list(documents)
        self.calls.append(("insert_many", documents))
        self.docs.extend(dict(d) for d in documents)

        class _Result:
            inserted_ids = [d.get("_id") for d in documents]

        return _Result()

    async def find_one(self, filt, *args, **kwargs):
        self._boom()
        self.calls.append(("find_one", filt))
        found = self._find(filt)
        return found[0] if found else None

    async def find_one_and_update(self, filt, update, **kwargs):
        self._boom()
        self.calls.append(("find_one_and_update", filt, update, kwargs))

        found = self._find(filt)

        if not found:
            if not kwargs.get("upsert"):
                return None
            doc = dict(filt)
            doc.update(update.get("$setOnInsert", {}))
            self.docs.append(doc)
            found = [doc]

        found[0].update(update.get("$set", {}))
        return found[0]

    async def count_documents(self, filt):
        self._boom()
        self.calls.append(("count_documents", filt))
        return len(self._find(filt))

    async def delete_many(self, filt):
        self._boom()
        self.calls.append(("delete_many", filt))
        gone = self._find(filt)
        self.docs = [d for d in self.docs if d not in gone]

        class _Result:
            deleted_count = len(gone)

        return _Result()

    def find(self, filt=None, projection=None):
        self._boom()
        self.calls.append(("find", filt, projection))
        return _Cursor(self._find(filt or {}))


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *args, **kwargs):
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    def skip(self, n):
        self.docs = self.docs[n:]
        return self

    def __aiter__(self):
        async def gen():
            for d in self.docs:
                yield d

        return gen()


class FakeMongoDb:
    """`db[name]` hands back the collection, which is all BaseModel needs."""

    def __init__(self, **collections):
        self.collections = {k: FakeCollection() for k in collections} or {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


def driver_failure(message="mongo is unreachable"):
    return PyMongoError(message)
