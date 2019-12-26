from datetime import datetime
from typing import List, Mapping, Sequence, Tuple

from databases import Database

from athenian.api.controllers.features.github.pull_request import \
    BinnedPullRequestMetricCalculator, calculators as pull_request_calculators
from athenian.api.controllers.features.metric import Metric
from athenian.api.controllers.miners.github.pull_request import PullRequestTimesMiner


async def calc_metrics_line_func(
        metrics: Sequence[str], repos: Sequence[str], time_intervals: Sequence[datetime],
        db: Database) -> List[Tuple[Metric]]:
    """All the metric calculators must follow this call signature."""
    raise NotImplementedError


async def calc_pull_request_metrics_line_github(
        metrics: Sequence[str], repos: Sequence[str], time_intervals: Sequence[datetime],
        db: Database) -> List[Tuple[Metric]]:
    """Calculate pull request metrics on GitHub data."""
    miner = await PullRequestTimesMiner.mine(time_intervals[0], time_intervals[-1], repos, db)
    calcs = [pull_request_calculators[m]() for m in metrics]
    binned = BinnedPullRequestMetricCalculator(calcs, time_intervals)
    return binned(miner)


ENTRIES = {
    "github": {
        **{k: calc_pull_request_metrics_line_github for k in pull_request_calculators},
    },
}  # type: Mapping[str, Mapping[str, calc_metrics_line_func]]
