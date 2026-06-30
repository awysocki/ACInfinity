#!/usr/bin/env python3
import time
import threading
import traceback

import udi_interface

from acinf_cloud import ACInfinityCloudClient

LOGGER = udi_interface.LOGGER
VERSION = "2026.6.045"
try:
    _version_parts = str(VERSION).split(".")
    VERSION_YEAR = int(_version_parts[0])
    VERSION_MONTH = int(_version_parts[1])
    VERSION_REVISION = int(_version_parts[2])
except Exception:
    VERSION_YEAR = 2026
    VERSION_MONTH = 1
    VERSION_REVISION = 0


class ACInfinityFanNode(udi_interface.Node):
    id = "fan"

    COMMAND_VERIFY_TIMEOUT_S = 30
    COMMAND_VERIFY_INTERVAL_S = 2.0

    # ST/GV0 are local intent state, GV1/GV2 are remote cloud-readback state.
    DEFAULT_DRIVERS = [
        {"driver": "ST", "value": 0, "uom": 25},
        {"driver": "GV0", "value": 0, "uom": 56},
        {"driver": "GV1", "value": 0, "uom": 25},
        {"driver": "GV2", "value": 0, "uom": 56},
    ]
    drivers = [dict(d) for d in DEFAULT_DRIVERS]

    def __init__(self, polyglot, primary, address, name, client, verify_timeout_s=None, verify_interval_s=None):
        super().__init__(polyglot, primary, address, name)
        self.drivers = [dict(d) for d in self.drivers]
        self._ensure_driver_definitions()
        self.client = client
        self._last_nonzero_speed = 10
        self._pending_speed_level = None
        self.COMMAND_VERIFY_TIMEOUT_S = int(verify_timeout_s) if verify_timeout_s is not None else int(self.COMMAND_VERIFY_TIMEOUT_S)
        self.COMMAND_VERIFY_INTERVAL_S = float(verify_interval_s) if verify_interval_s is not None else float(self.COMMAND_VERIFY_INTERVAL_S)

    def _ensure_driver_definitions(self):
        existing = {str(d.get("driver")) for d in self.drivers if isinstance(d, dict)}
        added = []
        for spec in self.DEFAULT_DRIVERS:
            name = str(spec.get("driver"))
            if name not in existing:
                self.drivers.append(dict(spec))
                added.append(name)
        if added:
            LOGGER.info("%s repaired missing drivers: %s", self.address, ", ".join(added))

    def set_client(self, client):
        self.client = client

    def set_verify_timing(self, timeout_s, interval_s):
        self.COMMAND_VERIFY_TIMEOUT_S = max(2, int(timeout_s))
        self.COMMAND_VERIFY_INTERVAL_S = max(0.1, float(interval_s))

    @staticmethod
    def _percent_to_level(speed_percent):
        speed_raw = max(0, min(100, int(speed_percent)))
        if speed_raw <= 0:
            return 0
        if speed_raw <= 10:
            return speed_raw
        return max(1, min(10, int(round(speed_raw / 10.0))))

    @staticmethod
    def _level_to_percent(speed_level):
        speed_level = max(0, min(10, int(speed_level)))
        return speed_level

    def _apply_state(self, state, remote_state=None):
        speed_level = self._percent_to_level(state.get("speed", 0))
        is_on = bool(state.get("is_on", speed_level > 0))
        cloud_state = remote_state if remote_state is not None else state
        remote_speed_level = self._percent_to_level(cloud_state.get("speed", 0))
        remote_is_on = bool(cloud_state.get("is_on", remote_speed_level > 0))
        current_local_speed = int(self.getDriver("GV0"))

        if is_on and speed_level > 0:
            # Only trust cloud speed while fan is ON.
            self._last_nonzero_speed = speed_level
            display_speed = speed_level
        else:
            # Keep our local desired speed while OFF.
            display_speed = max(0, current_local_speed)

        self.setDriver("ST", 1 if is_on else 0, report=True, force=True)
        self.setDriver("GV0", display_speed, report=True, force=True)
        self.setDriver("GV1", 1 if remote_is_on else 0, report=True, force=True)
        self.setDriver("GV2", remote_speed_level, report=True, force=True)

    def _apply_state_with_expected_speed(self, state, expected_speed_level, remote_state=None):
        """Apply cloud state while guarding against short ramp/readback lag.

        Immediately after writes, controller can report ON with a temporary lower speed
        while ramping. Keep the requested local speed in that case.
        """
        expected = max(0, min(10, int(expected_speed_level)))
        observed = self._percent_to_level(state.get("speed", 0))
        is_on = bool(state.get("is_on", observed > 0))
        if is_on and expected > 0 and observed < expected:
            guarded = dict(state)
            guarded["speed"] = expected
            self._apply_state(guarded, remote_state=remote_state)
            return
        self._apply_state(state, remote_state=remote_state)

    def _await_expected_state(self, expected_is_on=None, expected_speed_level=None):
        """Temporarily fast-poll cloud state until command outcome is observed.

        Polls every COMMAND_VERIFY_INTERVAL_S seconds up to COMMAND_VERIFY_TIMEOUT_S
        and stops immediately
        when expected state is reached.
        """
        deadline = time.monotonic() + float(self.COMMAND_VERIFY_TIMEOUT_S)
        interval = max(0.1, float(self.COMMAND_VERIFY_INTERVAL_S))
        expected_speed = None if expected_speed_level is None else max(0, min(10, int(expected_speed_level)))
        # While waiting for OFF confirmation, keep locally staged speed visible instead
        # of transient cloud target rewrites (for example, 10) until spin-down completes.
        hold_off_speed = None
        optimistic_off = False
        optimistic_on = False
        if expected_is_on is False and expected_speed is None:
            hold_off_speed = max(0, min(10, int(self.getDriver("GV0"))))
            optimistic_off = True
        if expected_is_on is True:
            optimistic_on = True
        last_state = None

        while True:
            state = self.client.get_fan_state()
            last_state = state

            state_for_apply = state
            if hold_off_speed is not None and bool(state.get("is_on", False)):
                state_for_apply = dict(state)
                state_for_apply["speed"] = hold_off_speed
                if optimistic_off:
                    # Treat DOF as immediately OFF in node state while cloud catches up.
                    state_for_apply["is_on"] = False
            elif optimistic_on and not bool(state.get("is_on", False)):
                state_for_apply = dict(state)
                state_for_apply["is_on"] = True
                if expected_speed is not None and expected_speed > 0:
                    state_for_apply["speed"] = expected_speed

            if expected_speed is not None:
                self._apply_state_with_expected_speed(state_for_apply, expected_speed, remote_state=state)
            else:
                self._apply_state(state_for_apply, remote_state=state)

            observed_is_on = bool(state.get("is_on", False))
            observed_speed = self._percent_to_level(state.get("speed", 0))

            matched = True
            if expected_is_on is not None and observed_is_on != bool(expected_is_on):
                matched = False
            if expected_speed is not None and observed_speed != expected_speed:
                matched = False

            if matched:
                LOGGER.debug(
                    "Command verification complete: expected_is_on=%s expected_speed=%s observed_state=%s",
                    expected_is_on,
                    expected_speed,
                    state,
                )
                return True

            if time.monotonic() >= deadline:
                LOGGER.error(
                    "Command verification timeout after %ss. expected_is_on=%s expected_speed=%s last_state=%s",
                    self.COMMAND_VERIFY_TIMEOUT_S,
                    expected_is_on,
                    expected_speed,
                    last_state,
                )
                return False

            time.sleep(interval)

    def query(self, command=None):
        try:
            state = self.client.get_fan_state()
            self._apply_state(state, remote_state=state)
        except Exception as exc:
            LOGGER.error("Fan query failed: %s", exc)
            LOGGER.debug(traceback.format_exc())
        return True

    def cmd_on(self, command):
        try:
            local_level = int(self.getDriver("GV0"))
            if self._pending_speed_level is not None:
                target_level = self._pending_speed_level
            elif local_level > 0:
                # Honor locally staged speed (survives OFF state and restarts).
                target_level = local_level
            else:
                target_level = self._last_nonzero_speed
            target_level = max(1, min(10, int(target_level)))
            # Immediately reflect ON intent locally; remote fields continue to show cloud readback.
            self.setDriver("ST", 1, report=True, force=True)
            self.setDriver("GV0", target_level, report=True, force=True)
            self.client.set_power(True, speed_preference=self._level_to_percent(target_level))
            self._pending_speed_level = None
            self._await_expected_state(expected_is_on=True, expected_speed_level=target_level)
        except Exception as exc:
            LOGGER.error("Failed to turn fan on: %s", exc)
            LOGGER.debug(traceback.format_exc())
        return True

    def cmd_off(self, command):
        try:
            current_speed = int(self.getDriver("GV0"))
            if current_speed > 0:
                self._last_nonzero_speed = current_speed
            # Immediately reflect OFF intent locally, then verify cloud convergence.
            self.setDriver("ST", 0, report=True, force=True)
            self.client.set_power(False)
            self._await_expected_state(expected_is_on=False)
        except Exception as exc:
            LOGGER.error("Failed to turn fan off: %s", exc)
            LOGGER.debug(traceback.format_exc())
        return True

    def cmd_set_speed(self, command):
        try:
            LOGGER.debug("Raw SETSPD command payload: %s", command)
            speed_level = 0
            if isinstance(command, dict):
                raw_value = command.get("value")
                if raw_value in (None, ""):
                    query = command.get("query")
                    if isinstance(query, dict):
                        for key in ("value", "SPD", "SPD.uom56", "spd", "speed"):
                            if key in query and str(query.get(key)).strip() != "":
                                raw_value = query.get(key)
                                break
                if raw_value not in (None, ""):
                    speed_level = int(float(raw_value))
            speed_level = max(0, min(10, speed_level))
            if speed_level > 0:
                self._last_nonzero_speed = speed_level
            # Keep speed as its own desired value regardless of current power state.
            self._pending_speed_level = speed_level if speed_level > 0 else None
            self.setDriver("GV0", speed_level, report=True, force=True)

            is_on = int(self.getDriver("ST")) == 1
            if is_on:
                # If currently ON, apply speed to cloud and verify expected status.
                self.client.set_speed(self._level_to_percent(speed_level))
                if speed_level == 0:
                    self._await_expected_state(expected_is_on=False)
                else:
                    self._await_expected_state(expected_is_on=True, expected_speed_level=speed_level)
                LOGGER.debug("Speed update applied while ON and verification loop completed: level=%s", speed_level)
            else:
                # While OFF, keep speed local only; apply on next DON.
                LOGGER.debug("Speed update stored locally only while OFF: level=%s", speed_level)
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

    DEFAULT_DRIVERS = [
        {"driver": "ST", "value": 1, "uom": 25},
        {"driver": "GV1", "value": VERSION_YEAR, "uom": 25},
        {"driver": "GV2", "value": VERSION_MONTH, "uom": 25},
        {"driver": "GV3", "value": VERSION_REVISION, "uom": 25},
        {"driver": "GV4", "value": 0, "uom": 25},
        {"driver": "GV5", "value": 0, "uom": 4},
        {"driver": "GV6", "value": 0, "uom": 51},
        {"driver": "GV7", "value": 0, "uom": 25},
    ]
    drivers = [dict(d) for d in DEFAULT_DRIVERS]

    @staticmethod
    def _normalize_temp_unit(value):
        # Only explicit F/f enables Fahrenheit conversion; anything else defaults to C.
        return "F" if str(value).strip().lower() == "f" else "C"

    def __init__(self, polyglot, primary, address, name):
        super().__init__(polyglot, primary, address, name)
        self.drivers = [dict(d) for d in self.drivers]
        self._ensure_driver_definitions()
        self.poly = polyglot
        self.Parameters = udi_interface.Custom(polyglot, "customparams")
        self.client = None
        self._client_lock = threading.RLock()
        self._last_login_gate_reason = None
        self._login_ready_logged = False
        self._last_logged_temperature_c = None
        self._last_logged_humidity = None
        self._last_logged_vpd_kpa = None
        self._temp_display_unit = "C"
        self._verify_timeout_s = ACInfinityFanNode.COMMAND_VERIFY_TIMEOUT_S
        self._verify_interval_s = ACInfinityFanNode.COMMAND_VERIFY_INTERVAL_S

        self.poly.subscribe(self.poly.START, self.start, address)
        self.poly.subscribe(self.poly.CUSTOMPARAMS, self.parameter_handler)
        self.poly.subscribe(self.poly.POLL, self.poll)
        self.poly.subscribe(self.poly.STOP, self.stop)

    def _ensure_driver_definitions(self):
        existing = {str(d.get("driver")) for d in self.drivers if isinstance(d, dict)}
        added = []
        for spec in self.DEFAULT_DRIVERS:
            name = str(spec.get("driver"))
            if name not in existing:
                self.drivers.append(dict(spec))
                added.append(name)
        if added:
            LOGGER.info("%s repaired missing drivers: %s", self.address, ", ".join(added))

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

    @staticmethod
    def _parse_int_param(params, key, default, minimum, maximum):
        try:
            value = int(float(params.get(key, default)))
        except (TypeError, ValueError):
            value = int(default)
        return max(minimum, min(maximum, value))

    @staticmethod
    def _parse_float_param(params, key, default, minimum, maximum):
        try:
            value = float(params.get(key, default))
        except (TypeError, ValueError):
            value = float(default)
        return max(minimum, min(maximum, value))

    def _update_verify_timing_from_params(self, params):
        # Hidden tuning knobs (optional): verify_interval_s, verify_timeout_s
        interval_s = self._parse_float_param(params, "verify_interval_s", 2.0, 0.1, 60.0)
        timeout_s = self._parse_int_param(params, "verify_timeout_s", 30, 2, 600)
        if timeout_s < interval_s:
            timeout_s = int(interval_s)
        self._verify_interval_s = interval_s
        self._verify_timeout_s = timeout_s
        LOGGER.debug(
            "Command verify timing set: interval=%ss timeout=%ss",
            self._verify_interval_s,
            self._verify_timeout_s,
        )

    def _build_client(self, params=None):
        p = params if params is not None else self.Parameters
        self._log_cloud_warnings(p)
        self._update_verify_timing_from_params(p)
        self._temp_display_unit = self._normalize_temp_unit(p.get("temp_unit", "C"))
        controller_type = str(p.get("controller_type", "controller69")).strip() or "controller69"
        LOGGER.info("Controller profile: %s", controller_type)
        LOGGER.info("Temperature display unit: %s", self._temp_display_unit)
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
        try:
            port_label = int(self.client.port)
        except Exception:
            port_label = 1
        desired_name = f"AC Infinity Fan {port_label}"

        node = self.poly.getNode("acifan1")
        if node is None:
            node = ACInfinityFanNode(
                self.poly,
                self.address,
                "acifan1",
                desired_name,
                self.client,
                verify_timeout_s=self._verify_timeout_s,
                verify_interval_s=self._verify_interval_s,
            )
            self.poly.addNode(node)
        else:
            # Keep runtime node object label aligned with configured port.
            if getattr(node, "name", None) != desired_name:
                node.name = desired_name
                # Ask Polyglot to apply the name update for existing node.
                try:
                    node.rename = True
                    self.poly.addNode(node)
                except Exception:
                    LOGGER.debug("Fan node rename update could not be applied immediately")
            node.set_client(self.client)
            node.set_verify_timing(self._verify_timeout_s, self._verify_interval_s)
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

        try:
            preferred_port = int(self.client.get_preferred_active_port())
            if preferred_port > 0 and int(self.client.port) != preferred_port:
                LOGGER.info(
                    "Aligning runtime fan port to cloud active port: %s -> %s",
                    self.client.port,
                    preferred_port,
                )
                self.client.port = preferred_port
        except Exception as exc:
            LOGGER.warning("Failed to align fan port from cloud telemetry: %s", exc)
            LOGGER.debug(traceback.format_exc())

        fan = self._ensure_fan_node()
        fan.query()
        self.setDriver("GV1", VERSION_YEAR, report=True, force=True)
        self.setDriver("GV2", VERSION_MONTH, report=True, force=True)
        self.setDriver("GV3", VERSION_REVISION, report=True, force=True)
        try:
            # Show connected/plugged port count at controller level.
            port_count = int(self.client.get_connected_port_count())
            LOGGER.debug("Controller port count update: %s", port_count)
            self.setDriver("GV4", port_count, report=True, force=True)
        except Exception as exc:
            LOGGER.warning("Failed to update controller port count: %s", exc)
            LOGGER.debug(traceback.format_exc())
            self.setDriver("GV4", 0, report=True, force=True)

        try:
            env = self.client.get_controller_environment()
            temp_c = env.get("temperature_c")
            humidity = env.get("humidity")
            vpd_kpa = env.get("vpd_kpa")

            if temp_c is not None:
                temp_value = float(temp_c)
                temp_uom = 4
                if self._temp_display_unit == "F":
                    temp_value = (temp_value * 9.0 / 5.0) + 32.0
                    temp_uom = 17
                temp_rounded = round(temp_value, 1)
                self.setDriver("GV5", temp_rounded, uom=temp_uom, report=True, force=True)
            else:
                temp_rounded = None

            if humidity is not None:
                humidity_rounded = round(max(0.0, min(100.0, float(humidity))), 1)
                self.setDriver("GV6", humidity_rounded, report=True, force=True)
            else:
                humidity_rounded = None

            if vpd_kpa is not None:
                vpd_rounded = round(max(0.0, min(10.0, float(vpd_kpa))), 2)
                self.setDriver("GV7", vpd_rounded, report=True, force=True)
            else:
                vpd_rounded = None

            if (
                temp_rounded != self._last_logged_temperature_c
                or humidity_rounded != self._last_logged_humidity
                or vpd_rounded != self._last_logged_vpd_kpa
            ):
                LOGGER.info(
                    "Controller sensors: temperature_%s=%s humidity=%s vpd_kpa=%s",
                    self._temp_display_unit.lower(),
                    temp_rounded,
                    humidity_rounded,
                    vpd_rounded,
                )
                self._last_logged_temperature_c = temp_rounded
                self._last_logged_humidity = humidity_rounded
                self._last_logged_vpd_kpa = vpd_rounded
        except Exception as exc:
            LOGGER.warning("Failed to update controller sensors: %s", exc)
            LOGGER.debug(traceback.format_exc())
        return True

    def _sync_on_poll(self):
        # Always re-read cloud state so physical button changes are reflected in IoX.
        self._sync_nodes_after_login()

    def parameter_handler(self, params):
        LOGGER.info("Received custom params update")
        with self._client_lock:
            self.Parameters.load(params)
            self._seed_required_custom_params()
            self._last_login_gate_reason = None
            self._build_client(params)
            self._sync_nodes_after_login()

    def start(self):
        LOGGER.info("Starting AC Infinity nodeserver")
        self.poly.setCustomParamsDoc()
        with self._client_lock:
            self._seed_required_custom_params()
            self._build_client()
            self._sync_nodes_after_login()

    def stop(self):
        LOGGER.info("Stopping AC Infinity nodeserver")

    def poll(self, poll_type):
        # Re-sync cloud/controller state on short poll.
        # Keep bootstrap behavior while waiting for initial fan node creation.
        if poll_type == "shortPoll" or self.poly.getNode("acifan1") is None:
            with self._client_lock:
                self._sync_on_poll()

    def query(self, command=None):
        with self._client_lock:
            self._sync_nodes_after_login()
        self.setDriver("ST", 1, report=True, force=True)
        # Version, port count, and controller sensor drivers are maintained by _sync_nodes_after_login().
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
