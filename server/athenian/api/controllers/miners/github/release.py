import asyncio
from datetime import datetime, timedelta, timezone
from itertools import chain, groupby
import marshal
import pickle
import re
from typing import Collection, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

import aiomcache
import aiosqlite
import asyncpg
import databases
import numpy as np
import pandas as pd
import sentry_sdk
from sqlalchemy import and_, desc, distinct, func, insert, or_, select
from sqlalchemy.cprocessors import str_to_datetime
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.sql.elements import BinaryExpression

from athenian.api.async_read_sql_query import postprocess_datetime, read_sql_query, wrap_sql_query
from athenian.api.cache import cached, gen_cache_key, max_exptime
from athenian.api.controllers.miners.github.branches import extract_branches
from athenian.api.controllers.miners.github.release_accelerated import update_history
from athenian.api.controllers.settings import default_branch_alias, Match, ReleaseMatchSetting
from athenian.api.models.metadata import PREFIXES
from athenian.api.models.metadata.github import Branch, PullRequest, PushCommit, Release, User
from athenian.api.models.precomputed.models import GitHubCommitHistory
from athenian.api.tracing import sentry_span


matched_by_column = "matched_by"


@sentry_span
async def load_releases(repos: Iterable[str],
                        time_from: datetime,
                        time_to: datetime,
                        settings: Dict[str, ReleaseMatchSetting],
                        db: databases.Database,
                        cache: Optional[aiomcache.Client],
                        index: Optional[Union[str, Sequence[str]]] = None,
                        ) -> pd.DataFrame:
    """
    Fetch releases from the metadata DB according to the match settings.

    :param repos: Repositories in which to search for releases *without the service prefix*.
    """
    assert isinstance(db, databases.Database)
    repos_by_tag_only = []
    repos_by_tag_or_branch = []
    repos_by_branch = []
    prefix = PREFIXES["github"]
    for repo in repos:
        v = settings[prefix + repo]
        if v.match == Match.tag:
            repos_by_tag_only.append(repo)
        elif v.match == Match.tag_or_branch:
            repos_by_tag_or_branch.append(repo)
        elif v.match == Match.branch:
            repos_by_branch.append(repo)
    result = []
    if repos_by_tag_only:
        result.append(_match_releases_by_tag(
            repos_by_tag_only, time_from, time_to, settings, db))
    if repos_by_tag_or_branch:
        result.append(_match_releases_by_tag_or_branch(
            repos_by_tag_or_branch, time_from, time_to, settings, db, cache))
    if repos_by_branch:
        result.append(_match_releases_by_branch(
            repos_by_branch, time_from, time_to, settings, db, cache))
    result = await asyncio.gather(*result, return_exceptions=True)
    for r in result:
        if isinstance(r, Exception):
            raise r
    result = pd.concat(result) if result else _dummy_releases_df()
    if index is not None:
        result.set_index(index, inplace=True)
    else:
        result.reset_index(drop=True, inplace=True)
    return result


def _dummy_releases_df():
    return pd.DataFrame(
        columns=[c.name for c in Release.__table__.columns] + [matched_by_column])


tag_by_branch_probe_lookaround = timedelta(weeks=4)


@sentry_span
async def _match_releases_by_tag_or_branch(repos: Iterable[str],
                                           time_from: datetime,
                                           time_to: datetime,
                                           settings: Dict[str, ReleaseMatchSetting],
                                           db: databases.Database,
                                           cache: Optional[aiomcache.Client],
                                           ) -> pd.DataFrame:
    probe = await read_sql_query(
        select([distinct(Release.repository_full_name)])
        .where(and_(Release.repository_full_name.in_(repos),
                    Release.published_at.between(
            time_from - tag_by_branch_probe_lookaround,
            time_to + tag_by_branch_probe_lookaround),
        )),
        db, [Release.repository_full_name.key])
    matched = []
    repos_by_tag = probe[Release.repository_full_name.key].values
    if repos_by_tag.size > 0:
        matched.append(_match_releases_by_tag(
            repos_by_tag, time_from, time_to, settings, db))
    repos_by_branch = set(repos) - set(repos_by_tag)
    if repos_by_branch:
        matched.append(_match_releases_by_branch(
            repos_by_branch, time_from, time_to, settings, db, cache))
    matched = await asyncio.gather(*matched, return_exceptions=True)
    for m in matched:
        if isinstance(m, Exception):
            raise m
    return pd.concat(matched)


@sentry_span
async def _match_releases_by_tag(repos: Iterable[str],
                                 time_from: datetime,
                                 time_to: datetime,
                                 settings: Dict[str, ReleaseMatchSetting],
                                 db: databases.Database,
                                 ) -> pd.DataFrame:
    with sentry_sdk.start_span(op="fetch_tags"):
        releases = await read_sql_query(
            select([Release])
            .where(and_(Release.published_at.between(time_from, time_to),
                        Release.repository_full_name.in_(repos),
                        Release.commit_id.isnot(None)))
            .order_by(desc(Release.published_at)),
            db, Release, index=[Release.repository_full_name.key, Release.tag.key])
    releases = releases[~releases.index.duplicated(keep="first")]
    regexp_cache = {}
    matched = []
    prefix = PREFIXES["github"]
    for repo in repos:
        try:
            repo_releases = releases.loc[repo]
        except KeyError:
            continue
        if repo_releases.empty:
            continue
        regexp = settings[prefix + repo].tags
        if not regexp.endswith("$"):
            regexp += "$"
        # note: dict.setdefault() is not good here because re.compile() will be evaluated
        try:
            regexp = regexp_cache[regexp]
        except KeyError:
            regexp = regexp_cache[regexp] = re.compile(regexp)
        tags_matched = repo_releases.index[repo_releases.index.str.match(regexp)]
        matched.append([(repo, tag) for tag in tags_matched])
    # this shows up in the profile but I cannot make it faster
    releases = releases.loc[list(chain.from_iterable(matched))]
    releases.reset_index(inplace=True)
    releases[matched_by_column] = Match.tag.value
    return releases


@sentry_span
async def _match_releases_by_branch(repos: Iterable[str],
                                    time_from: datetime,
                                    time_to: datetime,
                                    settings: Dict[str, ReleaseMatchSetting],
                                    db: databases.Database,
                                    cache: Optional[aiomcache.Client],
                                    ) -> pd.DataFrame:
    branches, default_branches = await extract_branches(repos, db, cache)
    regexp_cache = {}
    branches_matched = []
    prefix = PREFIXES["github"]
    for repo, repo_branches in branches.groupby(Branch.repository_full_name.key):
        regexp = settings[prefix + repo].branches
        default_branch = default_branches[repo]
        regexp = regexp.replace(default_branch_alias, default_branch)
        if not regexp.endswith("$"):
            regexp += "$"
        # note: dict.setdefault() is not good here because re.compile() will be evaluated
        try:
            regexp = regexp_cache[regexp]
        except KeyError:
            regexp = regexp_cache[regexp] = re.compile(regexp)
        branches_matched.append(
            repo_branches[repo_branches[Branch.branch_name.key].str.match(regexp)])
    if not branches_matched:
        return _dummy_releases_df()
    branches_matched = pd.concat(branches_matched, copy=False)

    mp_tasks = [
        _fetch_merge_points(repo, commit_id, branch_name, time_from, time_to, db, cache)
        for repo, commit_id, branch_name in zip(
            branches_matched[Branch.repository_full_name.key].values,
            branches_matched[Branch.commit_id.key].values,
            branches_matched[Branch.branch_name.key].values,
        )
    ]
    merge_points = await asyncio.gather(*mp_tasks, return_exceptions=True)
    merge_points_by_repo = {}
    for repo, mp in zip(branches_matched[Branch.repository_full_name.key].values, merge_points):
        if isinstance(mp, Exception):
            raise mp from None
        try:
            merge_points_by_repo[repo].update(mp)
        except KeyError:
            merge_points_by_repo[repo] = mp
    pseudo_releases = await asyncio.gather(
        *(_fetch_merge_commit_releases(*rm, db, cache) for rm in merge_points_by_repo.items()),
        return_exceptions=True)
    for r in pseudo_releases:
        if isinstance(r, Exception):
            raise r from None
    if not pseudo_releases:
        return _dummy_releases_df()
    return pd.concat(pseudo_releases, copy=False)


@cached(
    exptime=24 * 60 * 60,  # 1 day
    serialize=pickle.dumps,
    deserialize=pickle.loads,
    key=lambda repo, commit_id, branch_name, time_from, time_to, **_: (
        repo, commit_id, branch_name, time_from.timestamp(), time_to.timestamp(),
    ) if time_to <= datetime.combine(
        datetime.now(timezone.utc).date(), datetime.min.time(), tzinfo=timezone.utc,
    ) else None,
    refresh_on_access=True,
)
async def _fetch_merge_points(repo: str,
                              commit_id: str,
                              branch_name: str,
                              time_from: datetime,
                              time_to: datetime,
                              db: databases.Database,
                              cache: Optional[aiomcache.Client],
                              ) -> Set[str]:
    async with db.connection() as conn:
        first_parents = await _fetch_first_parents(commit_id, conn, cache)
        # we filter afterwards to increase the cache efficiency
        mp = {h for h, cdt in first_parents if time_from <= cdt < time_to}
        rows = await conn.fetch_all(select([PullRequest.merge_commit_id]).where(and_(
            PullRequest.repository_full_name == repo,
            PullRequest.base_ref == branch_name,
            PullRequest.merged_at.between(time_from, time_to),
            PullRequest.merge_commit_id.isnot(None),
        )))
        mp.update(r[0] for r in rows)
        return mp


@cached(
    exptime=24 * 60 * 60,  # 1 day
    serialize=pickle.dumps,
    deserialize=pickle.loads,
    key=lambda repo, merge_points, **_: (repo, ",".join(sorted(merge_points))),
    refresh_on_access=True,
)
async def _fetch_merge_commit_releases(repo: str,
                                       merge_points: Set[str],
                                       db: databases.Database,
                                       cache: Optional[aiomcache.Client]) -> pd.DataFrame:
    commits = await read_sql_query(
        select([PushCommit]).where(PushCommit.node_id.in_(merge_points))
        .order_by(desc(PushCommit.commit_date)),
        db, PushCommit)
    gh_merge = ((commits[PushCommit.committer_name.key] == "GitHub")
                & (commits[PushCommit.committer_email.key] == "noreply@github.com"))
    commits[PushCommit.author_login.key].where(
        gh_merge, commits.loc[~gh_merge, PushCommit.committer_login.key], inplace=True)
    return pd.DataFrame({
        Release.author.key: commits[PushCommit.author_login.key],
        Release.commit_id.key: commits[PushCommit.node_id.key],
        Release.id.key: commits[PushCommit.node_id.key] + "_" + repo,
        Release.name.key: commits[PushCommit.sha.key],
        Release.published_at.key: commits[PushCommit.committed_date.key],
        Release.repository_full_name.key: repo,
        Release.sha.key: commits[PushCommit.sha.key],
        Release.tag.key: None,
        Release.url.key: commits[PushCommit.url.key],
        matched_by_column: [Match.branch.value] * len(commits),
    })


@sentry_span
async def map_prs_to_releases(prs: pd.DataFrame,
                              time_from: datetime,
                              time_to: datetime,
                              release_settings: Dict[str, ReleaseMatchSetting],
                              mdb: databases.Database,
                              pdb: databases.Database,
                              cache: Optional[aiomcache.Client],
                              ) -> pd.DataFrame:
    """Match the merged pull requests to the nearest releases that include them."""
    assert isinstance(time_to, datetime)
    assert isinstance(mdb, databases.Database)
    assert isinstance(pdb, databases.Database)
    pr_releases = _new_map_df()
    if prs.empty:
        return pr_releases
    repos = prs[PullRequest.repository_full_name.key].unique()
    earliest_merge = prs[PullRequest.merged_at.key].min() - timedelta(minutes=1)
    if earliest_merge >= time_from:
        releases = await load_releases(
            repos, earliest_merge, time_to, release_settings, mdb, cache)
    else:
        # we have to load releases in two separate batches: before and after time_from
        # that's because the release strategy can change depending on the time range
        # see ENG-710 and ENG-725
        releases_new = await load_releases(repos, time_from, time_to, release_settings, mdb, cache)
        matched_bys = _extract_matched_bys_from_releases(releases_new)
        # these matching rules must be applied in the past to stay consistent
        consistent_release_settings = {}
        for k, setting in release_settings.items():
            consistent_release_settings[k] = ReleaseMatchSetting(
                tags=setting.tags,
                branches=setting.branches,
                match=Match(matched_bys.get(k.split("/", 1)[1], setting.match)),
            )
        releases_old = await load_releases(
            repos, earliest_merge, time_from, consistent_release_settings, mdb, cache)
        releases = pd.concat([releases_new, releases_old], copy=False)
        releases.reset_index(drop=True, inplace=True)
    if cache is not None:
        matched_bys = _extract_matched_bys_from_releases(releases)
        pr_releases.append(await _load_pr_releases_from_cache(
            prs.index, prs[PullRequest.repository_full_name.key].values, matched_bys,
            release_settings, cache))
    merged_prs = prs[~prs.index.isin(pr_releases.index)]
    missed_releases = await _map_prs_to_releases(merged_prs, releases, mdb, pdb)
    if cache is not None:
        await _cache_pr_releases(missed_releases, release_settings, cache)
    return pr_releases.append(missed_releases)


def _extract_matched_bys_from_releases(releases: pd.DataFrame) -> dict:
    return {
        r: g.head(1).iat[0, 1] for r, g in releases[
            [Release.repository_full_name.key, matched_by_column]
        ].groupby(Release.repository_full_name.key, sort=False, as_index=False)
        if len(g) > 0
    }


index_name = "pull_request_node_id"


def _new_map_df(data=None) -> pd.DataFrame:
    columns = [Release.published_at.key,
               Release.author.key,
               Release.url.key,
               Release.repository_full_name.key,
               matched_by_column]
    if data is None:
        return pd.DataFrame(columns=columns, index=pd.Index([], name=index_name))
    return pd.DataFrame.from_records(data, columns=[index_name] + columns, index=index_name)


def _gen_released_pr_cache_key(pr_id: str,
                               repo: str,
                               release_settings: Dict[str, ReleaseMatchSetting],
                               ) -> bytes:
    return gen_cache_key(
        "release_github|6|%s|%s", pr_id, release_settings[PREFIXES["github"] + repo])


async def _load_pr_releases_from_cache(prs: Iterable[str],
                                       pr_repos: Iterable[str],
                                       matched_bys: Dict[str, int],
                                       release_settings: Dict[str, ReleaseMatchSetting],
                                       cache: aiomcache.Client) -> pd.DataFrame:
    batch_size = 32
    records = []
    utc = timezone.utc
    keys = [_gen_released_pr_cache_key(pr, repo, release_settings)
            for pr, repo in zip(prs, pr_repos)]
    for key, val in zip(keys, chain.from_iterable(
            [await cache.multi_get(*(k for _, k in g))
             for _, g in groupby(enumerate(keys), lambda ik: ik[0] // batch_size)])):
        if val is None:
            continue
        released_at, released_by, released_url, repo, matched_by = marshal.loads(val)
        if matched_by != matched_bys.get(repo, matched_by):
            continue
        released_at = datetime.fromtimestamp(released_at).replace(tzinfo=utc)
        records.append((key, released_at, released_by, released_url, repo, matched_by))
    df = _new_map_df(records)
    return df


async def _map_prs_to_releases(prs: pd.DataFrame,
                               releases: pd.DataFrame,
                               mdb: databases.Database,
                               pdb: databases.Database,
                               ) -> pd.DataFrame:
    releases = dict(list(releases.groupby(Release.repository_full_name.key, sort=False)))
    histories = await _fetch_release_histories(releases, mdb, pdb)
    released_prs = []
    for repo, repo_prs in prs.groupby(PullRequest.repository_full_name.key, sort=False):
        try:
            repo_releases = releases[repo]
            history = histories[repo]
        except KeyError:
            # no releases exist for this repo
            continue
        for pr_id, merge_sha in zip(repo_prs.index,
                                    repo_prs[PullRequest.merge_commit_sha.key].values):
            try:
                items = history[merge_sha]
            except KeyError:
                continue
            ri = items[0]
            if ri < 0:
                continue
            r = repo_releases.xs(ri)
            released_prs.append((pr_id,
                                 r[Release.published_at.key],
                                 r[Release.author.key],
                                 r[Release.url.key],
                                 repo,
                                 r[matched_by_column]))
    released_prs = _new_map_df(released_prs)
    released_prs[Release.published_at.key] = np.maximum(
        released_prs[Release.published_at.key],
        prs.loc[released_prs.index, PullRequest.merged_at.key])
    return postprocess_datetime(released_prs)


async def _fetch_release_histories(releases: Dict[str, pd.DataFrame],
                                   mdb: databases.Database,
                                   pdb: databases.Database,
                                   ) -> Dict[str, Dict[str, List[str]]]:
    histories = {}

    async def fetch_release_history(repo, repo_releases):
        dag = await _fetch_commit_history_dag(
            repo, repo_releases[Release.commit_id.key].values, mdb, pdb,
            commit_shas=repo_releases[Release.sha.key].values)
        histories[repo] = history = {k: [-1, *v] for k, v in dag.items()}
        release_hashes = set(repo_releases[Release.sha.key].values)
        for rel_index, rel_sha in zip(repo_releases.index.values,
                                      repo_releases[Release.sha.key].values):
            assert rel_sha in history
            update_history(history, rel_sha, rel_index, release_hashes)

    errors = await asyncio.gather(*(fetch_release_history(*r) for r in releases.items()),
                                  return_exceptions=True)
    for e in errors:
        if e is not None:
            raise e from None
    return histories


async def _cache_pr_releases(releases: pd.DataFrame,
                             release_settings: Dict[str, ReleaseMatchSetting],
                             cache: aiomcache.Client) -> None:
    mt = max_exptime
    for pr_id, released_at, released_by, release_url, repo, matched_by in zip(
            releases.index, releases[Release.published_at.key],
            releases[Release.author.key].values, releases[Release.url.key].values,
            releases[Release.repository_full_name.key].values,
            releases[matched_by_column].values):
        key = _gen_released_pr_cache_key(pr_id, repo, release_settings)
        t = released_at.timestamp(), released_by, release_url, repo, int(matched_by)
        await cache.set(key, marshal.dumps(t), exptime=mt)


@cached(
    exptime=24 * 60 * 60,  # 1 day
    serialize=pickle.dumps,
    deserialize=pickle.loads,
    key=lambda commit_id, **_: (commit_id,),
    version=2,
    refresh_on_access=True,
)
async def _fetch_first_parents(commit_id: str,
                               conn: databases.core.Connection,
                               cache: Optional[aiomcache.Client],
                               ) -> List[Tuple[str, datetime]]:
    # Git parent-child is reversed github_node_commit_parents' parent-child.
    assert isinstance(conn, databases.core.Connection)
    quote = "`" if isinstance(conn.raw_connection, aiosqlite.core.Connection) else ""
    query = f"""
        WITH RECURSIVE commit_first_parents AS (
            SELECT
                p.child_id AS parent,
                cc.id AS parent_id,
                cc.committed_date as committed_date
            FROM
                github_node_commit_parents p
                    LEFT JOIN github_node_commit pc ON p.parent_id = pc.id
                    LEFT JOIN github_node_commit cc ON p.child_id = cc.id
            WHERE
                p.parent_id = '{commit_id}' AND p.{quote}index{quote} = 0
            UNION
                SELECT
                    p.child_id AS parent,
                    cc.id AS parent_id,
                    cc.committed_date as committed_date
                FROM
                    github_node_commit_parents p
                        INNER JOIN commit_first_parents h ON h.parent = p.parent_id
                        LEFT JOIN github_node_commit pc ON p.parent_id = pc.id
                        LEFT JOIN github_node_commit cc ON p.child_id = cc.id
                WHERE p.{quote}index{quote} = 0
        ) SELECT
            parent_id,
            committed_date
        FROM
            commit_first_parents
        UNION
            SELECT
                id as parent_id,
                committed_date
            FROM
                github_node_commit
            WHERE
                id = '{commit_id}';"""
    utc = timezone.utc
    # we must use string key lookups here because integers do not work with Postgres
    first_parents = []
    for r in await conn.fetch_all(query):
        cid = r["parent_id"]
        cdt = r["committed_date"]
        if isinstance(cdt, str):  # sqlite
            cdt = str_to_datetime(cdt).replace(tzinfo=utc)
        first_parents.append((cid, cdt))
    return first_parents


async def _fetch_commit_history_dag(repo: str,
                                    commit_ids: Iterable[str],
                                    mdb: databases.Database,
                                    pdb: databases.Database,
                                    commit_shas: Optional[Iterable[str]] = None,
                                    ) -> Dict[str, List[str]]:
    # Git parent-child is reversed github_node_commit_parents' parent-child.
    assert isinstance(mdb, databases.Database)
    assert isinstance(pdb, databases.Database)

    default_version = GitHubCommitHistory.__table__ \
        .columns[GitHubCommitHistory.format_version.key].default.arg
    tasks = [
        pdb.fetch_val(select([GitHubCommitHistory.dag])
                      .where(and_(GitHubCommitHistory.repository_full_name == repo,
                                  GitHubCommitHistory.format_version == default_version))),
    ]
    if commit_shas is None:
        tasks.append(mdb.fetch_all(select([PushCommit.sha])
                                   .where(PushCommit.node_id.in_(commit_ids))))
        dag, commit_shas = await asyncio.gather(*tasks, return_exceptions=True)
        for r in (dag, commit_shas):
            if isinstance(r, Exception):
                raise r from None
        commit_shas = [row[PushCommit.sha.key] for row in commit_shas]
    else:
        dag = await tasks[0]
    if dag is not None:
        dag = marshal.loads(dag)
        need_update = False
        for commit_sha in commit_shas:
            if commit_sha not in dag:
                need_update = True
                break
        if not need_update:
            return dag

    # query credits: @dennwc
    query = f"""
    WITH RECURSIVE commit_history AS (
        SELECT
            p.child_id AS parent,
            pc.oid AS child_oid,
            cc.oid AS parent_oid
        FROM
            github_node_commit_parents p
                LEFT JOIN github_node_commit pc ON p.parent_id = pc.id
                LEFT JOIN github_node_commit cc ON p.child_id = cc.id
        WHERE
            p.parent_id IN ('{"', '".join(commit_ids)}')
        UNION
            SELECT
                p.child_id AS parent,
                pc.oid AS child_oid,
                cc.oid AS parent_oid
            FROM
                github_node_commit_parents p
                    INNER JOIN commit_history h ON h.parent = p.parent_id
                    LEFT JOIN github_node_commit pc ON p.parent_id = pc.id
                    LEFT JOIN github_node_commit cc ON p.child_id = cc.id
    ) SELECT
        parent_oid,
        child_oid
    FROM
        commit_history;"""
    dag = {}
    async with mdb.connection() as conn:
        if isinstance(conn.raw_connection, asyncpg.connection.Connection):
            # this works much faster then iterate() / fetch_all()
            async with conn._query_lock:
                rows = await conn.raw_connection.fetch(query)
        else:
            rows = await conn.fetch_all(query)
    for r in rows:
        # reverse the order so that parent-child matches github_node_commit_parents again
        child, parent = r
        try:
            dag[parent].append(child)
        except KeyError:
            # first iteration
            dag[parent] = [child]
        dag.setdefault(child, [])
    if not dag:
        # initial commit(s)
        return {sha: [] for sha in commit_shas}
    else:
        values = GitHubCommitHistory(repository_full_name=repo, dag=marshal.dumps(dag)) \
            .create_defaults().explode(with_primary_keys=True)
        if pdb.url.dialect in ("postgres", "postgresql"):
            sql = postgres_insert(GitHubCommitHistory).values(values)
            sql = sql.on_conflict_do_update(
                constraint=GitHubCommitHistory.__table__.primary_key,
                set_={GitHubCommitHistory.dag.key: sql.excluded.dag,
                      GitHubCommitHistory.updated_at.key: sql.excluded.updated_at})
        elif pdb.url.dialect == "sqlite":
            sql = insert(GitHubCommitHistory).values(values).prefix_with("OR REPLACE")
        else:
            raise AssertionError("Unsupported database dialect: %s" % pdb.url.dialect)
        await pdb.execute(sql)
    return dag


async def _find_old_released_prs(releases: pd.DataFrame,
                                 time_boundary: datetime,
                                 authors: Collection[str],
                                 mergers: Collection[str],
                                 pr_blacklist: Optional[BinaryExpression],
                                 mdb: databases.Database,
                                 pdb: databases.Database,
                                 ) -> Iterable[Mapping]:
    observed_commits, _, _ = await _extract_released_commits(releases, time_boundary, mdb, pdb)
    repo = releases.iloc[0][Release.repository_full_name.key] if not releases.empty else ""
    filters = [
        PullRequest.merged_at < time_boundary,
        PullRequest.repository_full_name == repo,
        PullRequest.merge_commit_sha.in_(observed_commits),
        PullRequest.hidden.is_(False),
    ]
    if len(authors) and len(mergers):
        filters.append(or_(
            PullRequest.user_login.in_(authors),
            PullRequest.merged_by_login.in_(mergers),
        ))
    elif len(authors):
        filters.append(PullRequest.user_login.in_(authors))
    elif len(mergers):
        filters.append(PullRequest.merged_by_login.in_(mergers))
    if pr_blacklist is not None:
        filters.append(pr_blacklist)
    return await mdb.fetch_all(select([PullRequest]).where(and_(*filters)))


async def _extract_released_commits(releases: pd.DataFrame,
                                    time_boundary: datetime,
                                    mdb: databases.Database,
                                    pdb: databases.Database,
                                    ) -> Tuple[Dict[str, List[str]], pd.DataFrame, Dict[str, str]]:
    repo = releases[Release.repository_full_name.key].unique()
    assert len(repo) == 1
    repo = repo[0]
    resolved_releases = set()
    hash_to_release = {h: rid for rid, h in zip(releases.index, releases[Release.sha.key].values)}
    new_releases = releases[releases[Release.published_at.key] >= time_boundary]
    boundary_releases = set()
    dag = await _fetch_commit_history_dag(
        repo, new_releases[Release.commit_id.key].values, mdb, pdb,
        commit_shas=new_releases[Release.sha.key].values,
    )

    for rid, root in zip(new_releases.index, new_releases[Release.sha.key].values):
        if rid in resolved_releases:
            continue
        parents = [root]
        visited = set()
        while parents:
            x = parents.pop()
            if x in visited:
                continue
            else:
                visited.add(x)
            try:
                xrid = hash_to_release[x]
            except KeyError:
                pass
            else:
                pubdt = releases.loc[xrid, Release.published_at.key]
                if pubdt >= time_boundary:
                    resolved_releases.add(xrid)
                else:
                    boundary_releases.add(xrid)
                    continue
            parents.extend(dag[x])

    # we need to traverse full history from boundary_releases and subtract it from the full DAG
    ignored_commits = set()
    for rid in boundary_releases:
        release = releases.loc[rid]
        if release[Release.sha.key] in ignored_commits:
            continue
        parents = [release[Release.sha.key]]
        while parents:
            x = parents.pop()
            if x in ignored_commits:
                continue
            ignored_commits.add(x)
            children = dag[x]
            parents.extend(children)
    for c in ignored_commits:
        try:
            del dag[c]
        except KeyError:
            continue
    return dag, new_releases, hash_to_release


@sentry_span
async def map_releases_to_prs(repos: Iterable[str],
                              time_from: datetime,
                              time_to: datetime,
                              authors: Collection[str],
                              mergers: Collection[str],
                              release_settings: Dict[str, ReleaseMatchSetting],
                              mdb: databases.Database,
                              pdb: databases.Database,
                              cache: Optional[aiomcache.Client],
                              pr_blacklist: Optional[BinaryExpression] = None) -> pd.DataFrame:
    """Find pull requests which were released between `time_from` and `time_to` but merged before \
    `time_from`.

    :param authors: Required PR authors.
    :param mergers: Required PR mergers.
    :return: pd.DataFrame with found PRs that were created before `time_from` and released \
             between `time_from` and `time_to`.
    """
    assert isinstance(time_from, datetime)
    assert isinstance(time_to, datetime)
    assert isinstance(mdb, databases.Database)
    assert isinstance(pdb, databases.Database)
    old_from = time_from - timedelta(days=365)  # find PRs not older than 365 days before time_from
    releases = await load_releases(
        repos, old_from, time_to, release_settings, mdb, cache, index=Release.id.key)
    prs = []
    for _, repo_releases in releases.groupby(Release.repository_full_name.key, sort=False):
        prs.append(_find_old_released_prs(
            repo_releases, time_from, authors, mergers, pr_blacklist, mdb, pdb))
    if prs:
        prs = await asyncio.gather(*prs, return_exceptions=True)
        for pr in prs:
            if isinstance(pr, Exception):
                raise pr
        return wrap_sql_query(chain.from_iterable(prs), PullRequest, index=PullRequest.node_id.key)
    return pd.DataFrame(columns=[c.name for c in PullRequest.__table__.columns
                                 if c.name != PullRequest.node_id.key])


async def mine_releases(releases: pd.DataFrame,
                        time_boundary: datetime,
                        mdb: databases.Database,
                        pdb: databases.Database,
                        cache: Optional[aiomcache.Client]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Collect details about each release published after `time_boundary` and calculate added \
    and deleted line statistics."""
    assert isinstance(mdb, databases.Database)
    assert isinstance(pdb, databases.Database)
    miners = (
        _mine_monorepo_releases(repo_releases, time_boundary, mdb, pdb, cache)
        for _, repo_releases in releases.groupby(Release.repository_full_name.key, sort=False)
    )
    stats = await asyncio.gather(*miners, return_exceptions=True)
    for s in stats:
        if isinstance(s, BaseException):
            raise s from None
    user_columns = [User.login, User.avatar_url]
    if stats:
        stats = pd.concat(stats, copy=False)
        people = set(chain(chain.from_iterable(stats["commit_authors"]), stats["publisher"]))
        prefix = PREFIXES["github"]
        stats["publisher"] = prefix + stats["publisher"]
        stats["repository"] = prefix + stats["repository"]
        for calist in stats["commit_authors"].values:
            for i, v in enumerate(calist):
                calist[i] = prefix + v
        avatars = await read_sql_query(
            select(user_columns).where(User.login.in_(people)), mdb, user_columns)
        avatars[User.login.key] = prefix + avatars[User.login.key]
        return stats, avatars
    return pd.DataFrame(), pd.DataFrame(columns=[c.key for c in user_columns])


@cached(
    exptime=10 * 60,
    serialize=pickle.dumps,
    deserialize=pickle.loads,
    key=lambda releases, **_: (sorted(releases.index),),
    refresh_on_access=True,
)
async def _mine_monorepo_releases(releases: pd.DataFrame,
                                  time_boundary: datetime,
                                  mdb: databases.Database,
                                  pdb: databases.Database,
                                  cache: Optional[aiomcache.Client]) -> pd.DataFrame:
    dag, new_releases, hash_to_release = await _extract_released_commits(
        releases, time_boundary, mdb, pdb)
    stop_hashes = set(new_releases[Release.sha.key])
    owned_commits = {}  # type: Dict[str, Set[str]]
    neighbours = {}  # type: Dict[str, Set[str]]

    def find_owned_commits(sha):
        try:
            return owned_commits[sha]
        except KeyError:
            accessible, boundaries, leaves = _traverse_commits(dag, sha, stop_hashes)
            neighbours[sha] = boundaries.union(leaves)
            for b in boundaries:
                accessible -= find_owned_commits(b)
            owned_commits[sha] = accessible
            return accessible

    for sha in new_releases[Release.sha.key].values:
        find_owned_commits(sha)
    data = []
    commit_df_columns = [PushCommit.additions, PushCommit.deletions, PushCommit.author_login]
    for release in new_releases.itertuples():
        sha = getattr(release, Release.sha.key)
        included_commits = owned_commits[sha]
        repo = getattr(release, Release.repository_full_name.key)
        df = await read_sql_query(
            select(commit_df_columns)
            .where(and_(PushCommit.repository_full_name == repo,
                        PushCommit.sha.in_(included_commits))),
            mdb, commit_df_columns)
        try:
            previous_published_at = max(releases.loc[hash_to_release[n], Release.published_at.key]
                                        for n in neighbours[sha] if n in hash_to_release)
        except ValueError:
            # no previous releases
            previous_published_at = await mdb.fetch_val(
                select([func.min(PushCommit.committed_date)])
                .where(and_(PushCommit.repository_full_name.in_(repo),
                            PushCommit.sha.in_(included_commits))))
        published_at = getattr(release, Release.published_at.key)
        data.append([
            getattr(release, Release.name.key) or getattr(release, Release.tag.key),
            repo,
            getattr(release, Release.url.key),
            published_at,
            (published_at - previous_published_at) if previous_published_at is not None
            else timedelta(0),
            df[PushCommit.additions.key].sum(),
            df[PushCommit.deletions.key].sum(),
            len(included_commits),
            getattr(release, Release.author.key),
            sorted(set(df[PushCommit.author_login.key]) - {None}),
        ])
    return pd.DataFrame.from_records(data, columns=[
        "name", "repository", "url", "published", "age", "added_lines", "deleted_lines", "commits",
        "publisher", "commit_authors"])


def _traverse_commits(dag: Dict[str, List[str]],
                      root: str,
                      stops: Set[str]) -> Tuple[Set[str], Set[str], Set[str]]:
    parents = [root]
    visited = set()
    boundaries = set()
    leaves = set()
    while parents:
        x = parents.pop()
        if x in visited:
            continue
        if x in stops and x != root:
            boundaries.add(x)
            continue
        try:
            children = dag[x]
            parents.extend(children)
        except KeyError:
            leaves.add(x)
            continue
        visited.add(x)
    return visited, boundaries, leaves
