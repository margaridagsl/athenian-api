from collections import defaultdict
from datetime import datetime, timedelta, timezone
import pickle
from typing import Dict

from databases import Database
import lz4.frame
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete, select, sql
from sqlalchemy.schema import CreateTable

from athenian.api.async_read_sql_query import read_sql_query
from athenian.api.controllers.miners.github.bots import bots
from athenian.api.controllers.miners.github.branches import extract_branches
from athenian.api.controllers.miners.github.precomputed_prs import store_precomputed_done_facts
from athenian.api.controllers.miners.github.pull_request import PullRequestFactsMiner, \
    PullRequestMiner
from athenian.api.controllers.miners.github.release import \
    _empty_dag, _fetch_first_parents, _fetch_repository_commits, \
    _fetch_repository_first_commit_dates, _find_dead_merged_prs, load_releases, \
    map_prs_to_releases, map_releases_to_prs
from athenian.api.controllers.miners.github.release_accelerated import extract_subdag, join_dags, \
    mark_dag_access
from athenian.api.controllers.settings import ReleaseMatch, ReleaseMatchSetting
from athenian.api.defer import wait_deferred, with_defer
from athenian.api.models.metadata.github import Branch, PullRequest, PullRequestLabel, \
    Release
from athenian.api.models.precomputed.models import GitHubCommitFirstParents, GitHubCommitHistory
from tests.controllers.conftest import fetch_dag
from tests.controllers.test_filter_controller import force_push_dropped_go_git_pr_numbers


def generate_repo_settings(prs: pd.DataFrame) -> Dict[str, ReleaseMatchSetting]:
    return {
        "github.com/" + r: ReleaseMatchSetting(branches="", tags=".*", match=ReleaseMatch.tag)
        for r in prs[PullRequest.repository_full_name.key]
    }


@with_defer
async def test_map_prs_to_releases_cache(branches, default_branches, dag, mdb, pdb, cache):
    prs = await read_sql_query(select([PullRequest]).where(PullRequest.number == 1126),
                               mdb, PullRequest, index=PullRequest.node_id.key)
    time_to = datetime(year=2020, month=4, day=1, tzinfo=timezone.utc)
    time_from = time_to - timedelta(days=5 * 365)
    settings = generate_repo_settings(prs)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], branches, default_branches, time_from, time_to, settings,
        mdb, pdb, None)
    tag = "https://github.com/src-d/go-git/releases/tag/v4.12.0"
    for i in range(2):
        released_prs, facts = await map_prs_to_releases(
            prs, releases, matched_bys, branches, default_branches, time_to, dag, settings,
            mdb, pdb, cache)
        await wait_deferred()
        assert isinstance(facts, dict)
        assert len(facts) == 0
        assert len(cache.mem) > 0
        assert len(released_prs) == 1, str(i)
        assert released_prs.iloc[0][Release.url.key] == tag
        assert released_prs.iloc[0][Release.published_at.key] == \
            pd.Timestamp("2019-06-18 22:57:34+0000", tzinfo=timezone.utc)
        assert released_prs.iloc[0][Release.author.key] == "mcuadros"
    released_prs, _ = await map_prs_to_releases(
        prs, releases, matched_bys, branches, default_branches, time_to, dag, settings,
        mdb, pdb, None)
    # the PR was merged and released in the past, we must detect that
    assert len(released_prs) == 1
    assert released_prs.iloc[0][Release.url.key] == tag


@with_defer
async def test_map_prs_to_releases_pdb(branches, default_branches, dag, mdb, pdb):
    prs = await read_sql_query(select([PullRequest]).where(PullRequest.number.in_((1126, 1180))),
                               mdb, PullRequest, index=PullRequest.node_id.key)
    time_to = datetime(year=2020, month=4, day=1, tzinfo=timezone.utc)
    time_from = time_to - timedelta(days=5 * 365)
    settings = generate_repo_settings(prs)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], branches, default_branches, time_from, time_to, settings,
        mdb, pdb, None)
    released_prs, _ = await map_prs_to_releases(
        prs, releases, matched_bys, branches, default_branches, time_to, dag, settings,
        mdb, pdb, None)
    await wait_deferred()
    pdb_dag = pickle.loads(lz4.frame.decompress(
        await pdb.fetch_val(select([GitHubCommitHistory.dag]))))
    dag = await fetch_dag(mdb, branches[Branch.commit_id.key].tolist())
    assert not (set(dag["src-d/go-git"][0]) - set(pdb_dag[0]))
    assert len(released_prs) == 1
    dummy_mdb = Database("sqlite://", force_rollback=True)
    await dummy_mdb.connect()
    try:
        # https://github.com/encode/databases/issues/40
        await dummy_mdb.execute(CreateTable(PullRequestLabel.__table__).compile(
            dialect=dummy_mdb._backend._dialect).string)
        released_prs, _ = await map_prs_to_releases(
            prs, releases, matched_bys, branches, default_branches, time_to, dag, settings,
            dummy_mdb, pdb, None)
        assert len(released_prs) == 1
    finally:
        await dummy_mdb.disconnect()


@with_defer
async def test_map_prs_to_releases_empty(branches, default_branches, dag, mdb, pdb, cache):
    prs = await read_sql_query(select([PullRequest]).where(PullRequest.number == 1231),
                               mdb, PullRequest, index=PullRequest.node_id.key)
    time_to = datetime(year=2020, month=4, day=1, tzinfo=timezone.utc)
    time_from = time_to - timedelta(days=5 * 365)
    settings = generate_repo_settings(prs)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], branches, default_branches, time_from, time_to, settings,
        mdb, pdb, None)
    for i in range(2):
        released_prs, _ = await map_prs_to_releases(
            prs, releases, matched_bys, branches, default_branches, time_to, dag, settings,
            mdb, pdb, cache)
        assert len(cache.mem) == 2, i
        assert released_prs.empty
    prs = prs.iloc[:0]
    released_prs, _ = await map_prs_to_releases(
        prs, releases, matched_bys, branches, default_branches, time_to, dag, settings,
        mdb, pdb, cache)
    assert len(cache.mem) == 2
    assert released_prs.empty


@with_defer
async def test_map_prs_to_releases_precomputed_released(
        branches, default_branches, dag, mdb, pdb, release_match_setting_tag):
    time_to = datetime(year=2019, month=8, day=2, tzinfo=timezone.utc)
    time_from = time_to - timedelta(days=2)

    miner, _, _ = await PullRequestMiner.mine(
        time_from.date(),
        time_to.date(),
        time_from,
        time_to,
        {"src-d/go-git"},
        {},
        set(),
        branches, default_branches,
        False,
        release_match_setting_tag,
        mdb,
        pdb,
        None,
    )
    times_miner = PullRequestFactsMiner(await bots(mdb))
    true_prs = [pr for pr in miner if pr.release[Release.published_at.key] is not None]
    times = [times_miner(pr) for pr in true_prs]
    prs = pd.DataFrame([pr.pr for pr in true_prs]).set_index(PullRequest.node_id.key)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], branches, default_branches, time_from, time_to,
        release_match_setting_tag, mdb, pdb, None)

    await pdb.execute(delete(GitHubCommitHistory))
    dummy_mdb = Database("sqlite://", force_rollback=True)
    await dummy_mdb.connect()
    try:
        # https://github.com/encode/databases/issues/40
        await dummy_mdb.execute(CreateTable(PullRequestLabel.__table__).compile(
            dialect=dummy_mdb._backend._dialect).string)
        with pytest.raises(Exception):
            await map_prs_to_releases(
                prs, releases, matched_bys, branches, default_branches, time_to, dag,
                release_match_setting_tag, dummy_mdb, pdb, None)

        await store_precomputed_done_facts(
            true_prs, times, default_branches, release_match_setting_tag, pdb)

        released_prs, _ = await map_prs_to_releases(
            prs, releases, matched_bys, branches, default_branches, time_to, dag,
            release_match_setting_tag, dummy_mdb, pdb, None)
        assert len(released_prs) == len(prs)
    finally:
        await dummy_mdb.disconnect()


@with_defer
async def test_map_releases_to_prs_early_merges(
        branches, default_branches, mdb, pdb, release_match_setting_tag):
    prs, releases, matched_bys, dag = await map_releases_to_prs(
        ["src-d/go-git"],
        branches, default_branches,
        datetime(year=2018, month=1, day=7, tzinfo=timezone.utc),
        datetime(year=2018, month=1, day=9, tzinfo=timezone.utc),
        [], [],
        release_match_setting_tag, mdb, pdb, None)
    assert len(prs) == 60
    assert (prs[PullRequest.merged_at.key] >
            datetime(year=2017, month=9, day=4, tzinfo=timezone.utc)).all()
    assert isinstance(dag, dict)
    dag = dag["src-d/go-git"]
    assert len(dag) == 3
    assert len(dag[0]) == 1015
    assert dag[0].dtype == np.dtype("U40")
    assert len(dag[1]) == 1016
    assert dag[1].dtype == np.uint32
    assert len(dag[2]) == dag[1][-1]
    assert dag[2].dtype == np.uint32


@with_defer
async def test_map_releases_to_prs_smoke(
        branches, default_branches, mdb, pdb, cache, release_match_setting_tag):
    for _ in range(2):
        prs, releases, matched_bys, dag = await map_releases_to_prs(
            ["src-d/go-git"],
            branches, default_branches,
            datetime(year=2019, month=7, day=31, tzinfo=timezone.utc),
            datetime(year=2019, month=12, day=2, tzinfo=timezone.utc),
            [], [],
            release_match_setting_tag, mdb, pdb, cache)
        await wait_deferred()
        assert len(prs) == 7
        assert len(dag["src-d/go-git"][0]) == 1508
        assert (prs[PullRequest.merged_at.key] < pd.Timestamp(
            "2019-07-31 00:00:00", tzinfo=timezone.utc)).all()
        assert (prs[PullRequest.merged_at.key] > pd.Timestamp(
            "2019-06-19 00:00:00", tzinfo=timezone.utc)).all()
        assert len(releases) == 2
        assert set(releases[Release.sha.key]) == {
            "0d1a009cbb604db18be960db5f1525b99a55d727",
            "6241d0e70427cb0db4ca00182717af88f638268c",
        }
        assert matched_bys == {"src-d/go-git": ReleaseMatch.tag}


@with_defer
async def test_map_releases_to_prs_no_truncate(
        branches, default_branches, mdb, pdb, release_match_setting_tag):
    prs, releases, matched_bys, _ = await map_releases_to_prs(
        ["src-d/go-git"],
        branches, default_branches,
        datetime(year=2018, month=7, day=31, tzinfo=timezone.utc),
        datetime(year=2018, month=12, day=2, tzinfo=timezone.utc),
        [], [],
        release_match_setting_tag, mdb, pdb, None, truncate=False)
    assert len(prs) == 8
    assert len(releases) == 5 + 7
    assert releases[Release.published_at.key].is_monotonic_decreasing
    assert releases.index.is_monotonic
    assert "v4.13.1" in releases[Release.tag.key].values


@with_defer
async def test_map_releases_to_prs_empty(
        branches, default_branches, mdb, pdb, cache, release_match_setting_tag):
    prs, releases, matched_bys, _ = await map_releases_to_prs(
        ["src-d/go-git"],
        branches, default_branches,
        datetime(year=2019, month=7, day=1, tzinfo=timezone.utc),
        datetime(year=2019, month=12, day=2, tzinfo=timezone.utc),
        [], [], release_match_setting_tag, mdb, pdb, cache)
    await wait_deferred()
    assert prs.empty
    assert len(cache.mem) == 3
    assert len(releases) == 2
    assert set(releases[Release.sha.key]) == {
        "0d1a009cbb604db18be960db5f1525b99a55d727",
        "6241d0e70427cb0db4ca00182717af88f638268c",
    }
    assert matched_bys == {"src-d/go-git": ReleaseMatch.tag}
    prs, releases, matched_bys, _ = await map_releases_to_prs(
        ["src-d/go-git"],
        branches, default_branches,
        datetime(year=2019, month=7, day=1, tzinfo=timezone.utc),
        datetime(year=2019, month=12, day=2, tzinfo=timezone.utc),
        [], [], {
            "github.com/src-d/go-git": ReleaseMatchSetting(
                branches="master", tags=".*", match=ReleaseMatch.branch),
        }, mdb, pdb, cache)
    assert prs.empty
    assert len(cache.mem) == 11
    assert len(releases) == 19
    assert matched_bys == {"src-d/go-git": ReleaseMatch.branch}


@with_defer
async def test_map_releases_to_prs_blacklist(
        branches, default_branches, mdb, pdb, cache, release_match_setting_tag):
    prs, releases, matched_bys, _ = await map_releases_to_prs(
        ["src-d/go-git"],
        branches, default_branches,
        datetime(year=2019, month=7, day=31, tzinfo=timezone.utc),
        datetime(year=2019, month=12, day=2, tzinfo=timezone.utc),
        [], [], release_match_setting_tag, mdb, pdb, cache,
        pr_blacklist=PullRequest.node_id.notin_([
            "MDExOlB1bGxSZXF1ZXN0Mjk3Mzk1Mzcz", "MDExOlB1bGxSZXF1ZXN0Mjk5NjA3MDM2",
            "MDExOlB1bGxSZXF1ZXN0MzAxODQyNDg2", "MDExOlB1bGxSZXF1ZXN0Mjg2ODczMDAw",
            "MDExOlB1bGxSZXF1ZXN0Mjk0NTUyNTM0", "MDExOlB1bGxSZXF1ZXN0MzAyMTMwODA3",
            "MDExOlB1bGxSZXF1ZXN0MzAyMTI2ODgx",
        ]))
    assert prs.empty
    assert len(releases) == 2
    assert set(releases[Release.sha.key]) == {
        "0d1a009cbb604db18be960db5f1525b99a55d727",
        "6241d0e70427cb0db4ca00182717af88f638268c",
    }
    assert matched_bys == {"src-d/go-git": ReleaseMatch.tag}


@pytest.mark.parametrize("authors, mergers, n", [(["mcuadros"], [], 2),
                                                 ([], ["mcuadros"], 7),
                                                 (["mcuadros"], ["mcuadros"], 7)])
@with_defer
async def test_map_releases_to_prs_authors_mergers(
        branches, default_branches, mdb, pdb, cache,
        release_match_setting_tag, authors, mergers, n):
    prs, releases, matched_bys, _ = await map_releases_to_prs(
        ["src-d/go-git"],
        branches, default_branches,
        datetime(year=2019, month=7, day=31, tzinfo=timezone.utc),
        datetime(year=2019, month=12, day=2, tzinfo=timezone.utc),
        authors, mergers, release_match_setting_tag, mdb, pdb, cache)
    assert len(prs) == n
    assert len(releases) == 2
    assert set(releases[Release.sha.key]) == {
        "0d1a009cbb604db18be960db5f1525b99a55d727",
        "6241d0e70427cb0db4ca00182717af88f638268c",
    }
    assert matched_bys == {"src-d/go-git": ReleaseMatch.tag}


@with_defer
async def test_map_releases_to_prs_hard(
        branches, default_branches, mdb, pdb, cache, release_match_setting_tag):
    prs, releases, matched_bys, _ = await map_releases_to_prs(
        ["src-d/go-git"],
        branches, default_branches,
        datetime(year=2019, month=6, day=18, tzinfo=timezone.utc),
        datetime(year=2019, month=6, day=30, tzinfo=timezone.utc),
        [], [],
        release_match_setting_tag, mdb, pdb, cache)
    assert len(prs) == 24
    assert len(releases) == 1
    assert set(releases[Release.sha.key]) == {
        "f9a30199e7083bdda8adad3a4fa2ec42d25c1fdb",
    }
    assert matched_bys == {"src-d/go-git": ReleaseMatch.tag}


@with_defer
async def test_map_releases_to_prs_future(
        branches, default_branches, mdb, pdb, release_match_setting_tag):
    prs, releases, matched_bys, _ = await map_releases_to_prs(
        ["src-d/go-git"],
        branches, default_branches,
        datetime(year=2018, month=7, day=31, tzinfo=timezone.utc),
        datetime(year=2030, month=12, day=2, tzinfo=timezone.utc),
        [], [],
        release_match_setting_tag, mdb, pdb, None, truncate=False)
    assert len(prs) > 0
    assert releases is not None
    assert len(releases) > 0


@with_defer
async def test_map_prs_to_releases_smoke_metrics(branches, default_branches, dag, mdb, pdb):
    time_from = datetime(year=2015, month=10, day=13, tzinfo=timezone.utc)
    time_to = datetime(year=2020, month=1, day=24, tzinfo=timezone.utc)
    filters = [
        sql.and_(PullRequest.merged_at > time_from, PullRequest.created_at < time_to),
        PullRequest.repository_full_name.in_(["src-d/go-git"]),
        PullRequest.user_login.in_(["mcuadros", "vmarkovtsev"]),
    ]
    prs = await read_sql_query(select([PullRequest]).where(sql.and_(*filters)),
                               mdb, PullRequest, index=PullRequest.node_id.key)
    time_to = datetime(year=2020, month=4, day=1, tzinfo=timezone.utc)
    time_from = time_to - timedelta(days=5 * 365)
    settings = generate_repo_settings(prs)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], branches, default_branches, time_from, time_to, settings,
        mdb, pdb, None)
    released_prs, _ = await map_prs_to_releases(
        prs, releases, matched_bys, branches, default_branches, time_to, dag, settings,
        mdb, pdb, None)
    assert set(released_prs[Release.url.key].unique()) == {
        None,
        "https://github.com/src-d/go-git/releases/tag/v4.0.0-rc10",
        "https://github.com/src-d/go-git/releases/tag/v4.0.0-rc11",
        "https://github.com/src-d/go-git/releases/tag/v4.0.0-rc13",
        "https://github.com/src-d/go-git/releases/tag/v4.0.0-rc12",
        "https://github.com/src-d/go-git/releases/tag/v4.0.0-rc14",
        "https://github.com/src-d/go-git/releases/tag/v4.0.0-rc15",
        "https://github.com/src-d/go-git/releases/tag/v4.0.0",
        "https://github.com/src-d/go-git/releases/tag/v4.2.0",
        "https://github.com/src-d/go-git/releases/tag/v4.1.1",
        "https://github.com/src-d/go-git/releases/tag/v4.2.1",
        "https://github.com/src-d/go-git/releases/tag/v4.5.0",
        "https://github.com/src-d/go-git/releases/tag/v4.11.0",
        "https://github.com/src-d/go-git/releases/tag/v4.7.1",
        "https://github.com/src-d/go-git/releases/tag/v4.8.0",
        "https://github.com/src-d/go-git/releases/tag/v4.10.0",
        "https://github.com/src-d/go-git/releases/tag/v4.12.0",
        "https://github.com/src-d/go-git/releases/tag/v4.13.0",
    }


def check_branch_releases(releases: pd.DataFrame, n: int, date_from: datetime, date_to: datetime):
    assert len(releases) == n
    assert "mcuadros" in set(releases[Release.author.key])
    assert len(releases[Release.commit_id.key].unique()) == n
    assert releases[Release.id.key].all()
    assert all(len(n) == 40 for n in releases[Release.name.key])
    assert releases[Release.published_at.key].between(date_from, date_to).all()
    assert (releases[Release.repository_full_name.key] == "src-d/go-git").all()
    assert all(len(n) == 40 for n in releases[Release.sha.key])
    assert len(releases[Release.sha.key].unique()) == n
    assert (~releases[Release.tag.key].values.astype(bool)).all()
    assert releases[Release.url.key].str.startswith("http").all()


@pytest.mark.parametrize("branches_", ["{{default}}", "master", "m.*"])
@with_defer
async def test_load_releases_branches(branches, default_branches, mdb, pdb, cache, branches_):
    date_from = datetime(year=2017, month=10, day=13, tzinfo=timezone.utc)
    date_to = datetime(year=2020, month=1, day=24, tzinfo=timezone.utc)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"],
        branches, default_branches,
        date_from,
        date_to,
        {"github.com/src-d/go-git": ReleaseMatchSetting(
            branches=branches_, tags="", match=ReleaseMatch.branch)},
        mdb,
        pdb,
        cache,
    )
    assert matched_bys == {"src-d/go-git": ReleaseMatch.branch}
    check_branch_releases(releases, 240, date_from, date_to)


@with_defer
async def test_load_releases_branches_empty(branches, default_branches, mdb, pdb, cache):
    date_from = datetime(year=2017, month=10, day=13, tzinfo=timezone.utc)
    date_to = datetime(year=2020, month=1, day=24, tzinfo=timezone.utc)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"],
        branches, default_branches,
        date_from,
        date_to,
        {"github.com/src-d/go-git": ReleaseMatchSetting(
            branches="unknown", tags="", match=ReleaseMatch.branch)},
        mdb,
        pdb,
        cache,
    )
    assert len(releases) == 0
    assert matched_bys == {"src-d/go-git": ReleaseMatch.branch}


@pytest.mark.parametrize("date_from, n", [
    (datetime(year=2017, month=10, day=4, tzinfo=timezone.utc), 45),
    (datetime(year=2017, month=9, day=4, tzinfo=timezone.utc), 1),
    (datetime(year=2017, month=12, day=8, tzinfo=timezone.utc), 0),
])
@with_defer
async def test_load_releases_tag_or_branch_dates(
        branches, default_branches, mdb, pdb, cache, date_from, n):
    date_to = datetime(year=2017, month=12, day=8, tzinfo=timezone.utc)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"],
        branches, default_branches,
        date_from,
        date_to,
        {"github.com/src-d/go-git": ReleaseMatchSetting(
            branches="master", tags=".*", match=ReleaseMatch.tag_or_branch)},
        mdb,
        pdb,
        cache,
    )
    if n > 1:
        check_branch_releases(releases, n, date_from, date_to)
        assert matched_bys == {"src-d/go-git": ReleaseMatch.branch}
    else:
        assert len(releases) == n
        if n > 0:
            assert matched_bys == {"src-d/go-git": ReleaseMatch.tag}
        else:
            assert matched_bys == {"src-d/go-git": ReleaseMatch.branch}


@with_defer
async def test_load_releases_tag_or_branch_initial(branches, default_branches, mdb, pdb):
    date_from = datetime(year=2015, month=1, day=1, tzinfo=timezone.utc)
    date_to = datetime(year=2015, month=10, day=22, tzinfo=timezone.utc)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"],
        branches, default_branches,
        date_from,
        date_to,
        {"github.com/src-d/go-git": ReleaseMatchSetting(
            branches="master", tags="", match=ReleaseMatch.branch)},
        mdb,
        pdb,
        None,
    )
    assert matched_bys == {"src-d/go-git": ReleaseMatch.branch}
    check_branch_releases(releases, 17, date_from, date_to)


@with_defer
async def test_map_releases_to_prs_branches(branches, default_branches, mdb, pdb):
    date_from = datetime(year=2015, month=4, day=1, tzinfo=timezone.utc)
    date_to = datetime(year=2015, month=5, day=1, tzinfo=timezone.utc)
    prs, releases, matched_bys, _ = await map_releases_to_prs(
        ["src-d/go-git"],
        branches, default_branches,
        date_from,
        date_to,
        [], [],
        {"github.com/src-d/go-git": ReleaseMatchSetting(
            branches="master", tags="", match=ReleaseMatch.branch)},
        mdb,
        pdb,
        None)
    assert prs.empty
    assert len(releases) == 1
    assert releases[Release.sha.key][0] == "5d7303c49ac984a9fec60523f2d5297682e16646"
    assert matched_bys == {"src-d/go-git": ReleaseMatch.branch}


@pytest.mark.parametrize("repos", [["src-d/gitbase"], []])
@with_defer
async def test_load_releases_empty(branches, default_branches, mdb, pdb, repos):
    releases, matched_bys = await load_releases(
        repos,
        branches, default_branches,
        datetime(year=2020, month=6, day=30, tzinfo=timezone.utc),
        datetime(year=2020, month=7, day=30, tzinfo=timezone.utc),
        {"github.com/src-d/gitbase": ReleaseMatchSetting(
            branches=".*", tags=".*", match=ReleaseMatch.branch)},
        mdb,
        pdb,
        None,
        index=Release.id.key)
    assert releases.empty
    if repos:
        assert matched_bys == {"src-d/gitbase": ReleaseMatch.branch}
    date_from = datetime(year=2017, month=3, day=4, tzinfo=timezone.utc)
    date_to = datetime(year=2017, month=12, day=8, tzinfo=timezone.utc)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"],
        branches, default_branches,
        date_from,
        date_to,
        {"github.com/src-d/go-git": ReleaseMatchSetting(
            branches="master", tags="", match=ReleaseMatch.tag)},
        mdb,
        pdb,
        None,
    )
    assert releases.empty
    assert matched_bys == {"src-d/go-git": ReleaseMatch.tag}
    releases, matched_bys = await load_releases(
        ["src-d/go-git"],
        branches, default_branches,
        date_from,
        date_to,
        {"github.com/src-d/go-git": ReleaseMatchSetting(
            branches="", tags=".*", match=ReleaseMatch.branch)},
        mdb,
        pdb,
        None,
    )
    assert releases.empty
    assert matched_bys == {"src-d/go-git": ReleaseMatch.branch}


@pytest.mark.parametrize("prune", [False, True])
@with_defer
async def test__fetch_repository_commits_smoke(mdb, pdb, prune):
    dags = await _fetch_repository_commits(
        {"src-d/go-git": _empty_dag()},
        pd.DataFrame([
            ("d2a38b4a5965d529566566640519d03d2bd10f6c",
             "MDY6Q29tbWl0NDQ3MzkwNDQ6ZDJhMzhiNGE1OTY1ZDUyOTU2NjU2NjY0MDUxOWQwM2QyYmQxMGY2Yw==",
             525,
             "src-d/go-git"),
            ("31eae7b619d166c366bf5df4991f04ba8cebea0a",
             "MDY6Q29tbWl0NDQ3MzkwNDQ6MzFlYWU3YjYxOWQxNjZjMzY2YmY1ZGY0OTkxZjA0YmE4Y2ViZWEwYQ==",
             611,
             "src-d/go-git")],
            columns=["1", "2", "3", "4"],
        ),
        ("1", "2", "3", "4"),
        prune, mdb, pdb, None)
    assert isinstance(dags, dict)
    assert len(dags) == 1
    hashes, vertexes, edges = dags["src-d/go-git"]
    ground_truth = {
        "31eae7b619d166c366bf5df4991f04ba8cebea0a": ["b977a025ca21e3b5ca123d8093bd7917694f6da7",
                                                     "d2a38b4a5965d529566566640519d03d2bd10f6c"],
        "b977a025ca21e3b5ca123d8093bd7917694f6da7": ["35b585759cbf29f8ec428ef89da20705d59f99ec"],
        "d2a38b4a5965d529566566640519d03d2bd10f6c": ["35b585759cbf29f8ec428ef89da20705d59f99ec"],
        "35b585759cbf29f8ec428ef89da20705d59f99ec": ["c2bbf9fe8009b22d0f390f3c8c3f13937067590f"],
        "c2bbf9fe8009b22d0f390f3c8c3f13937067590f": ["fc9f0643b21cfe571046e27e0c4565f3a1ee96c8"],
        "fc9f0643b21cfe571046e27e0c4565f3a1ee96c8": ["c088fd6a7e1a38e9d5a9815265cb575bb08d08ff"],
        "c088fd6a7e1a38e9d5a9815265cb575bb08d08ff": ["5fddbeb678bd2c36c5e5c891ab8f2b143ced5baf"],
        "5fddbeb678bd2c36c5e5c891ab8f2b143ced5baf": ["5d7303c49ac984a9fec60523f2d5297682e16646"],
        "5d7303c49ac984a9fec60523f2d5297682e16646": [],
    }
    for k, v in ground_truth.items():
        vertex = np.where(hashes == k)[0][0]
        assert hashes[edges[vertexes[vertex]:vertexes[vertex + 1]]].tolist() == v
    assert len(hashes) == 9
    await wait_deferred()
    dags2 = await _fetch_repository_commits(
        dags,
        pd.DataFrame([
            ("d2a38b4a5965d529566566640519d03d2bd10f6c",
             "MDY6Q29tbWl0NDQ3MzkwNDQ6ZDJhMzhiNGE1OTY1ZDUyOTU2NjU2NjY0MDUxOWQwM2QyYmQxMGY2Yw==",
             525,
             "src-d/go-git"),
            ("31eae7b619d166c366bf5df4991f04ba8cebea0a",
             "MDY6Q29tbWl0NDQ3MzkwNDQ6MzFlYWU3YjYxOWQxNjZjMzY2YmY1ZGY0OTkxZjA0YmE4Y2ViZWEwYQ==",
             611,
             "src-d/go-git")],
            columns=["1", "2", "3", "4"],
        ),
        ("1", "2", "3", "4"),
        prune, Database("sqlite://"), pdb, None)
    assert pickle.dumps(dags2) == pickle.dumps(dags)
    with pytest.raises(Exception):
        await _fetch_repository_commits(
            dags,
            pd.DataFrame([
                ("1353ccd6944ab41082099b79979ded3223db98ec",
                 "MDY6Q29tbWl0NDQ3MzkwNDQ6MzFlYWU3YjYxOWQxNjZjMzY2YmY1ZGY0OTkxZjA0YmE4Y2ViZWEwYQ==",  # noqa
                 525,
                 "src-d/go-git"),
                ("31eae7b619d166c366bf5df4991f04ba8cebea0a",
                 "MDY6Q29tbWl0NDQ3MzkwNDQ6MzFlYWU3YjYxOWQxNjZjMzY2YmY1ZGY0OTkxZjA0YmE4Y2ViZWEwYQ==",  # noqa
                 611,
                 "src-d/go-git")],
                columns=["1", "2", "3", "4"],
            ),
            ("1", "2", "3", "4"),
            prune, Database("sqlite://"), pdb, None)


@pytest.mark.parametrize("prune", [False, True])
@with_defer
async def test__fetch_repository_commits_initial_commit(mdb, pdb, prune):
    dags = await _fetch_repository_commits(
        {"src-d/go-git": _empty_dag()},
        pd.DataFrame([
            ("5d7303c49ac984a9fec60523f2d5297682e16646",
             "MDY6Q29tbWl0NDQ3MzkwNDQ6NWQ3MzAzYzQ5YWM5ODRhOWZlYzYwNTIzZjJkNTI5NzY4MmUxNjY0Ng==",
             525,
             "src-d/go-git")],
            columns=["1", "2", "3", "4"],
        ),
        ("1", "2", "3", "4"),
        prune, mdb, pdb, None)
    hashes, vertexes, edges = dags["src-d/go-git"]
    assert hashes == np.array(["5d7303c49ac984a9fec60523f2d5297682e16646"], dtype="U40")
    assert (vertexes == np.array([0, 0], dtype=np.uint32)).all()
    assert (edges == np.array([], dtype=np.uint32)).all()


@with_defer
async def test__fetch_repository_commits_cache(mdb, pdb, cache):
    dags1 = await _fetch_repository_commits(
        {"src-d/go-git": _empty_dag()},
        pd.DataFrame([
            ("d2a38b4a5965d529566566640519d03d2bd10f6c",
             "MDY6Q29tbWl0NDQ3MzkwNDQ6ZDJhMzhiNGE1OTY1ZDUyOTU2NjU2NjY0MDUxOWQwM2QyYmQxMGY2Yw==",
             525,
             "src-d/go-git"),
            ("31eae7b619d166c366bf5df4991f04ba8cebea0a",
             "MDY6Q29tbWl0NDQ3MzkwNDQ6MzFlYWU3YjYxOWQxNjZjMzY2YmY1ZGY0OTkxZjA0YmE4Y2ViZWEwYQ==",
             611,
             "src-d/go-git")],
            columns=["1", "2", "3", "4"],
        ),
        ("1", "2", "3", "4"),
        False, mdb, pdb, cache)
    await wait_deferred()
    dags2 = await _fetch_repository_commits(
        {"src-d/go-git": _empty_dag()},
        pd.DataFrame([
            ("d2a38b4a5965d529566566640519d03d2bd10f6c",
             "MDY6Q29tbWl0NDQ3MzkwNDQ6ZDJhMzhiNGE1OTY1ZDUyOTU2NjU2NjY0MDUxOWQwM2QyYmQxMGY2Yw==",
             525,
             "src-d/go-git"),
            ("31eae7b619d166c366bf5df4991f04ba8cebea0a",
             "MDY6Q29tbWl0NDQ3MzkwNDQ6MzFlYWU3YjYxOWQxNjZjMzY2YmY1ZGY0OTkxZjA0YmE4Y2ViZWEwYQ==",
             611,
             "src-d/go-git")],
            columns=["1", "2", "3", "4"],
        ),
        ("1", "2", "3", "4"),
        False, None, None, cache)
    assert pickle.dumps(dags1) == pickle.dumps(dags2)
    fake_pdb = Database("sqlite://")

    class FakeMetrics:
        def get(self):
            return defaultdict(int)

    fake_pdb.metrics = {"hits": FakeMetrics(), "misses": FakeMetrics()}
    with pytest.raises(Exception):
        await _fetch_repository_commits(
            {"src-d/go-git": _empty_dag()},
            pd.DataFrame([
                ("d2a38b4a5965d529566566640519d03d2bd10f6c",
                 "MDY6Q29tbWl0NDQ3MzkwNDQ6ZDJhMzhiNGE1OTY1ZDUyOTU2NjU2NjY0MDUxOWQwM2QyYmQxMGY2Yw==",  # noqa
                 525,
                 "src-d/go-git"),
                ("31eae7b619d166c366bf5df4991f04ba8cebea0a",
                 "MDY6Q29tbWl0NDQ3MzkwNDQ6MzFlYWU3YjYxOWQxNjZjMzY2YmY1ZGY0OTkxZjA0YmE4Y2ViZWEwYQ==",  # noqa
                 611,
                 "src-d/go-git")],
                columns=["1", "2", "3", "4"],
            ),
            ("1", "2", "3", "4"),
            True, None, fake_pdb, cache)


@with_defer
async def test__fetch_repository_commits_many(mdb, pdb):
    dags = await _fetch_repository_commits(
        {"src-d/go-git": _empty_dag()},
        pd.DataFrame([
            ("d2a38b4a5965d529566566640519d03d2bd10f6c",
             "MDY6Q29tbWl0NDQ3MzkwNDQ6ZDJhMzhiNGE1OTY1ZDUyOTU2NjU2NjY0MDUxOWQwM2QyYmQxMGY2Yw==",
             525,
             "src-d/go-git"),
            ("31eae7b619d166c366bf5df4991f04ba8cebea0a",
             "MDY6Q29tbWl0NDQ3MzkwNDQ6MzFlYWU3YjYxOWQxNjZjMzY2YmY1ZGY0OTkxZjA0YmE4Y2ViZWEwYQ==",
             611,
             "src-d/go-git")] * 50,
            columns=["1", "2", "3", "4"],
        ),
        ("1", "2", "3", "4"),
        False, mdb, pdb, None)
    assert len(dags["src-d/go-git"][0]) == 9


@with_defer
async def test_fetch_first_parents_smoke(mdb, pdb):
    fp = await _fetch_first_parents(
        None,
        "src-d/go-git",
        ["MDY6Q29tbWl0NDQ3MzkwNDQ6ZDJhMzhiNGE1OTY1ZDUyOTU2NjU2NjY0MDUxOWQwM2QyYmQxMGY2Yw==",
         "MDY6Q29tbWl0NDQ3MzkwNDQ6MzFlYWU3YjYxOWQxNjZjMzY2YmY1ZGY0OTkxZjA0YmE4Y2ViZWEwYQ=="],
        datetime(2015, 4, 5),
        datetime(2015, 5, 20),
        mdb, pdb, None)
    await wait_deferred()
    ground_truth = {
        "MDY6Q29tbWl0NDQ3MzkwNDQ6NWQ3MzAzYzQ5YWM5ODRhOWZlYzYwNTIzZjJkNTI5NzY4MmUxNjY0Ng==",
        "MDY6Q29tbWl0NDQ3MzkwNDQ6NWZkZGJlYjY3OGJkMmMzNmM1ZTVjODkxYWI4ZjJiMTQzY2VkNWJhZg==",
        "MDY6Q29tbWl0NDQ3MzkwNDQ6YzA4OGZkNmE3ZTFhMzhlOWQ1YTk4MTUyNjVjYjU3NWJiMDhkMDhmZg==",
    }
    assert fp == ground_truth
    obj = await pdb.fetch_val(select([GitHubCommitFirstParents.commits]))
    fp = await _fetch_first_parents(
        obj,
        "src-d/go-git",
        ["MDY6Q29tbWl0NDQ3MzkwNDQ6ZDJhMzhiNGE1OTY1ZDUyOTU2NjU2NjY0MDUxOWQwM2QyYmQxMGY2Yw==",
         "MDY6Q29tbWl0NDQ3MzkwNDQ6MzFlYWU3YjYxOWQxNjZjMzY2YmY1ZGY0OTkxZjA0YmE4Y2ViZWEwYQ=="],
        datetime(2015, 4, 5),
        datetime(2015, 5, 20),
        Database("sqlite://"), pdb, None)
    await wait_deferred()
    assert fp == ground_truth
    with pytest.raises(Exception):
        await _fetch_first_parents(
            obj,
            "src-d/go-git",
            ["MDY6Q29tbWl0NDQ3MzkwNDQ6OTQwNDYwZjU0MjJiMDJmMDEzNTEzOTZhZjcwM2U5YjYzZTg1OTZhZQ=="],
            datetime(2015, 4, 5),
            datetime(2015, 5, 20),
            Database("sqlite://"), pdb, None)


@with_defer
async def test_fetch_first_parents_initial_commit(mdb, pdb):
    fp = await _fetch_first_parents(
        None,
        "src-d/go-git",
        ["MDY6Q29tbWl0NDQ3MzkwNDQ6NWQ3MzAzYzQ5YWM5ODRhOWZlYzYwNTIzZjJkNTI5NzY4MmUxNjY0Ng=="],
        datetime(2015, 4, 5),
        datetime(2015, 5, 20),
        mdb, pdb, None)
    assert fp == {
        "MDY6Q29tbWl0NDQ3MzkwNDQ6NWQ3MzAzYzQ5YWM5ODRhOWZlYzYwNTIzZjJkNTI5NzY4MmUxNjY0Ng==",
    }
    fp = await _fetch_first_parents(
        None,
        "src-d/go-git",
        ["MDY6Q29tbWl0NDQ3MzkwNDQ6NWQ3MzAzYzQ5YWM5ODRhOWZlYzYwNTIzZjJkNTI5NzY4MmUxNjY0Ng=="],
        datetime(2015, 3, 5),
        datetime(2015, 3, 20),
        mdb, pdb, None)
    assert fp == set()


@with_defer
async def test_fetch_first_parents_index_error(mdb, pdb):
    fp1 = await _fetch_first_parents(
        None,
        "src-d/go-git",
        ["MDY6Q29tbWl0NDQ3MzkwNDQ6NWQ3MzAzYzQ5YWM5ODRhOWZlYzYwNTIzZjJkNTI5NzY4MmUxNjY0Ng=="],
        datetime(2015, 4, 5),
        datetime(2015, 5, 20),
        mdb, pdb, None)
    await wait_deferred()
    data = await pdb.fetch_val(select([GitHubCommitFirstParents.commits]))
    assert data
    fp2 = await _fetch_first_parents(
        data,
        "src-d/go-git",
        ["MDY6Q29tbWl0NDQ3MzkwNDQ6NWZkZGJlYjY3OGJkMmMzNmM1ZTVjODkxYWI4ZjJiMTQzY2VkNWJhZg=="],
        datetime(2015, 4, 5),
        datetime(2015, 5, 20),
        mdb, pdb, None)
    await wait_deferred()
    assert fp1 != fp2


@with_defer
async def test_fetch_first_parents_cache(mdb, pdb, cache):
    await _fetch_first_parents(
        None,
        "src-d/go-git",
        ["MDY6Q29tbWl0NDQ3MzkwNDQ6ZDJhMzhiNGE1OTY1ZDUyOTU2NjU2NjY0MDUxOWQwM2QyYmQxMGY2Yw==",
         "MDY6Q29tbWl0NDQ3MzkwNDQ6MzFlYWU3YjYxOWQxNjZjMzY2YmY1ZGY0OTkxZjA0YmE4Y2ViZWEwYQ=="],
        datetime(2015, 4, 5),
        datetime(2015, 5, 20),
        mdb, pdb, cache)
    ground_truth = {
        "MDY6Q29tbWl0NDQ3MzkwNDQ6NWQ3MzAzYzQ5YWM5ODRhOWZlYzYwNTIzZjJkNTI5NzY4MmUxNjY0Ng==",
        "MDY6Q29tbWl0NDQ3MzkwNDQ6NWZkZGJlYjY3OGJkMmMzNmM1ZTVjODkxYWI4ZjJiMTQzY2VkNWJhZg==",
        "MDY6Q29tbWl0NDQ3MzkwNDQ6YzA4OGZkNmE3ZTFhMzhlOWQ1YTk4MTUyNjVjYjU3NWJiMDhkMDhmZg==",
    }
    await wait_deferred()
    fp = await _fetch_first_parents(
        None,
        "src-d/go-git",
        ["MDY6Q29tbWl0NDQ3MzkwNDQ6MzFlYWU3YjYxOWQxNjZjMzY2YmY1ZGY0OTkxZjA0YmE4Y2ViZWEwYQ==",
         "MDY6Q29tbWl0NDQ3MzkwNDQ6ZDJhMzhiNGE1OTY1ZDUyOTU2NjU2NjY0MDUxOWQwM2QyYmQxMGY2Yw=="],
        datetime(2015, 4, 5),
        datetime(2015, 5, 20),
        None, None, cache)
    await wait_deferred()
    assert fp == ground_truth
    with pytest.raises(Exception):
        await _fetch_first_parents(
            None,
            "src-d/go-git",
            ["MDY6Q29tbWl0NDQ3MzkwNDQ6MzFlYWU3YjYxOWQxNjZjMzY2YmY1ZGY0OTkxZjA0YmE4Y2ViZWEwYQ==",
             "MDY6Q29tbWl0NDQ3MzkwNDQ6ZDJhMzhiNGE1OTY1ZDUyOTU2NjU2NjY0MDUxOWQwM2QyYmQxMGY2Yw=="],
            datetime(2015, 4, 6),
            datetime(2015, 5, 20),
            None, None, cache)


@with_defer
async def test__fetch_repository_commits_full(mdb, pdb, dag, cache):
    branches, _ = await extract_branches(dag, mdb, None)
    cols = (Branch.commit_sha.key, Branch.commit_id.key, Branch.commit_date.key,
            Branch.repository_full_name.key)
    commits = await _fetch_repository_commits(dag, branches, cols, False, mdb, pdb, cache)
    await wait_deferred()
    assert len(commits) == 1
    assert len(commits["src-d/go-git"][0]) == 1919
    branches = branches.iloc[:1]
    commits = await _fetch_repository_commits(commits, branches, cols, False, mdb, pdb, cache)
    await wait_deferred()
    assert len(commits) == 1
    assert len(commits["src-d/go-git"][0]) == 1919  # without force-pushed commits
    commits = await _fetch_repository_commits(commits, branches, cols, True, mdb, pdb, cache)
    await wait_deferred()
    assert len(commits) == 1
    assert len(commits["src-d/go-git"][0]) == 1538  # without force-pushed commits


@with_defer
async def test__find_dead_merged_prs_smoke(mdb, pdb, dag):
    prs = await read_sql_query(
        select([PullRequest]).where(PullRequest.merged_at.isnot(None)),
        mdb, PullRequest, index=PullRequest.node_id.key)
    branches, _ = await extract_branches(["src-d/go-git"], mdb, None)
    branches = branches.iloc[:1]
    dead_prs = await _find_dead_merged_prs(prs, dag, branches, mdb, pdb, None)
    assert len(dead_prs) == 159
    dead_prs = await mdb.fetch_all(
        select([PullRequest.number]).where(PullRequest.node_id.in_(dead_prs.index)))
    assert {pr[0] for pr in dead_prs} == set(force_push_dropped_go_git_pr_numbers)


@with_defer
async def test__find_dead_merged_prs_no_branches(mdb, pdb, dag):
    prs = await read_sql_query(
        select([PullRequest]).where(PullRequest.merged_at.isnot(None)),
        mdb, PullRequest, index=PullRequest.node_id.key)
    branches, _ = await extract_branches(["src-d/go-git"], mdb, None)
    branches = branches.iloc[:1]
    branches[Branch.repository_full_name.key] = "xxx"
    dags = dag.copy()
    dags["xxx"] = _empty_dag()
    dead_prs = await _find_dead_merged_prs(prs, dags, branches, mdb, pdb, None)
    assert len(dead_prs) == 0
    branches = branches.iloc[:0]
    dead_prs = await _find_dead_merged_prs(prs, dags, branches, mdb, pdb, None)
    assert len(dead_prs) == 0


@with_defer
async def test__fetch_repository_first_commit_dates_pdb_cache(mdb, pdb, cache):
    fcd1 = await _fetch_repository_first_commit_dates(["src-d/go-git"], mdb, pdb, cache)
    await wait_deferred()
    fcd2 = await _fetch_repository_first_commit_dates(
        ["src-d/go-git"], Database("sqlite://"), pdb, None)
    fcd3 = await _fetch_repository_first_commit_dates(
        ["src-d/go-git"], Database("sqlite://"), Database("sqlite://"), cache)
    assert len(fcd1) == len(fcd2) == len(fcd3) == 1
    assert fcd1["src-d/go-git"] == fcd2["src-d/go-git"] == fcd3["src-d/go-git"]
    assert fcd1["src-d/go-git"].tzinfo == timezone.utc


def test_extract_subdag_smoke():
    hashes = np.array(["308a9f90707fb9d12cbcd28da1bc33da436386fe",
                       "33cafc14532228edca160e46af10341a8a632e3e",
                       "61a719e0ff7522cc0d129acb3b922c94a8a5dbca",
                       "a444ccadf5fddad6ad432c13a239c74636c7f94f"],
                      dtype="U40")
    vertexes = np.array([0, 1, 2, 3, 3], dtype=np.uint32)
    edges = np.array([3, 0, 0], dtype=np.uint32)
    heads = np.array(["61a719e0ff7522cc0d129acb3b922c94a8a5dbca"], dtype="U40")
    new_hashes, new_vertexes, new_edges = extract_subdag(hashes, vertexes, edges, heads)
    assert (new_hashes == np.array(["308a9f90707fb9d12cbcd28da1bc33da436386fe",
                                    "61a719e0ff7522cc0d129acb3b922c94a8a5dbca",
                                    "a444ccadf5fddad6ad432c13a239c74636c7f94f"],
                                   dtype="U40")).all()
    assert (new_vertexes == np.array([0, 1, 2, 2], dtype=np.uint32)).all()
    assert (new_edges == np.array([2, 0], dtype=np.uint32)).all()


def test_join_dags_smoke():
    hashes = np.array(["308a9f90707fb9d12cbcd28da1bc33da436386fe",
                       "33cafc14532228edca160e46af10341a8a632e3e",
                       "a444ccadf5fddad6ad432c13a239c74636c7f94f"],
                      dtype="U40")
    vertexes = np.array([0, 1, 2, 2], dtype=np.uint32)
    edges = np.array([2, 0], dtype=np.uint32)
    new_hashes, new_vertexes, new_edges = join_dags(
        hashes, vertexes, edges, [("61a719e0ff7522cc0d129acb3b922c94a8a5dbca",
                                   "308a9f90707fb9d12cbcd28da1bc33da436386fe"),
                                  ("308a9f90707fb9d12cbcd28da1bc33da436386fe",
                                   "a444ccadf5fddad6ad432c13a239c74636c7f94f")])
    assert (new_hashes == np.array(["308a9f90707fb9d12cbcd28da1bc33da436386fe",
                                    "33cafc14532228edca160e46af10341a8a632e3e",
                                    "61a719e0ff7522cc0d129acb3b922c94a8a5dbca",
                                    "a444ccadf5fddad6ad432c13a239c74636c7f94f"],
                                   dtype="U40")).all()
    assert (new_vertexes == np.array([0, 1, 2, 3, 3], dtype=np.uint32)).all()
    assert (new_edges == np.array([3, 0, 0], dtype=np.uint32)).all()


def test_mark_dag_access_smoke():
    hashes = np.array(["308a9f90707fb9d12cbcd28da1bc33da436386fe",
                       "33cafc14532228edca160e46af10341a8a632e3e",
                       "61a719e0ff7522cc0d129acb3b922c94a8a5dbca",
                       "a444ccadf5fddad6ad432c13a239c74636c7f94f"],
                      dtype="U40")
    vertexes = np.array([0, 1, 2, 3, 3], dtype=np.uint32)
    edges = np.array([3, 0, 0], dtype=np.uint32)
    heads = np.array(["33cafc14532228edca160e46af10341a8a632e3e",
                      "61a719e0ff7522cc0d129acb3b922c94a8a5dbca"], dtype="U40")
    marks = mark_dag_access(hashes, vertexes, edges, heads)
    assert (marks == np.array([1, 0, 1, 1], dtype=np.int64)).all()


"""
https://athenianco.atlassian.net/browse/DEV-250

async def test_map_prs_to_releases_miguel(mdb, pdb, release_match_setting_tag, cache):
    miguel_pr = await read_sql_query(select([PullRequest]).where(PullRequest.number == 907),
                                     mdb, PullRequest, index=PullRequest.node_id.key)
    # https://github.com/src-d/go-git/pull/907
    assert len(miguel_pr) == 1
    time_from = datetime(2018, 1, 1, tzinfo=timezone.utc)
    time_to = datetime(2020, 5, 1, tzinfo=timezone.utc)
    releases, matched_bys = await load_releases(
        ["src-d/go-git"], None, None, time_from, time_to,
        release_match_setting_tag, mdb, pdb, cache)
    released_prs, _ = await map_prs_to_releases(
        miguel_pr, releases, matched_bys, pd.DataFrame(), {}, time_to,
        release_match_setting_tag, mdb, pdb, cache)
    assert len(released_prs) == 1
"""
