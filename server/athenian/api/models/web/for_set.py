from typing import List, Optional

from athenian.api.models.web.base_model_ import Model
from athenian.api.models.web.pull_request_with import PullRequestWith


class ForSet(Model):
    """This class is auto generated by OpenAPI Generator (https://openapi-generator.tech)."""

    openapi_types = {
        "repositories": List[str],
        "with_": PullRequestWith,
        "labels_include": List[str],
    }

    attribute_map = {
        "repositories": "repositories",
        "with_": "with",
        "labels_include": "labels_include",
    }

    __slots__ = ["_" + k for k in openapi_types]

    def __init__(
        self,
        repositories: Optional[List[str]] = None,
        with_: Optional[PullRequestWith] = None,
        labels_include: Optional[List[str]] = None,
    ):
        """ForSet - a model defined in OpenAPI

        :param repositories: The repositories of this ForSet.
        :param with_: The with of this ForSet.
        :param labels_include: The labels_include of this ForSet.
        """
        self._repositories = repositories
        self._with_ = with_
        self._labels_include = labels_include

    @property
    def repositories(self) -> List[str]:
        """Gets the repositories of this ForSet.

        :return: The repositories of this ForSet.
        """
        return self._repositories

    @repositories.setter
    def repositories(self, repositories: List[str]):
        """Sets the repositories of this ForSet.

        :param repositories: The repositories of this ForSet.
        """
        if repositories is None:
            raise ValueError("Invalid value for `repositories`, must not be `None`")

        self._repositories = repositories

    @property
    def with_(self) -> PullRequestWith:
        """Gets the with_ of this PullRequest.

        List of developers related to this PR.

        :return: The with_ of this PullRequest.
        """
        return self._with_

    @with_.setter
    def with_(self, with_: PullRequestWith):
        """Sets the with_ of this PullRequest.

        List of developers related to this PR.

        :param with_: The with_ of this PullRequest.
        """
        self._with_ = with_

    @property
    def labels_include(self) -> List[str]:
        """Gets the labels_include of this ForSet.

        :return: The labels_include of this ForSet.
        """
        return self._labels_include

    @labels_include.setter
    def labels_include(self, labels_include: List[str]):
        """Sets the labels_include of this ForSet.

        :param labels_include: The labels_include of this ForSet.
        """
        self._labels_include = labels_include
