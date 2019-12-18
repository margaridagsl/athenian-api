# coding: utf-8

from athenian.api import serialization
from athenian.api.models.base_model_ import Model


class MetricID(Model):
    """NOTE: This class is auto generated by OpenAPI Generator (https://openapi-generator.tech).

    Do not edit the class manually.
    """

    """
    allowed enum values
    """
    TIME_TO_REVIEW = "pr-time-to-review"
    TIME_TO_MERGE = "pr-time-to-merge"
    TIME_TO_RELEASE = "pr-time-to-release"
    LEAD_TIME = "pr-lead-time"

    def __init__(self):
        """MetricID - a model defined in OpenAPI"""
        self.openapi_types = {}

        self.attribute_map = {}

    @classmethod
    def from_dict(cls, dikt: dict) -> "MetricID":
        """Returns the dict as a model

        :param dikt: A dict.
        :return: The MetricID of this MetricID.
        """
        return serialization.deserialize_model(dikt, cls)
