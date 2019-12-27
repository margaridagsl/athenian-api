# coding: utf-8
from typing import List, Optional

from athenian.api import serialization
from athenian.api.models.base_model_ import Model
from athenian.api.models.developer_set import DeveloperSet
from athenian.api.models.repository_set import RepositorySet


class ForSet(Model):
    """This class is auto generated by OpenAPI Generator (https://openapi-generator.tech)."""

    def __init__(
        self, repositories: List[str] = None, developers: Optional[List[str]] = None,
    ):
        """ForSet - a model defined in OpenAPI

        :param repositories: The repositories of this ForSet.
        :param developers: The developers of this ForSet.
        """
        self.openapi_types = {"repositories": RepositorySet, "developers": DeveloperSet}

        self.attribute_map = {
            "repositories": "repositories",
            "developers": "developers",
        }

        self._repositories = repositories
        self._developers = developers

    @classmethod
    def from_dict(cls, dikt: dict) -> "ForSet":
        """Returns the dict as a model

        :param dikt: A dict.
        :return: The ForSet of this ForSet.
        """
        return serialization.deserialize_model(dikt, cls)

    @property
    def repositories(self):
        """Gets the repositories of this ForSet.

        :return: The repositories of this ForSet.
        :rtype: RepositorySet
        """
        return self._repositories

    @repositories.setter
    def repositories(self, repositories):
        """Sets the repositories of this ForSet.

        :param repositories: The repositories of this ForSet.
        :type repositories: RepositorySet
        """
        if repositories is None:
            raise ValueError("Invalid value for `repositories`, must not be `None`")

        self._repositories = repositories

    @property
    def developers(self):
        """Gets the developers of this ForSet.

        :return: The developers of this ForSet.
        :rtype: DeveloperSet
        """
        return self._developers

    @developers.setter
    def developers(self, developers):
        """Sets the developers of this ForSet.

        :param developers: The developers of this ForSet.
        :type developers: DeveloperSet
        """
        if developers is None:
            raise ValueError("Invalid value for `developers`, must not be `None`")

        self._developers = developers
