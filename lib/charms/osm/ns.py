# A prototype of a library to aid in the development and operation of
# OSM Network Service charms

# This class handles the heavy lifting associated with asyncio.
from charmhelpers.contrib.python.packages import pip_install
from charmhelpers.core.hookenv import (
    log,
)

try:
    import juju
except ImportError:
    pip_install(['juju'])

import asyncio
import logging
from juju.controller import Controller
import os
import time

import ssl
# Allow unverified SSL connection to the Juju controller
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except AttributeError:
    # Legacy Python doesn't verify by default (see pep-0476)
    #   https://www.python.org/dev/peps/pep-0476/
    pass


# Quiet the debug logging
logging.getLogger('websockets.protocol').setLevel(logging.INFO)
logging.getLogger('juju.client.connection').setLevel(logging.WARN)
logging.getLogger('juju.model').setLevel(logging.WARN)
logging.getLogger('juju.machine').setLevel(logging.WARN)


class NetworkService:
    """A lightweight interface to the Juju controller.

    This NetworkService client is specifically designed to allow a higher-level
    "NS" charm to interoperate with "VNF" charms, allowing for the execution of
    Primitives across other charms within the same model.
    """
    endpoint = None
    user = 'admin'
    secret = None
    port = 17070
    loop = None
    client = None
    model = None

    def __init__(self, user, secret, endpoint=None):

        self.user = user
        self.secret = secret
        if endpoint is None:
            addresses = os.environ['JUJU_API_ADDRESSES']
            for address in addresses.split(' '):
                self.endpoint = address
        else:
            self.endpoint = endpoint

        # Stash the name of the model
        self.model = os.environ['JUJU_MODEL_NAME']

        # Create our event loop
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    async def connect(self):
        """asdf"""
        cacert = None
        controller = Controller()

        log(
            "Connecting to controller... ws://{}:{} as {}/{}".format(
                self.endpoint,
                self.port,
                self.user,
                self.secret,
            )
        )
        await controller.connect(
            endpoint=self.endpoint,
            username=self.user,
            password=self.secret,
            cacert=cacert,
        )

        return controller

    async def disconnect(self, controller):
        pass

    def login(self):
        if not self.client:
            # Connect to the Juju API server
            self.client = self.loop.run_until_complete(self.connect())
        return self.client

    def logout(self):
        if self.loop:
            log("Disconnecting from API")
            self.loop.run_until_complete(client.disconnect())

    def ExecutePrimitiveGetOutput(self, application, primitive, params={}, timeout=600):

        uuid = self.ExecutePrimitive(application, primitive, params)

        status = None
        output = None

        starttime = time.time()
        while(time.time() < starttime + timeout):
            status = self.GetPrimitiveStatus(uuid)
            if status in ['completed', 'failed']:
                break
            time.sleep(10)

        # When the primitive is done, get the output
        if status in ['completed', 'failed']:
            output = self.GetPrimitiveOutput(uuid)

        return output

    def ExecutePrimitive(self, application, primitive, params={}):
        """Execute a primitive.

        :param application string: The name of the application
        :param primitive string: The name of the Primitive.
        :param params list: A list of parameters.

        :returns uuid string: The UUID of the executed Primitive
        """
        uuid = None

        if not self.client:
            self.login()

        model = self.loop.run_until_complete(
            self.client.get_model(self.model)
        )

        # Get the application
        if application in model.applications:
            app = model.applications[application]

            # Execute the primitive
            unit = app.units[0]
            if unit:
                action = self.loop.run_until_complete(
                    unit.run_action(primitive, **params)
                )
                uuid = action.id
                log("Executing action: {}".format(uuid))

        else:
            # Invalid mapping: application not found. Raise exception
            raise Exception("Application not found: {}".format(application))

        return uuid

    def GetPrimitiveStatus(self, uuid):
        """Get the status of a Primitive execution.

        :param uuid string: The UUID of the executed Primitive.
        :returns: The status of the executed Primitive
        """
        status = None

        if not self.client:
            self.login()

        model = self.loop.run_until_complete(
            self.client.get_model(self.model)
        )

        status = self.loop.run_until_complete(
            model.get_action_status(uuid)
        )

        return status[uuid]

    def GetPrimitiveOutput(self, uuid):
        """Get the output of a completed Primitive execution.


        :param uuid string: The UUID of the executed Primitive.
        :returns: The output of the execution, or None if it's still running.
        """
        resulit = None
        if not self.client:
            self.login()

        model = self.loop.run_until_complete(
            self.client.get_model(self.model)
        )

        result = self.loop.run_until_complete(
            model.get_action_output(uuid)
        )

        return result

    def GetApplications(self):
        """Get a list of applications in the model.

        :returns: A list of application names.
        """
        return []

    def GetApplicationStatus(self, application):
        """Get the status of an application.

        :param application string: The name of the application
        :returns: The status of the application.
        """
        return None
