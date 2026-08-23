"""Both backends must implement the whole interface.

This is the point of having interfaces at all, and it was not true: the Mongo
repositories did not import the ABCs they claimed to implement, so seven
missing methods went unnoticed until the imports were repaired. An abstract
method left unimplemented is not a type error here, it is a TypeError the
moment the provider tries to build the repository — i.e. at first request.
"""

import inspect

import pytest

from factories.db import mongo, postgres

BACKENDS = {"mongo": mongo, "postgres": postgres}


def concrete_repositories(package):
    found = []
    for module in vars(package).values():
        if not inspect.ismodule(module):
            continue
        for name, obj in vars(module).items():
            if (inspect.isclass(obj)
                    and name.startswith(("Mongo", "Postgres", "Qdrant"))
                    and obj.__module__.startswith(package.__name__)):
                found.append(obj)
    return found


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_repository_is_instantiable(backend):
    package = BACKENDS[backend]
    repositories = concrete_repositories(package)

    assert repositories, f"no repositories discovered for {backend}"

    unimplemented = {
        cls.__name__: sorted(cls.__abstractmethods__)
        for cls in repositories
        if getattr(cls, "__abstractmethods__", None)
    }

    assert not unimplemented, (
        f"{backend} leaves interface methods unimplemented: {unimplemented}. "
        "Building the repository would raise TypeError at the first request."
    )
