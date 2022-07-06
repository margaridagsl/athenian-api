from operator import attrgetter
from typing import Any

from ariadne import QueryType
from graphql import GraphQLResolveInfo

from athenian.api.async_utils import gather
from athenian.api.internal.account import get_metadata_account_ids
from athenian.api.internal.team import (
    fetch_team_members_recursively,
    get_all_team_members,
    get_root_team,
    get_team_from_db,
)
from athenian.api.models.state.models import Team
from athenian.api.tracing import sentry_span

query = QueryType()


@query.field("members")
@sentry_span
async def resolve_members(
    obj: Any,
    info: GraphQLResolveInfo,
    accountId: int,
    teamId: int,
    recursive: bool,
) -> Any:
    """Serve members()."""
    sdb, mdb, cache = info.context.sdb, info.context.mdb, info.context.cache

    team, meta_ids = await gather(
        # teamId 0 means root team
        get_root_team(accountId, sdb) if teamId == 0 else get_team_from_db(accountId, teamId, sdb),
        get_metadata_account_ids(accountId, sdb, cache),
    )

    if recursive:
        member_ids = await fetch_team_members_recursively(accountId, sdb, team[Team.id.name])
    else:
        member_ids = team[Team.members.name]
    members = await get_all_team_members(member_ids, accountId, meta_ids, mdb, sdb, cache)

    # Contributor web model is exactly the same as GraphQL Member
    return sorted(members.values(), key=attrgetter("login"))
