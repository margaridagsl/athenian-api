from athenian.api.models.web.base_model_ import Enum, Model


class CodeCheckMetricID(Model, metaclass=Enum):
    """Currently supported code check metric types."""

    SUITES_COUNT = "chk-suites-count"
    SUCCESS_RATIO = "chk-success-ratio"
    SUITE_TIME = "chk-suite-time"
    SUITES_PER_PR = "chk-suites-per-pr"
    SUITE_TIME_PER_PR = "chk-suite-time-per-pr"
    PRS_WITH_CHECKS_COUNT = "chk-prs-with-checks-count"
    FLAKY_COMMIT_CHECKS_COUNT = "chk-flaky-commit-checks-count"
