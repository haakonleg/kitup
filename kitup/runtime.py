"""Per-run bundle runtime state."""

from dataclasses import dataclass, field

from .registry import Bundle, SideEffect
from .results import TaskResult


@dataclass(slots=True)
class BundleState:
    """Mutable state for one bundle during a single run."""

    bundle: Bundle
    results: dict[str, TaskResult] = field(default_factory=dict)
    pending_effects: list[SideEffect] = field(default_factory=list)
    deferred_effects: list[SideEffect] = field(default_factory=list)
    executed_effects: set[SideEffect] = field(default_factory=set)
    side_effects: dict[str, SideEffect] = field(default_factory=dict)


@dataclass(slots=True)
class RunState:
    """Runtime state for all bundles in one run."""

    bundles: dict[str, BundleState] = field(default_factory=dict)
    current: BundleState | None = None

    def for_bundle(self, bundle: Bundle) -> BundleState:
        """Return the state for ``bundle``, creating it on first use."""
        state = self.bundles.get(bundle.id)
        if state is None:
            state = BundleState(bundle)
            self.bundles[bundle.id] = state
        return state
