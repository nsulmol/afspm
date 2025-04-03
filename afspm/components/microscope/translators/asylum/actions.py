"""Holds asylum controller action handling."""

import logging
from afspm.components.microscope import actions
from afspm.components.microscope.translators.asylum.client import (
    XopClient, XopMessageError)


logger = logging.getLogger(__name__)


MOVE_PROBE_UUID = 'move-spec'


class AsylumActionHandler(actions.ActionHandler):
    """Implements asylum-specific aciton handling.

    Attributes:
        client: XopClient, used to communicate with the Asylum controller
            (via the IGOR software).
    """

    def __init__(self, actions_config_path: str, client: XopClient):
        """Init our Asylum handler, feeding the Xop Client."""
        if client is None:
            msg = "No xop client provided, cannot continue!"
            logger.critical(msg)
            raise AttributeError(msg)

        self.client = client
        super().__init__(actions_config_path)


def request_action(handler: AsylumActionHandler, method_name: str,
                   params: tuple[float | str] | None = None):
    """Request an action from Asylum.

    Args:
        handler: the action handler we use to request.
        method_name: the name of the Asylum method we are calling.
        params: additional parameters to pass (as a tuple).

    Raises:
        actions.ActionError if the request fails for any reason.
    """
    try:
        success, __ = handler.client.send_request(method_name, params)
        if success:
            return
        else:
            logger.info('Did not receive response from XopClient for '
                        f'{method_name}, with {params}.')
    except XopMessageError:
        pass

    msg = f'Asylum: Calling {method_name} with args {params} failed.'
    logger.error(msg)
    raise actions.ActionError(msg)
