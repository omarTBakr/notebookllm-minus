from shared.utils import Settings, get_logger, get_settings


class BaseService:
    def __init__(self):
        self.settings: Settings = get_settings()
        # e.g. "application.services.ingest.FileService" — every service inherits a logger
        # named after its own module.
        self.logger = get_logger(type(self).__module__)
