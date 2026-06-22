#!/usr/bin/env python3
import traceback

import udi_interface

from acinf_cloud import ACInfinityCloudClient

LOGGER = udi_interface.LOGGER


class ACInfinityFanNode(udi_interface.Node):
    id = "ACFAN"

    # ST = speed percentage (0-100), GV0 = power (0/1)
    drivers = [
        {"driver": "ST", "value": 0, "uom": 51},
        {"driver": "GV0", "value": 0, "uom": 2},
    ]

    def __init__(self, polyglot, primary, address, name, client):
        super().__init__(polyglot, primary, address, name)
        self.client = client
        self._last_nonzero_speed = 100

    def set_client(self, client):
        self.client = client

    def _apply_state(self, state):
        speed = max(0, min(100, int(state.get("speed", 0))))
        is_on = bool(state.get("is_on", speed > 0))
        if speed > 0:
            self._last_nonzero_speed = speed

        self.setDriver("ST", speed, report=True, force=True)
        self.setDriver("GV0", 1 if is_on else 0, report=True, force=True)

    def query(self, command=None):
        try:
            state = self.client.get_fan_state()
            self._apply_state(state)
        except Exception as exc:
            LOGGER.error("Fan query failed: %s", exc)
            LOGGER.debug(traceback.format_exc())
        return True

    def cmd_on(self, command):
        try:
            state = self.client.set_power(True)
            if int(state.get("speed", 0)) == 0:
                state = self.client.set_speed(self._last_nonzero_speed)
            self._apply_state(state)
        except Exception as exc:
            LOGGER.error("Failed to turn fan on: %s", exc)
            LOGGER.debug(traceback.format_exc())
        return True

    def cmd_off(self, command):
        try:
            current_speed = int(self.getDriver("ST"))
            if current_speed > 0:
                self._last_nonzero_speed = current_speed
            state = self.client.set_power(False)
            self._apply_state(state)
        except Exception as exc:
            LOGGER.error("Failed to turn fan off: %s", exc)
            LOGGER.debug(traceback.format_exc())
        return True

    def cmd_set_speed(self, command):
        try:
            speed = int(command.get("value", 0))
            state = self.client.set_speed(speed)
            self._apply_state(state)
        except Exception as exc:
            LOGGER.error("Failed to set fan speed: %s", exc)
            LOGGER.debug(traceback.format_exc())
        return True

    commands = {
        "DON": cmd_on,
        "DOF": cmd_off,
        "SETSPD": cmd_set_speed,
        "QUERY": query,
    }


class ACInfinityController(udi_interface.Node):
    id = "ACCTRL"

    drivers = [
        {"driver": "ST", "value": 1, "uom": 2},
    ]

    def __init__(self, polyglot, primary, address, name):
        super().__init__(polyglot, primary, address, name)
        self.poly = polyglot
        self.Parameters = udi_interface.Custom(polyglot, "customparams")
        self.client = None

        self.poly.subscribe(self.poly.START, self.start, address)
        self.poly.subscribe(self.poly.CUSTOMPARAMS, self.parameter_handler)
        self.poly.subscribe(self.poly.POLL, self.poll)
        self.poly.subscribe(self.poly.STOP, self.stop)

    def _to_bool(self, value, default=False):
        if value is None:
            return default
        text = str(value).strip().lower()
        return text in ("1", "true", "yes", "on")

    def _normalize_base_url(self, base_url):
        if not base_url:
            return "http://www.acinfinityserver.com"

        text = str(base_url).strip()
        if text.startswith("https://www.acinfinityserver.com"):
            LOGGER.warning(
                "AC Infinity cloud endpoint appears to require HTTP. Converting api_base_url to http://www.acinfinityserver.com"
            )
            return text.replace("https://", "http://", 1)
        return text

    def _log_cloud_warnings(self, params):
        mock_mode = self._to_bool(params.get("mock_mode", "true"), default=True)
        if mock_mode:
            return

        LOGGER.warning(
            "AC Infinity cloud transport may be HTTP. Credentials and tokens could be exposed in transit. Use a dedicated account/password."
        )

        api_token = str(params.get("api_token", "")).strip()
        email = str(params.get("email", "")).strip()
        password = str(params.get("password", ""))
        if not api_token and (not email or not password):
            LOGGER.warning("Set api_token OR both email and password in PG3 custom parameters.")

    def _build_client(self):
        p = self.Parameters
        self._log_cloud_warnings(p)
        controller_type = str(p.get("controller_type", "controller69")).strip() or "controller69"
        LOGGER.info("Controller profile: %s", controller_type)
        self.client = ACInfinityCloudClient(
            api_base_url=self._normalize_base_url(p.get("api_base_url", "http://www.acinfinityserver.com")),
            api_token=p.get("api_token", ""),
            device_id=p.get("device_id", ""),
            email=p.get("email", ""),
            password=p.get("password", ""),
            controller_type=controller_type,
            port=p.get("port", "1"),
            user_agent=p.get("user_agent", "okhttp/4.12.0"),
            mock_mode=self._to_bool(p.get("mock_mode", "true"), default=True),
        )

    def _ensure_fan_node(self):
        node = self.poly.getNode("acifan1")
        if node is None:
            node = ACInfinityFanNode(self.poly, self.address, "acifan1", "AC Infinity Fan", self.client)
            self.poly.addNode(node)
        else:
            node.set_client(self.client)
        return node

    def parameter_handler(self, params):
        LOGGER.info("Received custom params update")
        self._build_client()
        fan = self.poly.getNode("acifan1")
        if fan is not None:
            fan.set_client(self.client)

    def start(self):
        LOGGER.info("Starting AC Infinity nodeserver")
        self._build_client()
        fan = self._ensure_fan_node()
        fan.query()

    def stop(self):
        LOGGER.info("Stopping AC Infinity nodeserver")

    def poll(self, poll_type):
        if poll_type != "longPoll":
            return
        fan = self.poly.getNode("acifan1")
        if fan is not None:
            fan.query()


if __name__ == "__main__":
    polyglot = udi_interface.Interface([])
    polyglot.start()

    controller = ACInfinityController(polyglot, "controller", "controller", "AC Infinity Controller")
    polyglot.addNode(controller)

    polyglot.ready()
    polyglot.runForever()
