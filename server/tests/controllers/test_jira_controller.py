from datetime import date, datetime
import json

from dateutil.tz import tzutc
import numpy as np
import pytest

from athenian.api import FriendlyJson
from athenian.api.models.web import CalculatedJIRAHistogram, CalculatedJIRAMetricValues, \
    CalculatedLinearMetricValues, FoundJIRAStuff, JIRAEpic, JIRALabel, JIRAMetricID, \
    JIRAPriority, JIRAUser
from athenian.api.models.web.jira_epic_child import JIRAEpicChild


async def test_filter_jira_smoke(client, headers):
    body = {
        "date_from": "2019-10-13",
        "date_to": "2020-01-23",
        "timezone": 120,
        "account": 1,
        "exclude_inactive": False,
    }
    response = await client.request(
        method="POST", path="/v1/filter/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    model = FoundJIRAStuff.from_dict(json.loads(body))
    assert model.labels == [
        JIRALabel(title="API",
                  last_used=datetime(2020, 7, 13, 17, 45, 58, tzinfo=tzutc()),
                  issues_count=4, kind="component"),
        JIRALabel(title="Webapp",
                  last_used=datetime(2020, 7, 13, 17, 45, 58, tzinfo=tzutc()),
                  issues_count=1, kind="component"),
        JIRALabel(title="accounts", last_used=datetime(2020, 4, 3, 18, 47, 43, tzinfo=tzutc()),
                  issues_count=1, kind="regular"),
        JIRALabel(title="bug", last_used=datetime(2020, 6, 1, 7, 15, 7, tzinfo=tzutc()),
                  issues_count=16, kind="regular"),
        JIRALabel(title="code-quality", last_used=datetime(2020, 6, 4, 11, 35, 12, tzinfo=tzutc()),
                  issues_count=1, kind="regular"),
        JIRALabel(title="discarded", last_used=datetime(2020, 6, 1, 1, 27, 23, tzinfo=tzutc()),
                  issues_count=4, kind="regular"),
        JIRALabel(title="discussion", last_used=datetime(2020, 3, 31, 21, 16, 11, tzinfo=tzutc()),
                  issues_count=3, kind="regular"),
        JIRALabel(title="feature", last_used=datetime(2020, 4, 3, 18, 48, tzinfo=tzutc()),
                  issues_count=6, kind="regular"),
        JIRALabel(title="functionality",
                  last_used=datetime(2020, 6, 4, 11, 35, 15, tzinfo=tzutc()), issues_count=1,
                  kind="regular"),
        JIRALabel(title="internal-story", last_used=datetime(2020, 6, 1, 7, 15, 7, tzinfo=tzutc()),
                  issues_count=11, kind="regular"),
        JIRALabel(title="needs-specs", last_used=datetime(2020, 4, 6, 13, 25, 2, tzinfo=tzutc()),
                  issues_count=4, kind="regular"),
        JIRALabel(title="onboarding", last_used=datetime(2020, 7, 13, 17, 45, 58, tzinfo=tzutc()),
                  issues_count=1, kind="regular"),
        JIRALabel(title="performance", last_used=datetime(2020, 3, 31, 21, 16, 5, tzinfo=tzutc()),
                  issues_count=1, kind="regular"),
        JIRALabel(title="user-story", last_used=datetime(2020, 4, 3, 18, 48, tzinfo=tzutc()),
                  issues_count=5, kind="regular"),
        JIRALabel(title="webapp", last_used=datetime(2020, 4, 3, 18, 47, 6, tzinfo=tzutc()),
                  issues_count=1, kind="regular"),
    ]
    assert model.epics == [
        JIRAEpic(id="DEV-70", title="Show the installation progress in the waiting page",
                 updated=datetime(2020, 7, 27, 16, 56, 22, tzinfo=tzutc()),
                 children=[JIRAEpicChild("DEV-365", "Released", "Story"),
                           JIRAEpicChild("DEV-183", "Closed", "Task"),
                           JIRAEpicChild("DEV-315", "Released", "Story"),
                           JIRAEpicChild("DEV-228", "Released", "Task"),
                           JIRAEpicChild("DEV-364", "Released", "Story")]),
        JIRAEpic(id="ENG-1", title="Evaluate our product and process internally",
                 updated=datetime(2020, 6, 1, 7, 19, tzinfo=tzutc()), children=[]),
    ]
    assert model.issue_types == ["Design document", "Epic", "Story", "Subtask", "Task"]
    assert model.users == [
        JIRAUser(name="David Pordomingo",
                 avatar="https://avatar-management--avatars.us-west-2.prod.public.atl-paas.net/initials/DP-4.png",  # noqa
                 type="atlassian"),
        JIRAUser(name="Denys Smirnov",
                 avatar="https://avatar-management--avatars.us-west-2.prod.public.atl-paas.net/initials/DS-1.png",  # noqa
                 type="atlassian"),
        JIRAUser(name="Kuba Podgórski",
                 avatar="https://secure.gravatar.com/avatar/ec2f95fe07b5ffec5cde78781f433b68?d=https%3A%2F%2Favatar-management--avatars.us-west-2.prod.public.atl-paas.net%2Finitials%2FKP-3.png",  # noqa
                 type="atlassian"),
        JIRAUser(name="Lou Marvin Caraig",
                 avatar="https://avatar-management--avatars.us-west-2.prod.public.atl-paas.net/initials/LC-0.png",  # noqa
                 type="atlassian"),
        JIRAUser(name="Marcelo Novaes",
                 avatar="https://avatar-management--avatars.us-west-2.prod.public.atl-paas.net/initials/MN-4.png",  # noqa
                 type="atlassian"),
        JIRAUser(name="Oleksandr Chabaiev",
                 avatar="https://avatar-management--avatars.us-west-2.prod.public.atl-paas.net/initials/OC-5.png",  # noqa
                 type="atlassian"),
        JIRAUser(name="Vadim Markovtsev",
                 avatar="https://avatar-management--avatars.us-west-2.prod.public.atl-paas.net/initials/VM-6.png",  # noqa
                 type="atlassian"),
        JIRAUser(name="Waren Long",
                 avatar="https://avatar-management--avatars.us-west-2.prod.public.atl-paas.net/initials/WL-5.png",  # noqa
                 type="atlassian")]
    assert model.priorities == [
        JIRAPriority(name="Medium",
                     image="https://athenianco.atlassian.net/images/icons/priorities/medium.svg",
                     rank=3,
                     color="EA7D24"),
        JIRAPriority(name="Low",
                     image="https://athenianco.atlassian.net/images/icons/priorities/low.svg",
                     rank=4,
                     color="2A8735"),
        JIRAPriority(name="None",
                     image="https://athenianco.atlassian.net/images/icons/priorities/trivial.svg",
                     rank=6,
                     color="9AA1B2")]


@pytest.mark.parametrize("exclude_inactive, labels, epics, types, users, priorities", [
    [False, 33, 34,
     ["Bug", "Design Document", "Epic", "Incident", "Story", "Sub-task", "Subtask", "Task"],
     15, 6],
    [True, 29, 13,
     ["Bug", "Epic", "Incident", "Story", "Sub-task", "Task"],
     11, 6],
])
async def test_filter_jira_exclude_inactive(
        client, headers, exclude_inactive, labels, epics, types, users, priorities):
    body = {
        "date_from": "2020-09-13",
        "date_to": "2020-10-23",
        "timezone": 120,
        "account": 1,
        "exclude_inactive": exclude_inactive,
    }
    response = await client.request(
        method="POST", path="/v1/filter/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    model = FoundJIRAStuff.from_dict(json.loads(body))
    assert len(model.labels) == labels
    assert len(model.epics) == epics
    assert model.issue_types == types
    assert len(model.users) == users
    assert len(model.priorities) == priorities


async def test_filter_jira_disabled_projects(client, headers, disabled_dev):
    body = {
        "date_from": "2019-10-13",
        "date_to": "2020-01-23",
        "timezone": 120,
        "account": 1,
        "exclude_inactive": False,
    }
    response = await client.request(
        method="POST", path="/v1/filter/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    model = FoundJIRAStuff.from_dict(json.loads(body))
    assert model.labels == [
        JIRALabel(title="accounts", last_used=datetime(2020, 4, 3, 18, 47, 43, tzinfo=tzutc()),
                  issues_count=1, kind="regular"),
        JIRALabel(title="bug", last_used=datetime(2020, 6, 1, 7, 15, 7, tzinfo=tzutc()),
                  issues_count=16, kind="regular"),
        JIRALabel(title="discarded", last_used=datetime(2020, 6, 1, 1, 27, 23, tzinfo=tzutc()),
                  issues_count=4, kind="regular"),
        JIRALabel(title="discussion", last_used=datetime(2020, 3, 31, 21, 16, 11, tzinfo=tzutc()),
                  issues_count=3, kind="regular"),
        JIRALabel(title="feature", last_used=datetime(2020, 4, 3, 18, 48, tzinfo=tzutc()),
                  issues_count=6, kind="regular"),
        JIRALabel(title="internal-story", last_used=datetime(2020, 6, 1, 7, 15, 7, tzinfo=tzutc()),
                  issues_count=11, kind="regular"),
        JIRALabel(title="needs-specs", last_used=datetime(2020, 4, 6, 13, 25, 2, tzinfo=tzutc()),
                  issues_count=4, kind="regular"),
        JIRALabel(title="performance", last_used=datetime(2020, 3, 31, 21, 16, 5, tzinfo=tzutc()),
                  issues_count=1, kind="regular"),
        JIRALabel(title="user-story", last_used=datetime(2020, 4, 3, 18, 48, tzinfo=tzutc()),
                  issues_count=5, kind="regular"),
        JIRALabel(title="webapp", last_used=datetime(2020, 4, 3, 18, 47, 6, tzinfo=tzutc()),
                  issues_count=1, kind="regular")]
    assert model.epics == [
        JIRAEpic(id="ENG-1", title="Evaluate our product and process internally",
                 updated=datetime(2020, 6, 1, 7, 19, tzinfo=tzutc()), children=[]),
    ]
    assert model.issue_types == ["Design document", "Epic", "Story", "Subtask", "Task"]


@pytest.mark.parametrize("account, date_to, timezone, status", [
    (1, "2015-10-12", 0, 400),
    (2, "2020-10-12", 0, 422),
    (3, "2020-10-12", 0, 404),
    (1, "2020-10-12", 100500, 400),
])
async def test_filter_jira_nasty_input(client, headers, account, date_to, timezone, status):
    body = {
        "date_from": "2015-10-13",
        "date_to": date_to,
        "timezone": timezone,
        "account": account,
        "exclude_inactive": True,
    }
    response = await client.request(
        method="POST", path="/v1/filter/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == status, "Response body is : " + body


@pytest.mark.parametrize("exclude_inactive", [False, True])
async def test_jira_metrics_smoke(client, headers, exclude_inactive):
    body = {
        "date_from": "2020-01-01",
        "date_to": "2020-10-23",
        "timezone": 120,
        "account": 1,
        "metrics": [JIRAMetricID.JIRA_RAISED, JIRAMetricID.JIRA_RESOLVED],
        "exclude_inactive": exclude_inactive,
        "granularities": ["all", "2 month"],
    }
    response = await client.request(
        method="POST", path="/v1/metrics/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    body = json.loads(body)
    assert len(body) == 2
    items = [CalculatedJIRAMetricValues.from_dict(i) for i in body]
    assert items[0].granularity == "all"
    assert items[0].with_ is None
    assert items[0].values == [CalculatedLinearMetricValues(
        date=date(2019, 12, 31),
        values=[1765, 1623],
        confidence_mins=[None] * 2,
        confidence_maxs=[None] * 2,
        confidence_scores=[None] * 2,
    )]
    assert items[1].granularity == "2 month"
    assert items[1].with_ is None
    assert len(items[1].values) == 5
    assert items[1].values[0] == CalculatedLinearMetricValues(
        date=date(2019, 12, 31),
        values=[160, 39],
        confidence_mins=[None] * 2,
        confidence_maxs=[None] * 2,
        confidence_scores=[None] * 2,
    )
    assert items[1].values[-1] == CalculatedLinearMetricValues(
        date=date(2020, 8, 31),
        values=[266, 243],
        confidence_mins=[None] * 2,
        confidence_maxs=[None] * 2,
        confidence_scores=[None] * 2,
    )


@pytest.mark.parametrize("account, metrics, date_from, date_to, timezone, granularities, status", [
    (1, [JIRAMetricID.JIRA_RAISED], "2020-01-01", "2020-04-01", 120, ["all"], 200),
    (2, [JIRAMetricID.JIRA_RAISED], "2020-01-01", "2020-04-01", 120, ["all"], 422),
    (3, [JIRAMetricID.JIRA_RAISED], "2020-01-01", "2020-04-01", 120, ["all"], 404),
    (1, [], "2020-01-01", "2020-04-01", 120, ["all"], 200),
    (1, None, "2020-01-01", "2020-04-01", 120, ["all"], 400),
    (1, [JIRAMetricID.JIRA_RAISED], "2020-05-01", "2020-04-01", 120, ["all"], 400),
    (1, [JIRAMetricID.JIRA_RAISED], "2020-01-01", "2020-04-01", 100500, ["all"], 400),
    (1, [JIRAMetricID.JIRA_RAISED], "2020-01-01", "2020-04-01", 120, ["whatever"], 400),
])
async def test_jira_metrics_nasty_input1(
        client, headers, account, metrics, date_from, date_to, timezone, granularities, status):
    body = {
        "date_from": date_from,
        "date_to": date_to,
        "timezone": timezone,
        "account": account,
        "metrics": metrics,
        "with": [],
        "exclude_inactive": True,
        "granularities": granularities,
    }
    response = await client.request(
        method="POST", path="/v1/metrics/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == status, "Response body is : " + body


async def test_jira_metrics_priorities(client, headers):
    body = {
        "date_from": "2020-01-01",
        "date_to": "2020-10-20",
        "timezone": 0,
        "account": 1,
        "metrics": [JIRAMetricID.JIRA_RAISED],
        "exclude_inactive": True,
        "granularities": ["all"],
        "priorities": ["high"],
    }
    response = await client.request(
        method="POST", path="/v1/metrics/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    body = json.loads(body)
    assert len(body) == 1
    items = [CalculatedJIRAMetricValues.from_dict(i) for i in body]
    assert items[0].granularity == "all"
    assert items[0].values[0].values == [410]


async def test_jira_metrics_types(client, headers):
    body = {
        "date_from": "2020-01-01",
        "date_to": "2020-10-20",
        "timezone": 0,
        "account": 1,
        "metrics": [JIRAMetricID.JIRA_RAISED],
        "exclude_inactive": True,
        "granularities": ["all"],
        "types": ["tASK"],
    }
    response = await client.request(
        method="POST", path="/v1/metrics/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    body = json.loads(body)
    assert len(body) == 1
    items = [CalculatedJIRAMetricValues.from_dict(i) for i in body]
    assert items[0].granularity == "all"
    assert items[0].values[0].values == [686]


async def test_jira_metrics_epics(client, headers):
    body = {
        "date_from": "2020-01-01",
        "date_to": "2020-10-20",
        "timezone": 0,
        "account": 1,
        "metrics": [JIRAMetricID.JIRA_RAISED],
        "exclude_inactive": True,
        "granularities": ["all"],
        "epics": ["DEV-70", "DEV-843"],
    }
    response = await client.request(
        method="POST", path="/v1/metrics/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    body = json.loads(body)
    assert len(body) == 1
    items = [CalculatedJIRAMetricValues.from_dict(i) for i in body]
    assert items[0].granularity == "all"
    assert items[0].values[0].values == [38]


async def test_jira_metrics_labels(client, headers):
    body = {
        "date_from": "2020-01-01",
        "date_to": "2020-10-20",
        "timezone": 0,
        "account": 1,
        "metrics": [JIRAMetricID.JIRA_RAISED],
        "exclude_inactive": True,
        "granularities": ["all"],
        "labels_include": ["PERFORmance"],
        "labels_exclude": ["buG"],
    }
    response = await client.request(
        method="POST", path="/v1/metrics/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    body = json.loads(body)
    assert len(body) == 1
    items = [CalculatedJIRAMetricValues.from_dict(i) for i in body]
    assert items[0].granularity == "all"
    assert items[0].values[0].values == [147]  # it is 148 without labels_exclude


@pytest.mark.parametrize("assignees, reporters, commenters, count", [
    (["Vadim markovtsev"], ["waren long"], ["lou Marvin caraig"], 1177),
    (["Vadim markovtsev"], [], [], 536),
    ([], ["waren long"], [], 567),
    ([], [], ["lou Marvin caraig"], 252),
])
async def test_jira_metrics_people(client, headers, assignees, reporters, commenters, count):
    body = {
        "date_from": "2020-01-01",
        "date_to": "2020-10-20",
        "timezone": 0,
        "account": 1,
        "metrics": [JIRAMetricID.JIRA_RAISED],
        "exclude_inactive": True,
        "granularities": ["all"],
        "with": [{
            "assignees": assignees,
            "reporters": reporters,
            "commenters": commenters,
        }],
    }
    response = await client.request(
        method="POST", path="/v1/metrics/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    body = json.loads(body)
    assert len(body) == 1
    items = [CalculatedJIRAMetricValues.from_dict(i) for i in body]
    assert items[0].granularity == "all"
    assert items[0].with_.to_dict() == {
        "assignees": assignees,
        "reporters": reporters,
        "commenters": commenters,
    }
    assert items[0].values[0].values == [count]


async def test_jira_metrics_teams(client, headers):
    body = {
        "date_from": "2020-01-01",
        "date_to": "2020-10-20",
        "timezone": 0,
        "account": 1,
        "metrics": [JIRAMetricID.JIRA_RAISED],
        "exclude_inactive": True,
        "granularities": ["all"],
        "with": [{
            "assignees": ["vadim Markovtsev"],
        }, {
            "reporters": ["waren Long"],
        }],
    }
    response = await client.request(
        method="POST", path="/v1/metrics/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    body = json.loads(body)
    assert len(body) == 2
    items = [CalculatedJIRAMetricValues.from_dict(i) for i in body]
    assert items[0].granularity == "all"
    assert items[0].values[0].values == [536]
    assert items[0].with_.to_dict() == {"assignees": ["vadim Markovtsev"]}
    assert items[1].values[0].values == [567]
    assert items[1].with_.to_dict() == {"reporters": ["waren Long"]}


@pytest.mark.parametrize("metric, exclude_inactive, n", [
    (JIRAMetricID.JIRA_OPEN, False, 208),
    (JIRAMetricID.JIRA_OPEN, True, 196),
    (JIRAMetricID.JIRA_RESOLVED, False, 850),
    (JIRAMetricID.JIRA_RESOLVED, True, 850),
    (JIRAMetricID.JIRA_RESOLUTION_RATE, False, 0.9594137542277339),
])
async def test_jira_metrics_counts(client, headers, metric, exclude_inactive, n):
    body = {
        "date_from": "2020-06-01",
        "date_to": "2020-10-23",
        "timezone": 120,
        "account": 1,
        "metrics": [metric],
        "exclude_inactive": exclude_inactive,
        "granularities": ["all"],
    }
    response = await client.request(
        method="POST", path="/v1/metrics/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    body = json.loads(body)
    assert len(body) == 1
    items = [CalculatedJIRAMetricValues.from_dict(i) for i in body]
    assert items[0].granularity == "all"
    assert items[0].values == [CalculatedLinearMetricValues(
        date=date(2020, 5, 31),
        values=[n],
        confidence_mins=[None],
        confidence_maxs=[None],
        confidence_scores=[None],
    )]


@pytest.mark.parametrize("metric, value, score, cmin, cmax", [
    (JIRAMetricID.JIRA_LIFE_TIME, "758190s", 72, "654210s", "868531s"),
    (JIRAMetricID.JIRA_LEAD_TIME, "304289s", 51, "226105s", "375748s"),
])
async def test_jira_metrics_bug_times(client, headers, metric, value, score, cmin, cmax):
    np.random.seed(7)
    body = {
        "date_from": "2016-01-01",
        "date_to": "2020-10-23",
        "timezone": 120,
        "account": 1,
        "metrics": [metric],
        "types": ["BUG"],
        "exclude_inactive": False,
        "granularities": ["all", "1 year"],
    }
    response = await client.request(
        method="POST", path="/v1/metrics/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    body = json.loads(body)
    assert len(body) == 2
    items = [CalculatedJIRAMetricValues.from_dict(i) for i in body]
    assert items[0].granularity == "all"
    assert items[0].values == [CalculatedLinearMetricValues(
        date=date(2015, 12, 31),
        values=[value],
        confidence_mins=[cmin],
        confidence_maxs=[cmax],
        confidence_scores=[score],
    )]


async def test_jira_metrics_disabled_projects(client, headers, disabled_dev):
    body = {
        "date_from": "2020-01-01",
        "date_to": "2020-10-23",
        "timezone": 120,
        "account": 1,
        "metrics": [JIRAMetricID.JIRA_RAISED, JIRAMetricID.JIRA_RESOLVED],
        "exclude_inactive": False,
        "granularities": ["all", "2 month"],
    }
    response = await client.request(
        method="POST", path="/v1/metrics/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    items = [CalculatedJIRAMetricValues.from_dict(i) for i in json.loads(body)]
    assert items[0].values == [CalculatedLinearMetricValues(
        date=date(2019, 12, 31),
        values=[768, 824],
        confidence_mins=[None] * 2,
        confidence_maxs=[None] * 2,
        confidence_scores=[None] * 2,
    )]


@pytest.mark.parametrize("with_, ticks, frequencies, interquartile", [
    [None,
     [["60s", "122s", "249s", "507s", "1033s", "2105s", "4288s", "8737s", "17799s", "36261s",
       "73870s", "150489s", "306576s", "624554s", "1272338s", "2591999s"]],
     [[351, 7, 12, 27, 38, 70, 95, 103, 68, 76, 120, 116, 132, 114, 285]],
     [{"left": "1255s", "right": "618082s"}],
     ],
    [[{"assignees": ["Vadim Markovtsev"]}, {"reporters": ["Waren Long"]}],
     [["60s", "158s", "417s", "1102s", "2909s", "7676s", "20258s", "53456s", "141062s", "372237s",
       "982262s", "2591999s"],
      ["60s", "136s", "309s", "704s", "1601s", "3639s", "8271s", "18801s", "42732s", "97125s",
       "220753s", "501745s", "1140405s", "2591999s"]],
     [[60, 4, 18, 36, 76, 88, 42, 33, 31, 19, 81],
      [129, 3, 6, 9, 18, 23, 21, 32, 56, 43, 57, 46, 59]],
     [{"left": "3062s", "right": "194589s"}, {"left": "60s", "right": "364828s"}],
     ],
])
async def test_jira_histograms_smoke(client, headers, with_, ticks, frequencies, interquartile):
    for _ in range(2):
        body = {
            "histograms": [{
                "metric": JIRAMetricID.JIRA_LEAD_TIME,
                "scale": "log",
            }],
            **({"with": with_} if with_ is not None else {}),
            "date_from": "2015-10-13",
            "date_to": "2020-11-01",
            "exclude_inactive": False,
            "account": 1,
        }
        response = await client.request(
            method="POST", path="/v1/histograms/jira", headers=headers, json=body,
        )
        body = (await response.read()).decode("utf-8")
        assert response.status == 200, "Response body is : " + body
        body = FriendlyJson.loads(body)
        for item in body:
            CalculatedJIRAHistogram.from_dict(item)
        for histogram, hticks, hfrequencies, hinterquartile, hwith_ in zip(
                body, ticks, frequencies, interquartile, with_ or [None]):
            assert histogram == {
                "metric": JIRAMetricID.JIRA_LEAD_TIME,
                "scale": "log",
                "ticks": hticks,
                "frequencies": hfrequencies,
                "interquartile": hinterquartile,
                **({"with": hwith_} if hwith_ is not None else {}),
            }


async def test_jira_histogram_disabled_projects(client, headers, disabled_dev):
    body = {
        "histograms": [{
            "metric": JIRAMetricID.JIRA_LEAD_TIME,
            "scale": "log",
        }],
        "date_from": "2015-10-13",
        "date_to": "2020-11-01",
        "exclude_inactive": False,
        "account": 1,
    }
    response = await client.request(
        method="POST", path="/v1/histograms/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == 200, "Response body is : " + body
    histogram = FriendlyJson.loads(body)[0]
    assert histogram == {
        "metric": JIRAMetricID.JIRA_LEAD_TIME,
        "scale": "log",
        "ticks": ["60s", "128s", "275s", "590s", "1266s", "2714s", "5818s", "12470s", "26730s",
                  "57293s", "122803s", "263218s", "564186s", "1209285s", "2591999s"],
        "frequencies": [214, 3, 6, 20, 25, 31, 33, 33, 31, 55, 54, 55, 74, 222],
        "interquartile": {"left": "149s", "right": "1273387s"},
    }


@pytest.mark.parametrize(
    "metric, date_to, bins, scale, ticks, quantiles, account, status",
    [
        (JIRAMetricID.JIRA_RAISED, "2020-01-23", 10, "log", None, [0, 1], 1, 400),
        (JIRAMetricID.JIRA_LEAD_TIME, "2020-01-23", -1, "log", None, [0, 1], 1, 400),
        (JIRAMetricID.JIRA_LEAD_TIME, "2020-01-23", 10, "xxx", None, [0, 1], 1, 400),
        (JIRAMetricID.JIRA_LEAD_TIME, "2015-01-23", 10, "linear", None, [0, 1], 1, 400),
        (JIRAMetricID.JIRA_LEAD_TIME, "2020-01-23", 10, "linear", None, [0, 1], 2, 422),
        (JIRAMetricID.JIRA_LEAD_TIME, "2020-01-23", 10, "linear", None, [0, 1], 4, 404),
        (JIRAMetricID.JIRA_LEAD_TIME, "2015-11-23", 10, "linear", None, [-1, 1], 1, 400),
        (JIRAMetricID.JIRA_LEAD_TIME, "2015-11-23", None, None, None, [0, 1], 1, 200),
        (JIRAMetricID.JIRA_LEAD_TIME, "2015-11-23", None, None, [], [0, 1], 1, 400),
    ],
)
async def test_jira_histograms_nasty_input(
        client, headers, metric, date_to, bins, scale, ticks, quantiles, account, status):
    body = {
        "histograms": [{
            "metric": metric,
            **({"scale": scale} if scale is not None else {}),
            **({"bins": bins} if bins is not None else {}),
            **({"ticks": ticks} if ticks is not None else {}),
        }],
        "date_from": "2015-10-13",
        "date_to": date_to,
        "quantiles": quantiles,
        "exclude_inactive": False,
        "account": account,
    }
    response = await client.request(
        method="POST", path="/v1/histograms/jira", headers=headers, json=body,
    )
    body = (await response.read()).decode("utf-8")
    assert response.status == status, "Response body is : " + body
