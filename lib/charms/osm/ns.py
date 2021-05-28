# A prototype of a library to aid in the development and operation of
# OSM Network Service charms

# This class handles the heavy lifting associated with asyncio.
from charmhelpers.core.hookenv import (
    log,
)

try:
    import juju
except ImportError:
    import subprocess
    log('Installing juju')
    subprocess.run(["pip3", "install", "--upgrade", "pip"])
    subprocess.run(["pip3", "-qqq", "install", "juju"])

import asyncio
import logging
from juju.controller import Controller
import os
import os.path
import re
import time
import yaml

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
    cacert = None

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

        # Load the ca-cert from agent.conf
        AGENT_PATH = os.path.dirname(os.environ['JUJU_CHARM_DIR'])
        with open("{}/agent.conf".format(AGENT_PATH), "r") as f:
            try:
                y = yaml.safe_load(f)
                self.cacert = y['cacert']
            except yaml.YAMLError as exc:
                log("Unable to find Juju ca-cert.")
                raise exc

        # Create our event loop
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    async def connect(self):
        """Connect to the Juju controller."""
        controller = Controller()

        log(
            "Connecting to controller... ws://{}:{} as {}/{}".format(
                self.endpoint,
                self.port,
                self.user,
                self.secret[-4:].rjust(len(self.secret), "*"),
            )
        )
        await controller.connect(
            endpoint=self.endpoint,
            username=self.user,
            password=self.secret,
            cacert=self.cacert,
        )

        return controller

    def __del__(self):
        self.logout()

    async def disconnect(self):
        """Disconnect from the Juju controller."""
        if self.client:
            log("Disconnecting Juju controller")
            await self.client.disconnect()

    def login(self):
        """Login to the Juju controller."""
        if not self.client:
            # Connect to the Juju API server
            self.client = self.loop.run_until_complete(self.connect())
        return self.client

    def logout(self):
        """Logout of the Juju controller."""

        if self.loop:
            log("Disconnecting from API")
            self.loop.run_until_complete(self.disconnect())

    def FormatApplicationName(self, *args):
        """
        Generate a Juju-compatible Application name

        :param args tuple: Positional arguments to be used to construct the
        application name.

        Limitations::
        - Only accepts characters a-z and non-consequitive dashes (-)
        - Application name should not exceed 50 characters

        Examples::

            FormatApplicationName("ping_pong_ns", "ping_vnf", "a")
        """
        appname = ""
        for c in "-".join(list(args)):
            if c.isdigit():
                c = chr(97 + int(c))
            elif not c.isalpha():
                c = "-"
            appname += c

        return re.sub('-+', '-', appname.lower())

    def GetApplicationName(self, vnf_member_index, vdu_id=None):
        """Get the runtime application name of a VNF/VDU.

        This will generate an application name matching the name of the deployed charm,
        given the right parameters.

        :param vnf_member_index str: The vnf-member-index of the VNF
        :param vdu_id str: The vdu:id as specified in the descriptor
        """

        # Get the NSR name from the Juju Unit name
        # i.e., JUJU_UNIT_NAME=testc-ac/0
        # or JUJU_UNIT_NAME=asdf-asdf-ac/0 (a NS name with a hyphen)
        app = os.environ['JUJU_UNIT_NAME']
        nsr_name = app[:app.rindex('-')]

        vdu_id = (vdu_id if vdu_id else "") + "-"
        application_name = self.FormatApplicationName(nsr_name, vnf_member_index, vdu_id)

        # This matches the logic used by the LCM
        application_name = application_name[0:48]
        vca_index = int(vnf_member_index) - 1
        application_name += chr(97 + vca_index // 26) + chr(97 + vca_index % 26)

        return application_name

    def ExecutePrimitiveGetOutput(self, application, primitive, params={}, timeout=600):
        """Execute a single primitive and return it's output.

        This is a blocking method that will execute a single primitive and wait
        for its completion before return it's output.

        :param application str: The application name provided by `GetApplicationName`.
        :param primitive str: The name of the primitive to execute.
        :param params list: A list of parameters.
        :param timeout int: A timeout, in seconds, to wait for the primitive to finish. Defaults to 600 seconds.
        """
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

        This is a non-blocking method to execute a primitive. It will return
        the UUID of the queued primitive execution, which you can use
        for subsequent calls to `GetPrimitiveStatus` and `GetPrimitiveOutput`.

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
            self.loop.run_until_complete(
                model.disconnect()
            )
        else:
            # Invalid mapping: application not found. Raise exception
            raise Exception("Application not found: {}".format(application))

        return uuid

    def GetPrimitiveStatus(self, uuid):
        """Get the status of a Primitive execution.

        This will return one of the following strings:
        - pending
        - running
        - completed
        - failed

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

        self.loop.run_until_complete(
            model.disconnect()
        )

        return status[uuid]

    def GetPrimitiveOutput(self, uuid):
        """Get the output of a completed Primitive execution.


        :param uuid string: The UUID of the executed Primitive.
        :returns: The output of the execution, or None if it's still running.
        """
        result = None
        if not self.client:
            self.login()

        model = self.loop.run_until_complete(
            self.client.get_model(self.model)
        )

        result = self.loop.run_until_complete(
            model.get_action_output(uuid)
        )

        self.loop.run_until_complete(
            model.disconnect()
        )

        return result
