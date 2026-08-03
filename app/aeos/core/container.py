"""
A tiny dependency-injection container.

Why not a framework? The engineering standard for this platform is "avoid
framework lock-in" and "prefer dependency injection". A ~100-line container
gives us constructor injection, singletons and factories without pulling in a
heavy DI framework, and it is trivially testable (swap any binding in a test).

Usage::

    container = Container()
    container.register_singleton(EventBus, InMemoryEventBus())
    container.register_factory(MemoryManager, lambda c: MemoryManager(c.resolve(EventBus)))
    bus = container.resolve(EventBus)
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Type, TypeVar

from .exceptions import ConfigurationError

T = TypeVar("T")


class Container:
    """Service locator + DI container (thread-safe)."""

    def __init__(self) -> None:
        self._singletons: Dict[Any, Any] = {}
        self._factories: Dict[Any, Callable[["Container"], Any]] = {}
        self._lazy_singletons: Dict[Any, Callable[["Container"], Any]] = {}
        self._lock = threading.RLock()

    def register_singleton(self, key: Type[T] | str, instance: T) -> None:
        """Bind an already-constructed instance to a key."""
        with self._lock:
            self._singletons[key] = instance

    def register_lazy_singleton(
        self, key: Type[T] | str, factory: Callable[["Container"], T]
    ) -> None:
        """Bind a factory that is invoked once, on first resolve."""
        with self._lock:
            self._lazy_singletons[key] = factory

    def register_factory(
        self, key: Type[T] | str, factory: Callable[["Container"], T]
    ) -> None:
        """Bind a factory invoked on *every* resolve (transient)."""
        with self._lock:
            self._factories[key] = factory

    def resolve(self, key: Type[T] | str) -> T:
        """Resolve a binding, constructing lazily as needed."""
        with self._lock:
            if key in self._singletons:
                return self._singletons[key]
            if key in self._lazy_singletons:
                instance = self._lazy_singletons[key](self)
                self._singletons[key] = instance
                return instance
            if key in self._factories:
                return self._factories[key](self)
        name = getattr(key, "__name__", str(key))
        raise ConfigurationError(f"No binding registered for '{name}'")

    def has(self, key: Type[T] | str) -> bool:
        with self._lock:
            return (
                key in self._singletons
                or key in self._lazy_singletons
                or key in self._factories
            )
