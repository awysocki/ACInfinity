#!/usr/bin/env python3
import traceback

import udi_interface

from acinf_cloud import ACInfinityCloudClient

LOGGER = udi_interface.LOGGER
VERSION = "0.2.1"


class ACInfinityFanNode(udi_interface.Node):
    id = "fan"

    # ST = fan speed level (0-10), GV0 = power (0/1)
    drivers = [
        {"driver": "ST", "value": 0, "uom": 56},
        {"driver": "GV0", "value": 0, "uom": 2},
    ]

    def __init__(self, polyglot, primary, address, name, client):
        super().__init__(polyglot, primary, address, name)
        self.client = client
        self._last_nonzero_speed = 10

    def set_client(self, client):
        self.client = client

    @staticmethod
    def _percent_to_level(speed_percent):
        speed_percent = max(0, min(100, int(speed_percent)))
        if speed_percent <= 0:
            return 0
        return max(1, min(10, int(round(speed_percent / 10.0))))

    @staticmethod
    def _level_to_percent(speed_level):
        speed_level = max(0, min(10, int(speed_level)))
        if speed_level <= 0:
            return 0
        return max(10, min(100, speed_level * 10))

    def _apply_state(self, state):
        speed_percent = max(0, min(100, int(state.get("speed", 0))))
        speed_level = self._percent_to_level(speed_percent)
        is_on = bool(state.get("is_on", speed_percent > 0))
        if speed_level > 0:
            self._last_nonzero_speed = speed_level

        self.setDriver("ST", speed_level, report=True, force=True)
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
                state = self.client.set_speed(self._level_to_percent(self._last_nonzero_speed))
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
            speed_level = int(command.get("value", 0))
            if speed_level > 0:
                self._last_nonzero_speed = speed_level
            state = self.client.set_speed(self._level_to_percent(speed_level))
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
    id = "controller"

    drivers = [
        {"driver": "ST", "value": 1, "uom": 2},
    ]

    def __init__(self, polyglot, primary, address, name):
        super().__init__(polyglot, primary, address, name)
        self.poly = polyglot
        self.Parameters = udi_interface.Custom(polyglot, "customparams")
        self.client = None
        self._last_login_gate_reason = None
        self._login_ready_logged = False

        self.poly.subscribe(self.poly.START, self.start, address)
        self.poly.subscribe(self.poly.CUSTOMPARAMS, self.parameter_handler)
        self.poly.subscribe(self.poly.POLL, self.poll)
        self.poly.subscribe(self.poly.STOP, self.stop)

    def _seed_required_custom_params(self):
        required = {
            "user": "",
            "password": "",
        }
        missing = {}
        for key, default in required.items():
            if self.Parameters.get(key, None) is None:
                missing[key] = default

        if not missing:
            return

        LOGGER.info("Seeding missing custom params: %s", ", ".join(sorted(missing.keys())))

        # Prefer direct Custom object persistence when available.
        for key, default in missing.items():
            self.Parameters[key] = default

        # Fallback for interface versions that require explicit add/update.
        if hasattr(self.poly, "addCustomParam"):
            try:
                self.poly.addCustomParam(missing)
            except Exception as exc:
                LOGGER.warning("Failed seeding custom params via addCustomParam: %s", exc)

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
        user = str(params.get("user", "")).strip()
        email = str(params.get("email", user)).strip()
        password = str(params.get("password", ""))
        if not api_token and (not email or not password):
            LOGGER.warning("Set api_token OR both user and password in PG3 custom parameters.")

    def _effective_mock_mode(self, params):
        # Default to live mode when credentials/token are provided, unless user explicitly sets mock_mode.
        explicit = params.get("mock_mode")
        if explicit is not None and str(explicit).strip() != "":
            return self._to_bool(explicit, default=True)

        api_token = str(params.get("api_token", "")).strip()
        user = str(params.get("user", "")).strip()
        email = str(params.get("email", user)).strip()
        password = str(params.get("password", "")).strip()
        has_creds = bool(api_token or (email and password))
        return not has_creds

    def _build_client(self, params=None):
        p = params if params is not None else self.Parameters
        self._log_cloud_warnings(p)
        controller_type = str(p.get("controller_type", "controller69")).strip() or "controller69"
        LOGGER.info("Controller profile: %s", controller_type)
        user = p.get("user", "")
        email = p.get("email", user)
        self.client = ACInfinityCloudClient(
            api_base_url=self._normalize_base_url(p.get("api_base_url", "http://www.acinfinityserver.com")),
            api_token=p.get("api_token", ""),
            device_id=p.get("device_id", ""),
            email=email,
            password=p.get("password", ""),
            controller_type=controller_type,
            port=p.get("port", "1"),
            user_agent=p.get("user_agent", "okhttp/4.12.0"),
            mock_mode=self._effective_mock_mode(p),
        )

    def _ensure_fan_node(self):
        node = self.poly.getNode("acifan1")
        if node is None:
            node = ACInfinityFanNode(self.poly, self.address, "acifan1", "AC Infinity Fan", self.client)
            self.poly.addNode(node)
        else:
            node.set_client(self.client)
        return node

    def _login_ready(self):
        if self.client is None:
            return False

        try:
            # Require a successful cloud login and device discovery before creating runtime nodes.
            self.client._ensure_cloud_ready()
            if not str(self.client.device_id).strip():
                raise ValueError("Cloud login succeeded but no device_id was discovered")
            self._last_login_gate_reason = None
            if not self._login_ready_logged:
                LOGGER.info("Cloud login/device discovery succeeded. device_id=%s", self.client.device_id)
                self._login_ready_logged = True
            return True
        except ValueError as exc:
            reason = str(exc)
            if reason != self._last_login_gate_reason:
                LOGGER.warning("Login/device discovery not ready yet; fan nodes will not be created: %s", reason)
                self._last_login_gate_reason = reason
            self._login_ready_logged = False
        except Exception as exc:
            LOGGER.error("Cloud readiness check failed unexpectedly: %s", exc)
            LOGGER.debug(traceback.format_exc())
            self._login_ready_logged = False
            return False

        return False

    def _sync_nodes_after_login(self):
        if not self._login_ready():
            return False

        fan = self._ensure_fan_node()
        fan.query()
        return True

    def _sync_on_poll(self):
        if self.poly.getNode("acifan1") is not None:
            self._sync_nodes_after_login()
            return

        self._sync_nodes_after_login()

    def parameter_handler(self, params):
        LOGGER.info("Received custom params update")
        self.Parameters.load(params)
        self._seed_required_custom_params()
        self._last_login_gate_reason = None
        self._build_client(params)
        self._sync_nodes_after_login()

    def start(self):
        LOGGER.info("Starting AC Infinity nodeserver")
        self.poly.setCustomParamsDoc()
        self._seed_required_custom_params()
        self._build_client()
        self._sync_nodes_after_login()

    def stop(self):
        LOGGER.info("Stopping AC Infinity nodeserver")

    def poll(self, poll_type):
        if poll_type == "longPoll" or self.poly.getNode("acifan1") is None:
            # Retry node creation while credentials/login/device discovery are being corrected.
            self._sync_on_poll()

    def query(self, command=None):
        self._sync_nodes_after_login()
        self.setDriver("ST", 1, report=True, force=True)
        return True

    commands = {
        "QUERY": query,
    }


if __name__ == "__main__":
    polyglot = udi_interface.Interface([])
    polyglot.start(VERSION)

    controller = ACInfinityController(polyglot, "controller", "controller", "AC Infinity Controller")
    polyglot.addNode(controller)

    polyglot.ready()
    polyglot.runForever()
