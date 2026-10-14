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


class FakeCollection:
    def __init__(self, docs: dict):
        self._docs = docs

    def document(self, key: str):
        return FakeDocument(self._docs, key)

    def stream(self):
        return iter([])


class FakeFirestore:
    """Collections are created on first use, like the real client."""

    def __init__(self):
        self.data: dict[str, dict] = {}

    def collection(self, name: str):
        return FakeCollection(self.data.setdefault(name, {}))


class BrokenFirestore:
    """Every operation fails, to exercise the fail-open paths."""

    def collection(self, name: str):
        raise RuntimeError("firestore unavailable")
