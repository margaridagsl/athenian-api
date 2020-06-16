import argparse
import asyncio
from collections import defaultdict
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
import logging

import databases
import sentry_sdk
from sqlalchemy import and_, create_engine
from sqlalchemy.orm import Session, sessionmaker
from tqdm import tqdm

from athenian.api import add_logging_args, check_schema_versions, create_memcached, \
    setup_cache_metrics, setup_context
from athenian.api.controllers.features.entries import calc_pull_request_metrics_line_github
from athenian.api.controllers.settings import default_branch_alias, ReleaseMatch, \
    ReleaseMatchSetting
from athenian.api.models.state.models import ReleaseSetting, RepositorySet


def parse_args():
    """Go away linter."""
    parser = argparse.ArgumentParser()
    add_logging_args(parser)
    parser.add_argument("--metadata-db", required=True,
                        help="Metadata DB endpoint, e.g. postgresql://0.0.0.0:5432/metadata")
    parser.add_argument("--precomputed-db", required=True,
                        help="Precomputed DB endpoint, e.g. postgresql://0.0.0.0:5432/precomputed")
    parser.add_argument("--state-db", required=True,
                        help="State DB endpoint, e.g. postgresql://0.0.0.0:5432/state")
    parser.add_argument("--memcached", required=True,
                        help="memcached address, e.g. 0.0.0.0:11211")
    return parser.parse_args()


def main():
    """Go away linter."""
    log = logging.getLogger("heat_cache")
    args = parse_args()
    setup_context(log)
    sentry_sdk.add_breadcrumb(category="origin", message="heater", level="info")
    if not check_schema_versions(args.metadata_db, args.state_db, args.precomputed_db, log):
        return 1
    engine = create_engine(args.state_db)
    session = sessionmaker(bind=engine)()  # type: Session
    reposets = session.query(RepositorySet).all()
    time_to = datetime.combine(date.today() + timedelta(days=1),
                               datetime.min.time(),
                               tzinfo=timezone.utc)
    time_from = time_to - timedelta(days=365)

    async def async_run():
        cache = create_memcached(args.memcached, log)
        setup_cache_metrics(cache, {}, None)
        for v in cache.metrics["context"].values():
            v.set(defaultdict(int))
        mdb = databases.Database(args.metadata_db)
        await mdb.connect()
        pdb = databases.Database(args.precomputed_db)
        await pdb.connect()
        pdb.metrics = {
            "hits": ContextVar("pdb_hits", default=defaultdict(int)),
            "misses": ContextVar("pdb_misses", default=defaultdict(int)),
        }

        for reposet in tqdm(reposets):
            repos = {r.split("/", 1)[1] for r in reposet.items}
            settings = {}
            rows = session.query(ReleaseSetting).filter(and_(
                ReleaseSetting.account_id == reposet.owner_id,
                ReleaseSetting.repository.in_(reposet.items)))
            for row in rows:
                settings[row.repository] = ReleaseMatchSetting(
                    branches=row.branches,
                    tags=row.tags,
                    match=ReleaseMatch(row.match),
                )
            for repo in reposet.items:
                if repo not in settings:
                    settings[repo] = ReleaseMatchSetting(
                        branches=default_branch_alias,
                        tags=".*",
                        match=ReleaseMatch.tag_or_branch,
                    )
            sentry_sdk.add_breadcrumb(
                category="account", message=str(reposet.owner_id), level="info")
            try:
                await calc_pull_request_metrics_line_github(
                    ["pr-lead-time"],
                    [[time_from, time_to]],
                    repos,
                    {},
                    False,
                    settings,
                    mdb,
                    pdb,
                    cache,
                )
            except Exception as e:
                sentry_sdk.capture_exception(e)

    asyncio.run(async_run())


if __name__ == "__main__":
    exit(main())
