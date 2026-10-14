"""A minimal in-memory stand-in for the Firestore client.

Covers only what the code under test uses: document get/set/create/delete and
``create`` raising AlreadyExists, which is the atomicity the idempotency claim
depends on.
"""

from __future__ import annotations

import copy


class AlreadyExists(Exception):
    """Mirrors google.api_core.exceptions.AlreadyExists by class name, which is
    how idempotency.claim distinguishes a taken claim from a broken store."""


class FakeSnapshot:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class FakeDocument:
    def __init__(self, store: dict, key: str):
        self._store = store
        self._key = key

    def get(self):
        return FakeSnapshot(self._store.get(self._key))

    def create(self, data):
        if self._key in self._store:
            raise AlreadyExists(self._key)
        self._store[self._key] = copy.deepcopy(data)

    def set(self, data, merge: bool = False):
        if merge and self._key in self._store:
            self._store[self._key].update(copy.deepcopy(data))
        else:
            self._store[self._key] = copy.deepcopy(data)

    def update(self, data):
        self._store.setdefault(self._key, {}).update(copy.deepcopy(data))

    def delete(self):
        self._store.pop(self._key, None)


class FakeAggregation:
    def __init__(self, value):
        self.value = value


class FakeQuery:
    """Supports the slice of the query API the snapshot uses: order_by, limit,
    count and stream. Ordering drops documents missing the field, exactly as
    Firestore does -- that behaviour is the point of one of the tests."""

    def __init__(self, docs: dict, order_field=None, descending=False, cap=None, backing=None):
        self._docs = docs
        self._order_field = order_field
        self._descending = descending
        self._cap = cap
        # Writes must land in the real collection even when reading a filtered view.
        self._backing = backing if backing is not None else docs

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self._docs, field, direction == "DESCENDING", self._cap, self._backing)

    def limit(self, cap):
        return FakeQuery(self._docs, self._order_field, self._descending, cap, self._backing)

    def count(self):
        return FakeAggregationQuery(len(self._docs))

    def _rows(self):
        rows = [(k, v) for k, v in self._docs.items()]
        if self._order_field:
            rows = [r for r in rows if r[1].get(self._order_field) is not None]
            rows.sort(key=lambda r: r[1][self._order_field], reverse=self._descending)
        if self._cap is not None:
            rows = rows[: self._cap]
        return rows

    def where(self, field=None, op=None, value=None, filter=None):
        if filter is not None:
            field, op, value = filter.field_path, filter.op_string, filter.value
        rows = {k: v for k, v in self._docs.items() if _matches(v, field, op, value)}
        return FakeQuery(rows, self._order_field, self._descending, self._cap, self._docs)

    def stream(self):
        return iter([FakeDocSnapshot(k, v, self._backing) for k, v in self._rows()])


class FakeAggregationQuery:
    def __init__(self, value):
        self._value = value

    def get(self):
        return [[FakeAggregation(self._value)]]


class FakeDocSnapshot:
    def __init__(self, doc_id, data, store=None):
        self.id = doc_id
        self._data = data
        self.exists = True
        self.reference = FakeDocument(store, doc_id) if store is not None else None

    def to_dict(self):
        return copy.deepcopy(self._data)


class FakeBatch:
    """Records writes and applies them on commit, like a Firestore WriteBatch."""

    def __init__(self):
        self.ops = []
        self.commits = 0

    def update(self, ref, data):
        self.ops.append((ref, data))

    def commit(self):
        for ref, data in self.ops:
            ref.update(data)
        self.commits += 1
        self.ops = []


class FakeCollection(FakeQuery):
    def __init__(self, docs: dict):
        super().__init__(docs)

    def document(self, key: str):
        return FakeDocument(self._docs, key)

    def add(self, data):
        key = f"auto-{len(self._docs)}"
        self._docs[key] = copy.deepcopy(data)
        return None, FakeDocument(self._docs, key)


def _matches(doc, field, op, value):
    if op != "==":
        raise NotImplementedError(f"fake only supports ==, got {op}")
    return doc.get(field) == value


class FakeFirestore:
    """Collections are created on first use, like the real client."""

    def __init__(self):
        self.data: dict[str, dict] = {}
        self.batches = []

    def collection(self, name: str):
        return FakeCollection(self.data.setdefault(name, {}))

    def batch(self):
        b = FakeBatch()
        self.batches.append(b)
        return b


class BrokenFirestore:
    """Every operation fails, to exercise the fail-open paths."""

    def collection(self, name: str):
        raise RuntimeError("firestore unavailable")
