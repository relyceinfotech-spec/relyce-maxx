class ConfigurationError(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class EvidenceError(Exception):
    pass


class EvidenceRejection(EvidenceError):
    def __init__(self, failures):
        super().__init__("Claim/source/passage validation failed")
        self.failures = failures


class FetchRejected(Exception):
    pass


class LeaseLost(Exception):
    pass


class ModelOutputTruncated(Exception):
    pass


class ModelEmptyResponse(Exception):
    pass


class ModelIncompleteResponse(Exception):
    pass


class ProviderCreditUnavailable(Exception):
    pass
