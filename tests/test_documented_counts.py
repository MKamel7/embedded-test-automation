"""Every test count the README claims must be the real one.

WHY THIS EXISTS, in the repository that already leads with a mutation score.
The mutant count is gated: `test_every_registered_mutant_has_a_search` compares
the case list against the registry, so "5 of 5 seeded defects killed" cannot be
rounded up by adding a mutant and forgetting to hunt it. The TEST count had no
such check, and it is the number in the diagram at the top of the README.

A sweep of the portfolio on 2026-08-31 found three stale counts in the two
repositories that had no gate, one of them eighty tests out of date inside a
safety argument, while the repository that had one stayed correct. The pattern
is not that some people are careless. It is that a number a human has to
remember to update is a number that will eventually be wrong.

WHY COLLECTION RATHER THAN A RUN. It is fast, needs no coverage threshold to be
met, and the count is exactly what the README claims. Collection does not
execute tests, so this cannot recurse. `--no-cov` because the configured
fail-under would otherwise make a bare collection exit non-zero.

Unlike the equivalent in `robot-arm-ik`, the count here does not depend on
which extras are installed: there are no optional dependency groups that change
what is collected, so one number is true everywhere.

A number that is deliberately historical should not be written as "N tests" at
all. Reword it rather than exempting it: an exemption is a second place for the
truth to live.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = ("README.md", "docs/TEST_STRATEGY.md")


def collected_tests() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-cov",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, check=False)
    found = re.search(r"^(\d+) tests collected", result.stdout, re.M)
    assert found is not None, (
        f"could not count the suite:\n{result.stdout[-1500:]}")
    return int(found.group(1))


@pytest.mark.parametrize("document", DOCUMENTS)
def test_every_stated_test_count_is_the_real_one(document: str) -> None:
    path = ROOT / document
    if not path.is_file():
        pytest.skip(f"{document} is not in this repository")

    actual = collected_tests()
    claimed = {int(n) for n in re.findall(r"(\d+)\s+tests\b",
                                          path.read_text(encoding="utf-8"))}

    wrong = sorted(n for n in claimed if n != actual)
    assert not wrong, (
        f"{document} says {wrong} tests; the suite collects {actual}. "
        f"Update the document, or reword the claim if it is historical.")


def test_the_readme_states_the_count_at_all() -> None:
    """A gate over a claim nobody makes passes for the wrong reason.

    If the README stopped quoting a suite size this file would go green forever
    while checking nothing, which is precisely the shape of test this
    repository exists to argue against.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert re.search(r"\d+\s+tests\b", readme), (
        "the README states no test count, so this gate guards nothing")
