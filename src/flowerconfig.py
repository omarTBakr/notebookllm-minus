"""Flower's own settings file, read from the working directory at startup.

Only what cannot come from the environment. Flower's Broker tab reads queue
depths from RabbitMQ's management API, and that URL carries the broker
password -- so it is built from the same Settings the workers use instead of
being written, secret and all, into docker-compose.yml. Without it the tab
reads "No queue data is available".
"""

from flower.utils import broker as _flower_broker

from shared.utils.config import get_settings

broker_api = get_settings().rabbitmq_management_api_url


# Flower 2.1.0 URL-encodes every vhost except the default one, so on vhost "/"
# it asks for /api/queues// -- a 404 -- and every count on the Broker tab reads
# N/A. /api/queues/%2F is the working form (checked against this broker). This
# file is executed by Flower at startup, which makes it the one place the fix
# can live without forking Flower; it touches nothing but Flower's own client.
_original_init = _flower_broker.RabbitMQ.__init__


def _init_with_encoded_default_vhost(self, *args, **kwargs):
    _original_init(self, *args, **kwargs)

    if self.vhost == "/":
        self.vhost = "%2F"


_flower_broker.RabbitMQ.__init__ = _init_with_encoded_default_vhost
