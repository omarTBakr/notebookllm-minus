from utils import Settings, get_logger, get_settings


class BaseController:
    def __init__(self):
        self.settings: Settings = get_settings()
        # e.g. "controllers.FileController" — every controller inherits a logger
        # named after its own module.
        self.logger = get_logger(type(self).__module__)
