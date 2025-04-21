# Configure logger

import logging


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

console_logging_handler = logging.StreamHandler()
console_logging_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)
console_logging_handler.setLevel(logging.DEBUG)
logger.addHandler(console_logging_handler)
