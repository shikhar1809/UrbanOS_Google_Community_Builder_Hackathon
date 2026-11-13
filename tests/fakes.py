"""A minimal in-memory stand-in for the Firestore client.

Covers only what the code under test uses: document get/set/create/delete and
``create`` raising AlreadyExists, which is the atomicity the idempotency claim
depends on.
"""

from __future__ import annotations

import copy
import uuid


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
        self.id = key

    def get(self, transaction=None):
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

    def __init__(self, docs: dict, orders=(), cap=None, filters=(), after=None):
        self._docs = docs
        self._orders = tuple(orders)
        self._cap = cap
        self._filters = tuple(filters)
        self._after = after

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self._docs, self._orders + ((field, direction),), self._cap, self._filters, self._after)

    def limit(self, cap):
        return FakeQuery(self._docs, self._orders, cap, self._filters, self._after)

    def start_after(self, values):
        return FakeQuery(self._docs, self._orders, self._cap, self._filters, values)

    def count(self):
        return FakeAggregationQuery(len(self._matching()))

    def _matching(self):
        return {k: v for k, v in self._docs.items()
                if all(_matches(v, f, o, val) for f, o, val in self._filters)}

    def _rows(self):
        rows = list(self._matching().items())

        def value(row, field):
            return row[0] if field == "__name__" else row[1].get(field)

        for field, direction in reversed(self._orders):
            rows = [row for row in rows if value(row, field) is not None]
            rows.sort(key=lambda row: value(row, field), reverse=direction == "DESCENDING")
        if self._after:
            cursor = tuple(v.id if isinstance(v, FakeDocument) else v for v in self._after.values())
            rows = [row for row in rows if tuple(value(row, f) for f, _ in self._orders) > cursor]
        if self._cap is not None:
            rows = rows[:self._cap]
        return rows

    def where(self, field=None, op=None, value=None, filter=None):
        if filter is not None:
            field, op, value = filter.field_path, filter.op_string, filter.value
        return FakeQuery(self._docs, self._orders, self._cap, self._filters + ((field, op, value),), self._after)

    def stream(self):
        return iter([FakeDocSnapshot(k, v, self._docs) for k, v in self._rows()])


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

    def set(self, ref, data):
        self.ops.append((ref, data, "set"))

    def delete(self, ref):
        self.ops.append((ref, None))

    def commit(self):
        for ref, data, *kind in self.ops:
            if data is None:
                ref.delete()
            elif kind:
                ref.set(data)
            else:
                ref.update(data)
        self.commits += 1
        self.ops = []


class FakeCollection(FakeQuery):
    def __init__(self, docs: dict):
        super().__init__(docs)

    def document(self, key: str = None):
        # Like the real client, no key means a generated id.
        return FakeDocument(self._docs, key or uuid.uuid4().hex[:20])

    def add(self, data):
        key = f"auto-{len(self._docs)}"
        self._docs[key] = copy.deepcopy(data)
        return None, FakeDocument(self._docs, key)


def _matches(doc, field, op, value):
    if op == "==":
        return doc.get(field) == value
    if op == ">":
        # Firestore excludes documents missing the field from a range query.
        return doc.get(field) is not None and doc.get(field) > value
    if op == ">=":
        return doc.get(field) is not None and doc.get(field) >= value
    raise NotImplementedError(f"Unsupported filter: {op}")


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
