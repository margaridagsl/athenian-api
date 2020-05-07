from datetime import date, datetime, timezone

import pytest

from athenian.api.controllers.features.entries import calc_pull_request_metrics_line_github
from athenian.api.controllers.features.github.pull_request_filter import filter_pull_requests
from athenian.api.controllers.miners.pull_request_list_item import ParticipationKind, Property
from athenian.api.models.web import PullRequestMetricID


@pytest.fixture(scope="module")
def time_from_to():
    time_from = datetime(year=2015, month=1, day=1, tzinfo=timezone.utc)
    time_to = datetime(year=2020, month=1, day=1, tzinfo=timezone.utc)
    return time_from, time_to


async def test_pr_list_miner_none(mdb, release_match_setting_tag, time_from_to):
    prs = list(await filter_pull_requests(
        [], *time_from_to, ["src-d/go-git"], release_match_setting_tag, {}, mdb, None))
    assert not prs


async def test_pr_list_miner_match_participants(mdb, release_match_setting_tag, time_from_to):
    participants = {ParticipationKind.AUTHOR: ["github.com/mcuadros", "github.com/smola"],
                    ParticipationKind.COMMENTER: ["github.com/mcuadros"]}
    prs = list(await filter_pull_requests(
        set(Property), *time_from_to, ["src-d/go-git"], release_match_setting_tag, participants,
        mdb, None))
    assert len(prs) == 320
    for pr in prs:
        mcuadros_is_author = "github.com/mcuadros" in pr.participants[ParticipationKind.AUTHOR]
        smola_is_author = "github.com/smola" in pr.participants[ParticipationKind.AUTHOR]
        mcuadros_is_only_commenter = (
            ("github.com/mcuadros" in pr.participants[ParticipationKind.COMMENTER])
            and  # noqa
            (not mcuadros_is_author)
            and  # noqa
            (not smola_is_author)
        )
        assert mcuadros_is_author or smola_is_author or mcuadros_is_only_commenter, str(pr)


@pytest.mark.parametrize("date_from, date_to", [(date(year=2018, month=1, day=1),
                                                 date(year=2019, month=1, day=1)),
                                                (date(year=2016, month=12, day=1),
                                                 date(year=2016, month=12, day=15)),
                                                (date(year=2016, month=11, day=17),
                                                 date(year=2016, month=12, day=1))])
async def test_pr_list_miner_match_metrics_all_count(mdb, release_match_setting_tag,
                                                     date_from, date_to):
    time_from = datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc)
    time_to = datetime.combine(date_to, datetime.min.time(), tzinfo=timezone.utc)
    prs = list(await filter_pull_requests(
        set(Property), time_from, time_to, ["src-d/go-git"], release_match_setting_tag,
        {}, mdb, None))
    assert prs
    metric = (await calc_pull_request_metrics_line_github(
        [PullRequestMetricID.PR_ALL_COUNT], [[time_from, time_to]],
        ["src-d/go-git"], release_match_setting_tag, [], mdb, None,
    ))[0][0][0]
    assert len(prs) == metric.value
