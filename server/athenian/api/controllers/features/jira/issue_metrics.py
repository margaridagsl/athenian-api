from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Sequence, Type

import numpy as np
import pandas as pd

from athenian.api.controllers.features.metric import Metric
from athenian.api.controllers.features.metric_calculator import AverageMetricCalculator, \
    BinnedEnsemblesCalculator, BinnedHistogramCalculator, HistogramCalculator, \
    HistogramCalculatorEnsemble, M, \
    make_register_metric, MetricCalculator, \
    MetricCalculatorEnsemble, RatioCalculator, \
    SumMetricCalculator
from athenian.api.controllers.miners.jira.issue import ISSUE_PRS_BEGAN, ISSUE_PRS_RELEASED
from athenian.api.models.metadata.jira import AthenianIssue, Issue
from athenian.api.models.web import JIRAMetricID
from athenian.api.tracing import sentry_span

metric_calculators: Dict[str, Type[MetricCalculator]] = {}
histogram_calculators: Dict[str, Type[HistogramCalculator]] = {}
register_metric = make_register_metric(metric_calculators, histogram_calculators)


class JIRAMetricCalculatorEnsemble(MetricCalculatorEnsemble):
    """MetricCalculatorEnsemble adapted for JIRA issues."""

    def __init__(self, *metrics: str, quantiles: Sequence[float]):
        """Initialize a new instance of JIRAMetricCalculatorEnsemble class."""
        super().__init__(*metrics, quantiles=quantiles, class_mapping=metric_calculators)


class JIRAHistogramCalculatorEnsemble(HistogramCalculatorEnsemble):
    """HistogramCalculatorEnsemble adapted for JIRA issues."""

    def __init__(self, *metrics: str, quantiles: Sequence[float]):
        """Initialize a new instance of JIRAHistogramCalculatorEnsemble class."""
        super().__init__(*metrics, quantiles=quantiles, class_mapping=histogram_calculators)


class JIRABinnedEnsemblesCalculator(BinnedEnsemblesCalculator[M]):
    """
    BinnedEnsemblesCalculator adapted for JIRA issues.

    We've got a completely custom __call__ method to avoid the redundant complexity of the parent.
    """

    @sentry_span
    def __call__(self,
                 items: pd.DataFrame,
                 time_intervals: Sequence[Sequence[datetime]],
                 groups: Sequence[np.ndarray],
                 agg_kwargs: Iterable[Dict[str, Any]],
                 ) -> List[List[List[List[List[M]]]]]:
        """
        Calculate the binned aggregations on a series of mined issues.

        :param items: pd.DataFrame with the fetched issues data.
        :param time_intervals: Time interval borders in UTC. Each interval spans \
                               `[time_intervals[i], time_intervals[i + 1]]`, the ending \
                               not included.
        :param groups: Various issue groups, the metrics will be calculated independently \
                       for each group.
        :param agg_kwargs: Keyword arguments to be passed to the ensemble aggregation.
        :return: ensembles x groups x time intervals primary x time intervals secondary x metrics.
        """
        min_times, max_times, ts_index_map = self._make_min_max_times(time_intervals)
        assert len(self.ensembles) == 1
        for ensemble in self.ensembles:
            ensemble(items, min_times, max_times, groups)
        values_dicts = self._aggregate_ensembles(agg_kwargs)
        result = [[[[[None] * len(metrics)
                     for _ in range(len(ts) - 1)]
                    for ts in time_intervals]
                   for _ in groups]
                  for metrics in self.metrics]
        for er, metrics, values_dict in zip(result, self.metrics, values_dicts):
            for mix, metric in enumerate(metrics):
                for gi in range(len(groups)):
                    for (primary, secondary), value in zip(ts_index_map, values_dict[metric][gi]):
                        er[gi][primary][secondary][mix] = value
        return result


class JIRABinnedMetricCalculator(JIRABinnedEnsemblesCalculator[Metric]):
    """
    JIRABinnedEnsemblesCalculator that calculates JIRA issue metrics.

    The number of ensembles is fixed to 1.
    """

    ensemble_class = JIRAMetricCalculatorEnsemble

    def __init__(self,
                 metrics: Sequence[str],
                 quantiles: Sequence[float],
                 **kwargs):
        """
        Initialize a new instance of `JIRABinnedMetricCalculator`.

        :param metrics: Sequence of metric names to calculate in each bin.
        :param quantiles: Pair of quantiles, common for each metric.
        """
        super().__init__([metrics], quantiles, **kwargs)

    def __call__(self,
                 items: pd.DataFrame,
                 time_intervals: Sequence[Sequence[datetime]],
                 groups: Sequence[np.ndarray],
                 ) -> List[List[List[List[M]]]]:
        """Extract the only ensemble's metrics from the parent's __call__."""
        return super().__call__(items, time_intervals, groups, [{}])[0]

    def _aggregate_ensembles(self, kwargs: Iterable[Dict[str, Any]],
                             ) -> List[Dict[str, List[List[Metric]]]]:
        return [self.ensembles[0].values()]


class JIRABinnedHistogramCalculator(JIRABinnedEnsemblesCalculator,
                                    BinnedHistogramCalculator):
    """JIRABinnedEnsemblesCalculator that calculates JIRA issue histograms."""

    ensemble_class = JIRAHistogramCalculatorEnsemble


@register_metric(JIRAMetricID.JIRA_RAISED)
class RaisedCounter(SumMetricCalculator[int]):
    """Number of created issues metric."""

    dtype = int

    def _analyze(self,
                 facts: pd.DataFrame,
                 min_times: np.ndarray,
                 max_times: np.ndarray,
                 **_) -> np.ndarray:
        result = np.full((len(min_times), len(facts)), None, object)
        created = facts[Issue.created.key].values
        result[(min_times[:, None] <= created) & (created < max_times[:, None])] = 1
        return result


@register_metric(JIRAMetricID.JIRA_RESOLVED)
class ResolvedCounter(SumMetricCalculator[int]):
    """Number of resolved issues metric."""

    dtype = int

    def _analyze(self,
                 facts: pd.DataFrame,
                 min_times: np.ndarray,
                 max_times: np.ndarray,
                 **_) -> np.ndarray:
        result = np.full((len(min_times), len(facts)), None, object)
        resolved = facts[AthenianIssue.resolved.key].values.astype(min_times.dtype)
        prs_began = facts[ISSUE_PRS_BEGAN].values.astype(min_times.dtype)
        released = facts[ISSUE_PRS_RELEASED].values.astype(min_times.dtype)[prs_began == prs_began]
        resolved[prs_began == prs_began][released != released] = np.datetime64("nat")
        result[(min_times[:, None] <= resolved) & (resolved < max_times[:, None])] = 1
        return result


@register_metric(JIRAMetricID.JIRA_OPEN)
class OpenCounter(SumMetricCalculator[int]):
    """Number of created issues metric."""

    dtype = int

    def _analyze(self,
                 facts: pd.DataFrame,
                 min_times: np.ndarray,
                 max_times: np.ndarray,
                 **_) -> np.ndarray:
        result = np.full((len(min_times), len(facts)), None, object)
        created = facts[Issue.created.key].values
        resolved = facts[AthenianIssue.resolved.key].values.astype(min_times.dtype)
        prs_began = facts[ISSUE_PRS_BEGAN].values.astype(min_times.dtype)
        released = facts[ISSUE_PRS_RELEASED].values.astype(min_times.dtype)[prs_began == prs_began]
        resolved[prs_began == prs_began][released != released] = np.datetime64("nat")
        not_resolved = resolved != resolved
        resolved_later = resolved >= max_times[:, None]
        created_earlier = created < max_times[:, None]
        result[(resolved_later | not_resolved) & created_earlier] = 1
        return result


@register_metric(JIRAMetricID.JIRA_RESOLUTION_RATE)
class ResolutionRateCalculator(RatioCalculator):
    """Calculate JIRA issues flow ratio = raised / resolved."""

    deps = (ResolvedCounter, RaisedCounter)


@register_metric(JIRAMetricID.JIRA_LIFE_TIME)
class LifeTimeCalculator(AverageMetricCalculator[timedelta]):
    """
    Issue Life Time calculator.

    Life Time is the time it takes for a ticket to go from the ticket creation to release.

    * If an issue is linked to PRs, the MTTR ends at the last PR release.
    * If an issue is not linked to any PR, the MTTR ends when the issue transition to the Done \
    status category.
    * If an issue is created after the work began, we consider the latter as the real ticket \
    creation.
    """

    may_have_negative_values = False
    dtype = "timedelta64[s]"

    def _analyze(self,
                 facts: pd.DataFrame,
                 min_times: np.ndarray,
                 max_times: np.ndarray,
                 **_) -> np.ndarray:
        result = np.full((len(min_times), len(facts)), None, object)
        created = facts[Issue.created.key].values
        prs_began = facts[ISSUE_PRS_BEGAN].values.astype(min_times.dtype)
        resolved = facts[Issue.resolved.key].values.astype(min_times.dtype)
        released = facts[ISSUE_PRS_RELEASED].values.astype(min_times.dtype)
        focus_mask = (min_times[:, None] <= resolved) & (resolved < max_times[:, None])
        life_times = np.maximum(released, resolved) - np.minimum(created, prs_began)
        nat = np.datetime64("nat")
        life_times[released != released] = nat
        life_times[resolved != resolved] = nat
        unmapped_mask = prs_began != prs_began
        life_times[unmapped_mask] = resolved[unmapped_mask] - created[unmapped_mask]
        empty_life_time_mask = life_times != life_times
        life_times = life_times.astype(self.dtype).view(int)
        result[:] = life_times
        result[:, empty_life_time_mask] = None
        result[~focus_mask] = None
        return result


@register_metric(JIRAMetricID.JIRA_LEAD_TIME)
class LeadTimeCalculator(AverageMetricCalculator[timedelta]):
    """
    Issue Lead Time calculator.

    Lead time is the time it takes for the changes to be released since the work on them started.

    * If an issue is linked to PRs, the MTTR ends at the last PR release.
    * If an issue is not linked to any PR, the MTTR ends when the issue transition to the Done
    status category.
    * The timestamp of work_began is min(issue became in progress, PR created).
    """

    may_have_negative_values = False
    dtype = "timedelta64[s]"

    def _analyze(self,
                 facts: pd.DataFrame,
                 min_times: np.ndarray,
                 max_times: np.ndarray,
                 **_) -> np.ndarray:
        result = np.full((len(min_times), len(facts)), None, object)
        work_began = facts[AthenianIssue.work_began.key].values
        prs_began = facts[ISSUE_PRS_BEGAN].values.astype(min_times.dtype)
        resolved = facts[Issue.resolved.key].values.astype(min_times.dtype)
        released = facts[ISSUE_PRS_RELEASED].values.astype(min_times.dtype)
        focus_mask = (min_times[:, None] <= resolved) & (resolved < max_times[:, None])
        lead_times = np.maximum(released, resolved) - np.minimum(work_began, prs_began)
        nat = np.datetime64("nat")
        lead_times[released != released] = nat
        lead_times[resolved != resolved] = nat
        unmapped_mask = prs_began != prs_began
        lead_times[unmapped_mask] = resolved[unmapped_mask] - work_began[unmapped_mask]
        empty_lead_time_mask = lead_times != lead_times
        lead_times = lead_times.astype(self.dtype).view(int)
        result[:] = lead_times
        result[:, empty_lead_time_mask] = None
        result[~focus_mask] = None
        return result
