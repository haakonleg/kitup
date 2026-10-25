from __future__ import annotations

import pytest

from kitup.registry import Bundle


def test_depends_on_rejects_a_single_string():
    with pytest.raises(TypeError, match="bundle depends_on must be a sequence"):
        Bundle("base", depends_on="other")


def test_task_tags_reject_a_single_string():
    bundle = Bundle("base")

    with pytest.raises(TypeError, match="task tags must be a sequence"):
        bundle.task(tags="packages")
