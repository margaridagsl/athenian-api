import dataclasses
from datetime import datetime, timedelta, timezone
import math
import pickle
from typing import Sequence
import uuid

import pandas as pd
import pytest
from sqlalchemy import and_, select

from athenian.api.async_read_sql_query import read_sql_query
from athenian.api.controllers.features.entries import calc_pull_request_facts_github
from athenian.api.controllers.miners.filters import JIRAFilter, LabelFilter
from athenian.api.controllers.miners.github.precomputed_prs import discover_unreleased_prs, \
    load_inactive_merged_unreleased_prs, load_precomputed_done_candidates, \
    load_precomputed_done_facts_filters, load_precomputed_done_facts_reponums, \
    load_precomputed_pr_releases, store_merged_unreleased_pull_request_facts, \
    store_precomputed_done_facts, update_unreleased_prs
from athenian.api.controllers.miners.github.release import load_releases, map_prs_to_releases
from athenian.api.controllers.miners.github.released_pr import matched_by_column, \
    new_released_prs_df
from athenian.api.controllers.miners.types import Fallback, MinedPullRequest, ParticipationKind, \
    PullRequestFacts
from athenian.api.controllers.settings import ReleaseMatch, ReleaseMatchSetting
from athenian.api.defer import wait_deferred, with_defer
from athenian.api.models.metadata.github import PullRequest, PullRequestCommit, Release
from athenian.api.models.precomputed.models import GitHubMergedPullRequestFacts


def gen_dummy_df(dt: datetime) -> pd.DataFrame:
    return pd.DataFrame.from_records(
        [["xxx", dt, dt]], columns=["user_login", "created_at", "submitted_at"])


async def test_load_store_precomputed_done_smoke(pdb, pr_samples):
    samples = pr_samples(200)  # type: Sequence[PullRequestFacts]
    for i in range(1, 6):
        # merged but unreleased
        kwargs = dataclasses.asdict(samples[-i])
        kwargs["released"] = Fallback(None, None)
        samples[-i] = PullRequestFacts(**kwargs)
    for i in range(6, 11):
        # rejected
        kwargs = dataclasses.asdict(samples[-i])
        kwargs["released"] = kwargs["merged"] = Fallback(None, None)
        samples[-i] = PullRequestFacts(**kwargs)
    names = ["one", "two", "three"]
    settings = {"github.com/" + k: ReleaseMatchSetting("{{default}}", ".*", ReleaseMatch(i))
                for i, k in enumerate(names)}
    default_branches = {k: "master" for k in names}
    prs = [MinedPullRequest(
        pr={PullRequest.created_at.key: s.created.best,
            PullRequest.repository_full_name.key: names[i % len(names)],
            PullRequest.user_login.key: "xxx",
            PullRequest.merged_by_login.key: "yyy",
            PullRequest.number.key: i + 1,
            PullRequest.node_id.key: uuid.uuid4().hex},
        release={matched_by_column: settings["github.com/" + names[i % len(names)]].match % 2,
                 Release.author.key: "zzz",
                 Release.url.key: "https://release",
                 Release.id.key: "MD%d" % i},
        comments=gen_dummy_df(s.first_comment_on_first_review.best),
        commits=pd.DataFrame.from_records(
            [["zzz", "zzz", s.first_commit.best]],
            columns=[
                PullRequestCommit.committer_login.key,
                PullRequestCommit.author_login.key,
                PullRequestCommit.committed_date.key,
            ],
        ),
        reviews=gen_dummy_df(s.first_comment_on_first_review.best),
        review_comments=gen_dummy_df(s.first_comment_on_first_review.best),
        review_requests=gen_dummy_df(s.first_review_request.best),
        labels=pd.DataFrame.from_records(([["bug"]], [["feature"]])[i % 2], columns=["name"]),
        jiras=pd.DataFrame(),
    ) for i, s in enumerate(samples)]
    await store_precomputed_done_facts(prs, samples, default_branches, settings, pdb)
    # we should not crash on repeat
    await store_precomputed_done_facts(prs, samples, default_branches, settings, pdb)
    released_ats = sorted((t.released.best, i) for i, t in enumerate(samples[:-10]))
    time_from = released_ats[len(released_ats) // 2][0]
    time_to = released_ats[-1][0]
    n = len(released_ats) - len(released_ats) // 2 + \
        sum(1 for s in samples[-10:-5] if s.closed.best >= time_from)
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, names, {}, LabelFilter.empty(), default_branches, False, settings, pdb)
    assert len(loaded_prs) == n
    true_prs = {prs[i].pr[PullRequest.node_id.key]: samples[i] for _, i in released_ats[-n:]}
    for i, s in enumerate(samples[-10:-5]):
        if s.closed.best >= time_from:
            true_prs[prs[-10 + i].pr[PullRequest.node_id.key]] = s
    diff_keys = set(loaded_prs) - set(true_prs)
    assert not diff_keys
    for k, load_value in loaded_prs.items():
        assert load_value == true_prs[k], k


async def test_load_store_precomputed_done_filters(pr_samples, pdb):
    samples = pr_samples(102)  # type: Sequence[PullRequestFacts]
    names = ["one", "two", "three"]
    settings = {"github.com/" + k: ReleaseMatchSetting("{{default}}", ".*", ReleaseMatch(i))
                for i, k in enumerate(names)}
    default_branches = {k: "master" for k in names}
    prs = [MinedPullRequest(
        pr={PullRequest.created_at.key: s.created.best,
            PullRequest.repository_full_name.key: names[i % len(names)],
            PullRequest.user_login.key: ["xxx", "wow"][i % 2],
            PullRequest.merged_by_login.key: "yyy",
            PullRequest.number.key: i + 1,
            PullRequest.node_id.key: uuid.uuid4().hex},
        release={matched_by_column: settings["github.com/" + names[i % len(names)]].match % 2,
                 Release.author.key: ["foo", "zzz"][i % 2],
                 Release.url.key: "https://release",
                 Release.id.key: "MD%d" % i},
        comments=gen_dummy_df(s.first_comment_on_first_review.best),
        commits=pd.DataFrame.from_records(
            [["yyy", "yyy", s.first_commit.best]],
            columns=[
                PullRequestCommit.committer_login.key,
                PullRequestCommit.author_login.key,
                PullRequestCommit.committed_date.key,
            ],
        ),
        reviews=gen_dummy_df(s.first_comment_on_first_review.best),
        review_comments=gen_dummy_df(s.first_comment_on_first_review.best),
        review_requests=gen_dummy_df(s.first_review_request.best),
        labels=pd.DataFrame.from_records(([["bug"]],
                                          [["feature"]],
                                          [["bug"], ["bad"]],
                                          [["feature"], ["bad"]])[i % 4], columns=["name"]),
        jiras=pd.DataFrame(),
    ) for i, s in enumerate(samples)]
    await store_precomputed_done_facts(prs, samples, default_branches, settings, pdb)
    time_from = min(s.created.best for s in samples)
    time_to = max(s.max_timestamp() for s in samples)
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, ["one"], {}, LabelFilter.empty(), default_branches,
        False, settings, pdb)
    assert set(loaded_prs) == {pr.pr[PullRequest.node_id.key] for pr in prs[::3]}
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, names, {ParticipationKind.AUTHOR: {"wow"},
                                    ParticipationKind.RELEASER: {"zzz"}},
        LabelFilter.empty(), default_branches, False, settings, pdb)
    assert set(loaded_prs) == {pr.pr[PullRequest.node_id.key] for pr in prs[1::2]}
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, names, {ParticipationKind.COMMIT_AUTHOR: {"yyy"}}, LabelFilter.empty(),
        default_branches, False, settings, pdb)
    assert len(loaded_prs) == len(prs)
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, names, {}, LabelFilter({"bug", "xxx"}, set()),
        default_branches, False, settings, pdb)
    assert len(loaded_prs) == len(prs) / 2
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, names, {}, LabelFilter({"bug"}, {"bad"}),
        default_branches, False, settings, pdb)
    assert len(loaded_prs) == int(math.ceil(len(prs) / 4.0))


async def test_load_store_precomputed_done_match_by(pr_samples, default_branches, pdb):
    samples, prs, settings = _gen_one_pr(pr_samples)
    await store_precomputed_done_facts(prs, samples, default_branches, settings, pdb)
    time_from = samples[0].created.best - timedelta(days=365)
    time_to = samples[0].released.best + timedelta(days=1)
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, ["src-d/go-git"], {}, [], default_branches, False, settings, pdb)
    assert len(loaded_prs) == 1
    settings = {
        "github.com/src-d/go-git": ReleaseMatchSetting("master", ".*", ReleaseMatch.branch),
    }
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, ["src-d/go-git"], {}, LabelFilter.empty(), default_branches,
        False, settings, pdb)
    assert len(loaded_prs) == 1
    settings = {
        "github.com/src-d/go-git": ReleaseMatchSetting("nope", ".*", ReleaseMatch.tag_or_branch),
    }
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, ["src-d/go-git"], {}, LabelFilter.empty(), default_branches, False,
        settings, pdb)
    assert len(loaded_prs) == 0
    settings = {
        "github.com/src-d/go-git": ReleaseMatchSetting("{{default}}", ".*", ReleaseMatch.tag),
    }
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, ["src-d/go-git"], {}, LabelFilter.empty(), default_branches, False,
        settings, pdb)
    assert len(loaded_prs) == 0
    prs[0].release[matched_by_column] = 1
    await store_precomputed_done_facts(prs, samples, default_branches, settings, pdb)
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, ["src-d/go-git"], {}, LabelFilter.empty(), default_branches, False,
        settings, pdb)
    assert len(loaded_prs) == 1
    settings = {
        "github.com/src-d/go-git": ReleaseMatchSetting("{{default}}", "xxx", ReleaseMatch.tag),
    }
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, ["src-d/go-git"], {}, LabelFilter.empty(), default_branches, False,
        settings, pdb)
    assert len(loaded_prs) == 0


async def test_load_store_precomputed_done_exclude_inactive(pr_samples, default_branches, pdb):
    while True:
        samples = pr_samples(2)  # type: Sequence[PullRequestFacts]
        samples = sorted(samples, key=lambda s: s.first_comment_on_first_review.best)
        deltas = [(samples[1].first_comment_on_first_review.best -
                   samples[0].first_comment_on_first_review.best),
                  samples[0].first_comment_on_first_review.best - samples[1].created.best,
                  samples[1].created.best - samples[0].created.best]
        if all(d > timedelta(days=2) for d in deltas):
            break
    settings = {"github.com/one": ReleaseMatchSetting("{{default}}", ".*", ReleaseMatch.tag)}
    prs = [MinedPullRequest(
        pr={PullRequest.created_at.key: s.created.best,
            PullRequest.repository_full_name.key: "one",
            PullRequest.user_login.key: "xxx",
            PullRequest.merged_by_login.key: "yyy",
            PullRequest.number.key: 777,
            PullRequest.node_id.key: uuid.uuid4().hex},
        release={matched_by_column: settings["github.com/one"].match,
                 Release.author.key: "zzz",
                 Release.url.key: "https://release",
                 Release.id.key: "MDwhatever="},
        comments=gen_dummy_df(s.first_comment_on_first_review.best),
        commits=pd.DataFrame.from_records(
            [["yyy", "yyy", s.first_comment_on_first_review.best]],
            columns=[
                PullRequestCommit.committer_login.key,
                PullRequestCommit.author_login.key,
                PullRequestCommit.committed_date.key,
            ],
        ),
        reviews=gen_dummy_df(s.first_comment_on_first_review.best),
        review_comments=gen_dummy_df(s.first_comment_on_first_review.best),
        review_requests=gen_dummy_df(s.first_comment_on_first_review.best),
        labels=pd.DataFrame.from_records([["bug"]], columns=["name"]),
        jiras=pd.DataFrame(),
    ) for s in samples]
    await store_precomputed_done_facts(prs, samples, default_branches, settings, pdb)
    time_from = samples[1].created.best + timedelta(days=1)
    time_to = samples[0].first_comment_on_first_review.best
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, ["one"], {}, LabelFilter.empty(), default_branches,
        True, settings, pdb)
    assert len(loaded_prs) == 1
    assert loaded_prs[prs[0].pr[PullRequest.node_id.key]] == samples[0]
    time_from = samples[1].created.best - timedelta(days=1)
    time_to = samples[1].created.best + timedelta(seconds=1)
    loaded_prs = await load_precomputed_done_facts_filters(
        time_from, time_to, ["one"], {}, LabelFilter.empty(), default_branches,
        True, settings, pdb)
    assert len(loaded_prs) == 1
    assert loaded_prs[prs[1].pr[PullRequest.node_id.key]] == samples[1]


async def test_load_precomputed_done_times_reponums_smoke(pr_samples, pdb):
    samples = pr_samples(12)  # type: Sequence[PullRequestFacts]
    names = ["one", "two", "three"]
    settings = {"github.com/" + k: ReleaseMatchSetting("{{default}}", ".*", ReleaseMatch(i))
                for i, k in enumerate(names)}
    default_branches = {k: "master" for k in names}
    prs = [MinedPullRequest(
        pr={PullRequest.created_at.key: s.created.best,
            PullRequest.repository_full_name.key: names[i % len(names)],
            PullRequest.user_login.key: ["xxx", "wow"][i % 2],
            PullRequest.merged_by_login.key: "yyy",
            PullRequest.number.key: i + 1,
            PullRequest.node_id.key: uuid.uuid4().hex},
        release={matched_by_column: settings["github.com/" + names[i % len(names)]].match % 2,
                 Release.author.key: ["foo", "zzz"][i % 2],
                 Release.url.key: "https://release",
                 Release.id.key: "MD%d" % i},
        comments=gen_dummy_df(s.first_comment_on_first_review.best),
        commits=pd.DataFrame.from_records(
            [["yyy", "yyy", s.first_commit.best]],
            columns=[
                PullRequestCommit.committer_login.key,
                PullRequestCommit.author_login.key,
                PullRequestCommit.committed_date.key,
            ],
        ),
        reviews=gen_dummy_df(s.first_comment_on_first_review.best),
        review_comments=gen_dummy_df(s.first_comment_on_first_review.best),
        review_requests=gen_dummy_df(s.first_review_request.best),
        labels=pd.DataFrame.from_records(([["bug"]], [["feature"]])[i % 2], columns=["name"]),
        jiras=pd.DataFrame(),
    ) for i, s in enumerate(samples)]
    await store_precomputed_done_facts(prs, samples, default_branches, settings, pdb)
    query1 = {"one": {pr.pr[PullRequest.number.key] for pr in prs
                      if pr.pr[PullRequest.repository_full_name.key] == "one"}}
    assert len(query1["one"]) == 4
    new_prs = await load_precomputed_done_facts_reponums(query1, default_branches, settings, pdb)
    assert new_prs == {pr.pr[PullRequest.node_id.key]: s
                       for pr, s in zip(prs, samples)
                       if pr.pr[PullRequest.repository_full_name.key] == "one"}
    query2 = {"one": set()}
    new_prs = await load_precomputed_done_facts_reponums(query2, default_branches, settings, pdb)
    assert len(new_prs) == 0
    query3 = {"one": {100500}}
    new_prs = await load_precomputed_done_facts_reponums(query3, default_branches, settings, pdb)
    assert len(new_prs) == 0


def _gen_one_pr(pr_samples):
    samples = pr_samples(1)  # type: Sequence[PullRequestFacts]
    s = samples[0]
    settings = {
        "github.com/src-d/go-git": ReleaseMatchSetting(
            "{{default}}", ".*", ReleaseMatch.tag_or_branch),
    }
    prs = [MinedPullRequest(
        pr={PullRequest.created_at.key: s.created.best,
            PullRequest.repository_full_name.key: "src-d/go-git",
            PullRequest.user_login.key: "xxx",
            PullRequest.merged_by_login.key: "yyy",
            PullRequest.number.key: 777,
            PullRequest.node_id.key: uuid.uuid4().hex},
        release={matched_by_column: ReleaseMatch.branch,
                 Release.author.key: "zzz",
                 Release.url.key: "https://release",
                 Release.id.key: "MDwhatever="},
        comments=gen_dummy_df(s.first_comment_on_first_review.best),
        commits=pd.DataFrame.from_records(
            [["zzz", "zzz", s.first_commit.best]],
            columns=[
                PullRequestCommit.committer_login.key,
                PullRequestCommit.author_login.key,
                PullRequestCommit.committed_date.key,
            ],
        ),
        reviews=gen_dummy_df(s.first_comment_on_first_review.best),
        review_comments=gen_dummy_df(s.first_comment_on_first_review.best),
        review_requests=gen_dummy_df(s.first_review_request.best),
        labels=pd.DataFrame.from_records([["bug"]], columns=["name"]),
        jiras=pd.DataFrame(),
    )]
    return samples, prs, settings


async def test_store_precomputed_done_facts_empty(pdb):
    await store_precomputed_done_facts([], [], None, None, pdb)


async def test_load_precomputed_done_candidates_smoke(pr_samples, default_branches, pdb):
    samples, prs, settings = _gen_one_pr(pr_samples)
    await store_precomputed_done_facts(prs, samples, default_branches, settings, pdb)
    time_from = samples[0].created.best
    time_to = samples[0].released.best
    loaded_prs = await load_precomputed_done_candidates(
        time_from, time_to, ["one"], {"one": "master"}, settings, pdb)
    assert len(loaded_prs) == 0
    loaded_prs = await load_precomputed_done_candidates(
        time_from, time_to, ["src-d/go-git"], default_branches, settings, pdb)
    assert loaded_prs == {prs[0].pr[PullRequest.node_id.key]}
    loaded_prs = await load_precomputed_done_candidates(
        time_from, time_from, ["src-d/go-git"], default_branches, settings, pdb)
    assert len(loaded_prs) == 0


@with_defer
async def test_load_precomputed_pr_releases_smoke(pr_samples, default_branches, pdb, cache):
    samples, prs, settings = _gen_one_pr(pr_samples)
    await store_precomputed_done_facts(prs, samples, default_branches, settings, pdb)
    for i in range(2):
        released_prs = await load_precomputed_pr_releases(
            [pr.pr[PullRequest.node_id.key] for pr in prs],
            max(s.released.best for s in samples) + timedelta(days=1),
            {pr.pr[PullRequest.repository_full_name.key]: ReleaseMatch.branch for pr in prs},
            default_branches, settings, pdb if i == 0 else None, cache)
        await wait_deferred()
        for s, pr in zip(samples, prs):
            rpr = released_prs.loc[pr.pr[PullRequest.node_id.key]]
            for col in (Release.author.key, Release.url.key, Release.id.key, matched_by_column):
                assert rpr[col] == pr.release[col], i
            assert rpr[Release.published_at.key] == s.released.best, i
            assert rpr[Release.repository_full_name.key] == \
                pr.pr[PullRequest.repository_full_name.key], i


async def test_load_precomputed_pr_releases_time_to(pr_samples, default_branches, pdb):
    samples, prs, settings = _gen_one_pr(pr_samples)
    await store_precomputed_done_facts(prs, samples, default_branches, settings, pdb)
    released_prs = await load_precomputed_pr_releases(
        [pr.pr[PullRequest.node_id.key] for pr in prs],
        min(s.released.best for s in samples),
        {pr.pr[PullRequest.repository_full_name.key]: ReleaseMatch.branch for pr in prs},
        default_branches, settings, pdb, None)
    assert released_prs.empty


async def test_load_precomputed_pr_releases_release_mismatch(pr_samples, default_branches, pdb):
    samples, prs, settings = _gen_one_pr(pr_samples)
    await store_precomputed_done_facts(prs, samples, default_branches, settings, pdb)
    released_prs = await load_precomputed_pr_releases(
        [pr.pr[PullRequest.node_id.key] for pr in prs],
        max(s.released.best for s in samples) + timedelta(days=1),
        {pr.pr[PullRequest.repository_full_name.key]: ReleaseMatch.tag for pr in prs},
        default_branches, settings, pdb, None)
    assert released_prs.empty
    released_prs = await load_precomputed_pr_releases(
        [pr.pr[PullRequest.node_id.key] for pr in prs],
        max(s.released.best for s in samples) + timedelta(days=1),
        {pr.pr[PullRequest.repository_full_name.key]: ReleaseMatch.branch for pr in prs},
        {"src-d/go-git": "xxx"}, settings, pdb, None)
    assert released_prs.empty


async def test_load_precomputed_pr_releases_tag(pr_samples, default_branches, pdb):
    samples, prs, settings = _gen_one_pr(pr_samples)
    prs[0].release[matched_by_column] = ReleaseMatch.tag
    await store_precomputed_done_facts(prs, samples, default_branches, settings, pdb)
    released_prs = await load_precomputed_pr_releases(
        [pr.pr[PullRequest.node_id.key] for pr in prs],
        max(s.released.best for s in samples) + timedelta(days=1),
        {pr.pr[PullRequest.repository_full_name.key]: ReleaseMatch.tag for pr in prs},
        {}, settings, pdb, None)
    assert len(released_prs) == len(prs)
    released_prs = await load_precomputed_pr_releases(
        [pr.pr[PullRequest.node_id.key] for pr in prs],
        max(s.released.best for s in samples) + timedelta(days=1),
        {pr.pr[PullRequest.repository_full_name.key]: ReleaseMatch.tag for pr in prs},
        {}, {"github.com/src-d/go-git": ReleaseMatchSetting(
            tags="v.*", branches="", match=ReleaseMatch.tag),
        }, pdb, None)
    assert released_prs.empty


async def test_discover_update_unreleased_prs_smoke(
        mdb, pdb, default_branches, release_match_setting_tag):
    prs = await read_sql_query(
        select([PullRequest]).where(and_(PullRequest.number.in_(range(1000, 1010)),
                                         PullRequest.merged_at.isnot(None))),
        mdb, PullRequest, index=PullRequest.node_id.key)
    prs[prs[PullRequest.merged_at.key].isnull()] = datetime.now(tz=timezone.utc)
    utc = timezone.utc
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], None, default_branches,
        datetime(2018, 9, 1, tzinfo=utc),
        datetime(2018, 11, 1, tzinfo=utc),
        release_match_setting_tag,
        mdb, pdb, None)
    assert len(releases) == 2
    assert matched_bys == {"src-d/go-git": ReleaseMatch.tag}
    empty_rdf = new_released_prs_df()
    await update_unreleased_prs(
        prs, empty_rdf, datetime(2018, 11, 1, tzinfo=utc), {},
        matched_bys, default_branches, release_match_setting_tag, pdb)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], None, default_branches,
        datetime(2018, 11, 1, tzinfo=utc),
        datetime(2018, 11, 20, tzinfo=utc),
        release_match_setting_tag,
        mdb, pdb, None)
    assert len(releases) == 1
    assert matched_bys == {"src-d/go-git": ReleaseMatch.tag}
    await update_unreleased_prs(
        prs, empty_rdf, datetime(2018, 11, 20, tzinfo=utc), {},
        matched_bys, default_branches, release_match_setting_tag, pdb)
    unreleased_prs = await discover_unreleased_prs(
        prs, datetime(2018, 11, 20, tzinfo=utc), matched_bys, default_branches,
        release_match_setting_tag, pdb)
    assert set(prs.index) == set(unreleased_prs)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], None, default_branches,
        datetime(2018, 9, 1, tzinfo=utc),
        datetime(2018, 11, 1, tzinfo=utc),
        release_match_setting_tag,
        mdb, pdb, None)
    assert matched_bys == {"src-d/go-git": ReleaseMatch.tag}
    unreleased_prs = await discover_unreleased_prs(
        prs, datetime(2018, 11, 1, tzinfo=utc), matched_bys, default_branches,
        {"github.com/src-d/go-git": ReleaseMatchSetting(
            branches="", tags="v.*", match=ReleaseMatch.tag)},
        pdb)
    assert len(unreleased_prs) == 0
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], None, default_branches,
        datetime(2019, 1, 29, tzinfo=utc),
        datetime(2019, 2, 1, tzinfo=utc),
        release_match_setting_tag,
        mdb, pdb, None)
    assert len(releases) == 2
    assert matched_bys == {"src-d/go-git": ReleaseMatch.tag}
    unreleased_prs = await discover_unreleased_prs(
        prs, datetime(2019, 2, 1, tzinfo=utc), matched_bys, default_branches,
        release_match_setting_tag, pdb)
    assert len(unreleased_prs) == 0


@with_defer
async def test_discover_update_unreleased_prs_released(
        mdb, pdb, dag, default_branches, release_match_setting_tag):
    prs = await read_sql_query(
        select([PullRequest]).where(and_(PullRequest.number.in_(range(1000, 1010)),
                                         PullRequest.merged_at.isnot(None))),
        mdb, PullRequest, index=PullRequest.node_id.key)
    prs[prs[PullRequest.merged_at.key].isnull()] = datetime.now(tz=timezone.utc)
    utc = timezone.utc
    time_from = datetime(2018, 10, 1, tzinfo=utc)
    time_to = datetime(2018, 12, 1, tzinfo=utc)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], None, default_branches,
        time_from,
        time_to,
        release_match_setting_tag,
        mdb, pdb, None)
    released_prs, _ = await map_prs_to_releases(
        prs, releases, matched_bys, pd.DataFrame(), {}, time_to, dag,
        release_match_setting_tag, mdb, pdb, None)
    await wait_deferred()
    await update_unreleased_prs(
        prs, released_prs, time_to, {},
        matched_bys, default_branches, release_match_setting_tag, pdb)
    unreleased_prs = await discover_unreleased_prs(
        prs, time_to, matched_bys, default_branches, release_match_setting_tag, pdb)
    assert len(unreleased_prs) == 1
    assert next(iter(unreleased_prs.keys())) == "MDExOlB1bGxSZXF1ZXN0MjI2NTg3NjE1"
    releases = releases[releases[Release.published_at.key] < datetime(2018, 11, 1, tzinfo=utc)]
    unreleased_prs = await discover_unreleased_prs(
        prs, releases[Release.published_at.key].max(), matched_bys, default_branches,
        release_match_setting_tag, pdb)
    assert len(unreleased_prs) == 7


@with_defer
async def test_load_old_merged_unreleased_prs_smoke(
        mdb, pdb, dag, release_match_setting_tag, cache):
    metrics_time_from = datetime(2018, 1, 1, tzinfo=timezone.utc)
    metrics_time_to = datetime(2020, 5, 1, tzinfo=timezone.utc)
    await calc_pull_request_facts_github(
        metrics_time_from, metrics_time_to, {"src-d/go-git"}, {}, LabelFilter.empty(),
        JIRAFilter.empty(), False, release_match_setting_tag, mdb, pdb, cache,
    )
    await wait_deferred()
    unreleased_time_from = datetime(2018, 11, 1, tzinfo=timezone.utc)
    unreleased_time_to = datetime(2018, 11, 19, tzinfo=timezone.utc)
    unreleased_prs = await load_inactive_merged_unreleased_prs(
        unreleased_time_from, unreleased_time_to, {"src-d/go-git"},
        {ParticipationKind.MERGER: {"mcuadros"}}, LabelFilter.empty(), {},
        release_match_setting_tag, mdb, pdb, cache)
    await wait_deferred()
    assert len(unreleased_prs) == 11
    assert (unreleased_prs[PullRequest.merged_at.key] >
            datetime(2018, 10, 17, tzinfo=timezone.utc)).all()
    unreleased_prs = await load_inactive_merged_unreleased_prs(
        unreleased_time_from, unreleased_time_to, {"src-d/go-git"},
        {ParticipationKind.MERGER: {"mcuadros"}}, LabelFilter.empty(), {},
        release_match_setting_tag, None, None, cache)
    assert len(unreleased_prs) == 11
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], None, None, metrics_time_from, unreleased_time_to,
        release_match_setting_tag, mdb, pdb, cache)
    await wait_deferred()
    released_prs, _ = await map_prs_to_releases(
        unreleased_prs, releases, matched_bys, pd.DataFrame(), {},
        unreleased_time_to, dag, release_match_setting_tag, mdb, pdb, cache)
    await wait_deferred()
    assert released_prs.empty
    unreleased_time_from = datetime(2018, 11, 19, tzinfo=timezone.utc)
    unreleased_time_to = datetime(2018, 11, 20, tzinfo=timezone.utc)
    unreleased_prs = await load_inactive_merged_unreleased_prs(
        unreleased_time_from, unreleased_time_to, {"src-d/go-git"},
        {ParticipationKind.MERGER: {"mcuadros"}}, LabelFilter.empty(), {},
        release_match_setting_tag, mdb, pdb, cache)
    assert unreleased_prs.empty


@with_defer
async def test_load_old_merged_unreleased_prs_labels(mdb, pdb, release_match_setting_tag, cache):
    metrics_time_from = datetime(2018, 5, 1, tzinfo=timezone.utc)
    metrics_time_to = datetime(2019, 1, 1, tzinfo=timezone.utc)
    await calc_pull_request_facts_github(
        metrics_time_from, metrics_time_to, {"src-d/go-git"}, {}, LabelFilter.empty(),
        JIRAFilter.empty(), False, release_match_setting_tag, mdb, pdb, cache,
    )
    await wait_deferred()
    unreleased_time_from = datetime(2018, 9, 19, tzinfo=timezone.utc)
    unreleased_time_to = datetime(2018, 9, 30, tzinfo=timezone.utc)
    unreleased_prs = await load_inactive_merged_unreleased_prs(
        unreleased_time_from, unreleased_time_to, {"src-d/go-git"},
        {}, LabelFilter({"bug", "plumbing"}, set()), {}, release_match_setting_tag,
        mdb, pdb, cache)
    assert list(unreleased_prs.index) == ["MDExOlB1bGxSZXF1ZXN0MjE2MTA0NzY1",
                                          "MDExOlB1bGxSZXF1ZXN0MjEzODQ1NDUx"]
    unreleased_prs = await load_inactive_merged_unreleased_prs(
        unreleased_time_from, unreleased_time_to, {"src-d/go-git"},
        {}, LabelFilter({"enhancement"}, set()), {}, release_match_setting_tag,
        mdb, pdb, cache)
    assert list(unreleased_prs.index) == ["MDExOlB1bGxSZXF1ZXN0MjEzODQwMDc3"]
    unreleased_prs = await load_inactive_merged_unreleased_prs(
        unreleased_time_from, unreleased_time_to, {"src-d/go-git"},
        {}, LabelFilter({"bug"}, {"ssh"}), {}, release_match_setting_tag,
        mdb, pdb, cache)
    assert list(unreleased_prs.index) == ["MDExOlB1bGxSZXF1ZXN0MjE2MTA0NzY1"]


async def test_store_precomputed_done_none_assert(pdb, pr_samples):
    samples = pr_samples(1)  # type: Sequence[PullRequestFacts]
    settings = {"github.com/one": ReleaseMatchSetting("{{default}}", ".*", ReleaseMatch.tag)}
    default_branches = {"one": "master"}
    prs = [MinedPullRequest(
        pr={PullRequest.created_at.key: samples[0].merged.best,
            PullRequest.repository_full_name.key: "one",
            PullRequest.user_login.key: "xxx",
            PullRequest.merged_by_login.key: "yyy",
            PullRequest.number.key: 1,
            PullRequest.node_id.key: uuid.uuid4().hex},
        release={matched_by_column: settings["github.com/one"],
                 Release.author.key: "foo",
                 Release.url.key: "https://release",
                 Release.id.key: "MDwhatever="},
        comments=gen_dummy_df(samples[0].first_comment_on_first_review.best),
        commits=pd.DataFrame.from_records(
            [["yyy", "yyy", samples[0].first_commit.best]],
            columns=[
                PullRequestCommit.committer_login.key,
                PullRequestCommit.author_login.key,
                PullRequestCommit.committed_date.key,
            ],
        ),
        reviews=gen_dummy_df(samples[0].first_comment_on_first_review.best),
        review_comments=gen_dummy_df(samples[0].first_comment_on_first_review.best),
        review_requests=gen_dummy_df(samples[0].first_review_request.best),
        labels=pd.DataFrame.from_records([["bug"]], columns=["name"]),
        jiras=pd.DataFrame(),
    )]
    await store_precomputed_done_facts(prs, [None], default_branches, settings, pdb)
    with pytest.raises(AssertionError):
        await store_precomputed_done_facts(prs, samples, default_branches, settings, pdb)


@with_defer
async def test_store_merged_unreleased_pull_request_facts_smoke(
        mdb, pdb, default_branches, release_match_setting_tag, pr_samples):
    prs = await read_sql_query(
        select([PullRequest]).where(and_(PullRequest.number.in_(range(1000, 1010)),
                                         PullRequest.merged_at.isnot(None))),
        mdb, PullRequest, index=PullRequest.node_id.key)
    prs[prs[PullRequest.merged_at.key].isnull()] = datetime.now(tz=timezone.utc)
    utc = timezone.utc
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], None, default_branches,
        datetime(2018, 9, 1, tzinfo=utc),
        datetime(2018, 11, 1, tzinfo=utc),
        release_match_setting_tag,
        mdb, pdb, None)
    assert len(releases) == 2
    assert matched_bys == {"src-d/go-git": ReleaseMatch.tag}
    empty_rdf = new_released_prs_df()
    await update_unreleased_prs(
        prs, empty_rdf, datetime(2018, 11, 1, tzinfo=utc), {},
        matched_bys, default_branches, release_match_setting_tag, pdb)
    samples = []
    for f in pr_samples(len(prs)):
        fields = f.__dict__
        fields["released"] = Fallback(None, None)
        samples.append(PullRequestFacts(**fields))
    index = prs.index
    prs = [pr.to_dict() for _, pr in prs.iterrows()]
    for i, pr in zip(index, prs):
        pr[PullRequest.node_id.key] = i
    await store_merged_unreleased_pull_request_facts(
        list(zip(prs, samples)), matched_bys, default_branches, release_match_setting_tag, pdb)
    true_dict = {pr[PullRequest.node_id.key]: s for pr, s in zip(prs, samples)}
    ghmprf = GitHubMergedPullRequestFacts
    rows = await pdb.fetch_all(select([ghmprf]))
    new_dict = {r[ghmprf.pr_node_id.key]: pickle.loads(r[ghmprf.data.key]) for r in rows}
    assert true_dict == new_dict


@with_defer
async def test_store_merged_unreleased_pull_request_facts_assert(
        mdb, pdb, default_branches, release_match_setting_tag, pr_samples):
    prs = await read_sql_query(
        select([PullRequest]).where(and_(PullRequest.number.in_(range(1000, 1010)),
                                         PullRequest.merged_at.isnot(None))),
        mdb, PullRequest, index=PullRequest.node_id.key)
    prs[prs[PullRequest.merged_at.key].isnull()] = datetime.now(tz=timezone.utc)
    utc = timezone.utc
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], None, default_branches,
        datetime(2018, 9, 1, tzinfo=utc),
        datetime(2018, 11, 1, tzinfo=utc),
        release_match_setting_tag,
        mdb, pdb, None)
    assert len(releases) == 2
    assert matched_bys == {"src-d/go-git": ReleaseMatch.tag}
    empty_rdf = new_released_prs_df()
    await update_unreleased_prs(
        prs, empty_rdf, datetime(2018, 11, 1, tzinfo=utc), {},
        matched_bys, default_branches, release_match_setting_tag, pdb)
    with pytest.raises(AssertionError):
        await store_merged_unreleased_pull_request_facts(
            list(zip(prs, pr_samples(len(prs)))), matched_bys, default_branches,
            release_match_setting_tag, pdb)
