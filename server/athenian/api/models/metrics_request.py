# coding: utf-8

from datetime import date
from typing import List

from athenian.api import serialization
from athenian.api.models.base_model_ import Model
from athenian.api.models.for_set import ForSet
from athenian.api.models.granularity import Granularity
from athenian.api.models.metric_id import MetricID


class MetricsRequest(Model):
    """This class is auto generated by OpenAPI Generator (https://openapi-generator.tech)."""

    def __init__(
        self,
        _for: List[ForSet] = None,
        metrics: List[MetricID] = None,
        date_from: date = None,
        date_to: date = None,
        granularity: Granularity = None,
    ):
        """MetricsRequest - a model defined in OpenAPI

        :param _for: The _for of this MetricsRequest.
        :param metrics: The metrics of this MetricsRequest.
        :param date_from: The date_from of this MetricsRequest.
        :param date_to: The date_to of this MetricsRequest.
        :param granularity: The granularity of this MetricsRequest.
        """
        self.openapi_types = {
            "_for": List[ForSet],
            "metrics": List[MetricID],
            "date_from": date,
            "date_to": date,
            "granularity": Granularity,
        }

        self.attribute_map = {
            "_for": "for",
            "metrics": "metrics",
            "date_from": "date_from",
            "date_to": "date_to",
            "granularity": "granularity",
        }

        self.__for = _for
        self._metrics = metrics
        self._date_from = date_from
        self._date_to = date_to
        self._granularity = granularity

    @classmethod
    def from_dict(cls, dikt: dict) -> "MetricsRequest":
        """Returns the dict as a model

        :param dikt: A dict.
        :return: The MetricsRequest of this MetricsRequest.
        """
        return serialization.deserialize_model(dikt, cls)

    @property
    def _for(self):
        """Gets the _for of this MetricsRequest.

        Sets of developers and repositories to calculate the metrics for.

        :return: The _for of this MetricsRequest.
        :rtype: List[ForSet]
        """
        return self.__for

    @_for.setter
    def _for(self, _for):
        """Sets the _for of this MetricsRequest.

        Sets of developers and repositories to calculate the metrics for.

        :param _for: The _for of this MetricsRequest.
        :type _for: List[ForSet]
        """
        if _for is None:
            raise ValueError("Invalid value for `_for`, must not be `None`")

        self.__for = _for

    @property
    def metrics(self):
        """Gets the metrics of this MetricsRequest.

        Requested metric identifiers.

        :return: The metrics of this MetricsRequest.
        :rtype: List[MetricID]
        """
        return self._metrics

    @metrics.setter
    def metrics(self, metrics):
        """Sets the metrics of this MetricsRequest.

        Requested metric identifiers.

        :param metrics: The metrics of this MetricsRequest.
        :type metrics: List[MetricID]
        """
        if metrics is None:
            raise ValueError("Invalid value for `metrics`, must not be `None`")

        self._metrics = metrics

    @property
    def date_from(self):
        """Gets the date_from of this MetricsRequest.

        The date from when to start measuring the metrics.

        :return: The date_from of this MetricsRequest.
        :rtype: date
        """
        return self._date_from

    @date_from.setter
    def date_from(self, date_from):
        """Sets the date_from of this MetricsRequest.

        The date from when to start measuring the metrics.

        :param date_from: The date_from of this MetricsRequest.
        :type date_from: date
        """
        if date_from is None:
            raise ValueError("Invalid value for `date_from`, must not be `None`")

        self._date_from = date_from

    @property
    def date_to(self):
        """Gets the date_to of this MetricsRequest.

        The date up to which to measure the metrics.

        :return: The date_to of this MetricsRequest.
        :rtype: date
        """
        return self._date_to

    @date_to.setter
    def date_to(self, date_to):
        """Sets the date_to of this MetricsRequest.

        The date up to which to measure the metrics.

        :param date_to: The date_to of this MetricsRequest.
        :type date_to: date
        """
        if date_to is None:
            raise ValueError("Invalid value for `date_to`, must not be `None`")

        self._date_to = date_to

    @property
    def granularity(self):
        """Gets the granularity of this MetricsRequest.

        :return: The granularity of this MetricsRequest.
        :rtype: Granularity
        """
        return self._granularity

    @granularity.setter
    def granularity(self, granularity):
        """Sets the granularity of this MetricsRequest.

        :param granularity: The granularity of this MetricsRequest.
        :type granularity: Granularity
        """
        if granularity is None:
            raise ValueError("Invalid value for `granularity`, must not be `None`")

        self._granularity = granularity
