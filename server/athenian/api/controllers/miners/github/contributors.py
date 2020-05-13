import asyncio
from collections import defaultdict
from datetime import datetime
import marshal
from typing import Collection, List, Optional

import aiomcache
import databases
from sqlalchemy import and_, func, or_, select

from athenian.api.cache import cached
from athenian.api.models.metadata.github import PullRequest, PullRequestComment, \
    PullRequestReview, PushCommit, Release, User


async def mine_contributors(repos: Collection[str],
                            db: databases.Database,
                            time_from: Optional[datetime] = None,
                            time_to: Optional[datetime] = None,
                            with_stats: Optional[bool] = True,
                            cache: Optional[aiomcache.Client] = None) -> List[dict]:
    """Discover developers who made any important action in the given repositories and \
    in the given time frame."""
    time_from = time_from or datetime(1970, 1, 1)
    time_to = time_to or datetime.now()

    return await _mine_contributors(repos, time_from, time_to, db, with_stats, cache)


@cached(
    exptime=5 * 60,
    serialize=marshal.dumps,
    deserialize=marshal.loads,
    key=lambda repos, time_from, time_to, **_: (
        ",".join(repos), time_from.timestamp(), time_to.timestamp()),
)
async def _mine_contributors(repos: Collection[str],
                             time_from: datetime,
                             time_to: datetime,
                             db: databases.Database,
                             with_stats: bool,
                             cache: Optional[aiomcache.Client]) -> List[dict]:
    assert isinstance(time_from, datetime)
    assert isinstance(time_to, datetime)

    async def fetch_prs():
        return await db.fetch_all(
            select([PullRequest.user_login, func.count(PullRequest.user_login)])
            .where(and_(PullRequest.repository_full_name.in_(repos),
                        PullRequest.hidden.is_(False),
                        or_(PullRequest.created_at.between(time_from, time_to),
                            and_(PullRequest.created_at < time_to,
                                 PullRequest.closed_at.is_(None)),
                            PullRequest.closed_at.between(time_from, time_to),
                            PullRequest.updated_at.between(time_from, time_to))))
            .group_by(PullRequest.user_login))

    async def fetch_comments():
        return await db.fetch_all(
            select([PullRequestComment.user_login, func.count(PullRequestComment.user_login)])
            .where(and_(PullRequestComment.repository_full_name.in_(repos),
                        PullRequestComment.created_at.between(time_from, time_to),
                        ))
            .group_by(PullRequestComment.user_login))

    async def fetch_commit_authors():
        return await db.fetch_all(
            select([PushCommit.author_login, func.count(PushCommit.author_login)])
            .where(and_(PushCommit.repository_full_name.in_(repos),
                        PushCommit.committed_date.between(time_from, time_to)))
            .group_by(PushCommit.author_login))

    async def fetch_commit_committers():
        return await db.fetch_all(
            select([PushCommit.committer_login, func.count(PushCommit.committer_login)])
            .where(and_(PushCommit.repository_full_name.in_(repos),
                        PushCommit.committed_date.between(time_from, time_to)))
            .group_by(PushCommit.committer_login))

    async def fetch_reviews():
        return await db.fetch_all(
            select([PullRequestReview.user_login, func.count(PullRequestReview.user_login)])
            .where(and_(PullRequestReview.repository_full_name.in_(repos),
                        PullRequestReview.submitted_at.between(time_from, time_to)))
            .group_by(PullRequestReview.user_login))

    async def fetch_releases():
        return await db.fetch_all(
            select([Release.author, func.count(Release.author)])
            .where(and_(Release.repository_full_name.in_(repos),
                        Release.published_at.between(time_from, time_to)))
            .group_by(Release.author))

    data = await asyncio.gather(
        fetch_prs(), fetch_comments(), fetch_commit_authors(), fetch_commit_committers(),
        fetch_reviews(), fetch_releases())

    stats = defaultdict(dict)
    for rows, key in zip(data, ("prs", "commenter", "commit_author", "commit_committer",
                                "reviewer", "releaser")):
        for row in rows:
            stats[row[0]][key] = row[1]

    keys = ["login", "email", "avatar_url", "name"]
    user_details = await db.fetch_all(
        select([getattr(User, k) for k in keys])
        .where(User.login.in_(stats.keys())))

    indexed_keys = list(enumerate(keys))
    contribs = []
    for ud in user_details:
        c = {k: ud[i] for i, k in indexed_keys}
        if with_stats:
            c = {**c, "stats": stats[c["login"]]}

        contribs.append(c)

    return contribs
