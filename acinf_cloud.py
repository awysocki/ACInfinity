import requests

import udi_interface


LOGGER = udi_interface.LOGGER


class ACInfinityCloudClient:
    """Clean-room AC Infinity cloud client based on observed endpoint behavior."""

    API_URL_LOGIN = "/api/user/appUserLogin"
    API_URL_GET_DEVICE_INFO_LIST_ALL = "/api/user/devInfoListAll"
    API_URL_GET_DEV_MODE_SETTING = "/api/dev/getdevModeSettingList"
    API_URL_ADD_DEV_MODE = "/api/dev/addDevMode"
    API_URL_MODE_AND_SETTINGS = "/api/dev/modeAndSetting"

    def __init__(
        self,
        api_base_url,
        api_token,
        device_id,
        email,
        password,
        controller_type="controller69",
        port=1,
        user_agent="okhttp/4.12.0",
        mock_mode=True,
        timeout=15,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.api_token = api_token.strip() if api_token else ""
        self.device_id = device_id.strip() if device_id else ""
        self.email = email.strip() if email else ""
        self.password = password if password else ""
        self.controller_type = self._normalize_controller_type(controller_type)
        self.port = int(port) if str(port).strip() else 1
        self.user_agent = user_agent.strip() if user_agent else "okhttp/4.12.0"
        self.mock_mode = mock_mode
        self.timeout = timeout

        self._mock_state = {
            "is_on": False,
            "speed": 0,
            "temperature_c": None,
            "humidity": None,
        }

    @staticmethod
    def _redact_payload(payload):
        if isinstance(payload, dict):
            redacted = {}
            for key, value in payload.items():
                if key in ("password", "appPasswordl", "appPassword", "token") and value not in (None, ""):
                    redacted[key] = "<redacted>"
                else:
                    redacted[key] = ACInfinityCloudClient._redact_payload(value)
            return redacted

        if isinstance(payload, list):
            return [ACInfinityCloudClient._redact_payload(item) for item in payload]

        return payload

    @staticmethod
    def _log_json(label, data):
        LOGGER.debug("%s: %s", label, data)

    @staticmethod
    def _normalize_controller_type(controller_type):
        text = str(controller_type or "controller69").strip().lower()
        aliases = {
            "69": "controller69",
            "standard": "controller69",
            "controller69": "controller69",
            "controller_69": "controller69",
            "69pro": "controller69pro",
            "controller69pro": "controller69pro",
            "controller_69_pro": "controller69pro",
            "controller69_pro": "controller69pro",
            "pro": "controller69pro",
            "ai_plus": "ai_plus",
            "ai-plus": "ai_plus",
            "aiplus": "ai_plus",
            "auto": "auto",
        }
        return aliases.get(text, "controller69")

    def _headers(self, include_token=True, use_min_version=False):
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": self.user_agent,
        }
        if include_token and self.api_token:
            headers["token"] = self.api_token
        if use_min_version:
            headers["minversion"] = "3.5"
        return headers

    def _post(self, path, payload, include_token=True):
        self._log_json(f"AC Infinity POST {path} request", self._redact_payload(payload))
        response = requests.post(
            f"{self.api_base_url}{path}",
            headers=self._headers(include_token=include_token),
            data=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        self._log_json(f"AC Infinity POST {path} response", body)
        if body.get("code") != 200:
            msg = body.get("msg", "unknown API error")
            raise ValueError(f"AC Infinity API error for {path}: {msg}")
        return body

    def _put(self, path, query_params, include_token=True, use_min_version=False):
        self._log_json(f"AC Infinity PUT {path} request", self._redact_payload(query_params))
        response = requests.put(
            f"{self.api_base_url}{path}",
            headers=self._headers(include_token=include_token, use_min_version=use_min_version),
            params=query_params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json() if response.content else {}
        if body:
            self._log_json(f"AC Infinity PUT {path} response", body)
        if body and body.get("code", 200) != 200:
            msg = body.get("msg", "unknown API error")
            raise ValueError(f"AC Infinity API error for {path}: {msg}")
        return body

    def _ensure_cloud_ready(self):
        if not self.api_token:
            if not self.email or not self.password:
                raise ValueError("Set api_token OR set both user and password")
            self.login()

        if not self.device_id:
            self.device_id = self._discover_first_device_id()

        if self.port < 0:
            raise ValueError("port must be >= 0")

        if self.controller_type not in ("controller69", "controller69pro", "ai_plus", "auto"):
            raise ValueError(
                "Unsupported controller_type. Use controller69, controller69pro, ai_plus, or auto"
            )

    def login(self):
        normalized_password = self.password[:25]
        body = self._post(
            self.API_URL_LOGIN,
            {
                "appEmail": self.email,
                "appPasswordl": normalized_password,
            },
            include_token=False,
        )
        self.api_token = str(body.get("data", {}).get("appId", "")).strip()
        if not self.api_token:
            raise ValueError("Login succeeded but no appId was returned")
        self._log_json("AC Infinity login parsed data", body.get("data", {}))
        return self.api_token

    def _discover_first_device_id(self):
        body = self._post(
            self.API_URL_GET_DEVICE_INFO_LIST_ALL,
            {"userId": self.api_token},
            include_token=True,
        )
        devices = body.get("data", [])
        self._log_json("AC Infinity device list parsed data", devices)
        if not devices:
            raise ValueError("No devices returned by AC Infinity cloud account")
        dev_id = devices[0].get("devId")
        if dev_id in (None, ""):
            raise ValueError("Device list returned but no devId found")
        return str(dev_id)

    def get_connected_port_count(self):
        if self.mock_mode:
            return 0

        self._ensure_cloud_ready()
        body = self._post(
            self.API_URL_GET_DEVICE_INFO_LIST_ALL,
            {"userId": self.api_token},
            include_token=True,
        )
        devices = body.get("data", [])
        if not isinstance(devices, list) or not devices:
            return 0

        target = None
        for device in devices:
            if str(device.get("devId", "")).strip() == str(self.device_id):
                target = device
                break
        if target is None:
            target = devices[0]

        device_info = target.get("deviceInfo") or {}
        ports = device_info.get("ports") or target.get("ports") or []
        if not isinstance(ports, list):
            LOGGER.debug("AC Infinity ports payload is not a list: %s", ports)
            return 0

        connected = 0
        for port in ports:
            if not isinstance(port, dict):
                continue
            resistance = self._to_int(port.get("portResistance"), default=65535)
            online = self._to_int(port.get("online"), default=0)
            load_state = self._to_int(port.get("loadState"), default=0)
            port_id = port.get("port") or port.get("externalPort") or port.get("portId")
            # Treat a port as plugged when resistance is not open-circuit (65535)
            # or the cloud reports the port online/loaded.
            if resistance != 65535 or online == 1 or load_state == 1:
                connected += 1
            LOGGER.debug(
                "AC Infinity port summary: port=%s resistance=%s online=%s loadState=%s connected=%s",
                port_id,
                resistance,
                online,
                load_state,
                resistance != 65535 or online == 1 or load_state == 1,
            )

        LOGGER.debug("AC Infinity connected port count: %s", connected)
        return connected

    def get_preferred_active_port(self):
        """Return best cloud-indicated active port for this controller.

        Preference order:
        1) deviceInfo.masterPort when valid
        2) first connected/online port from ports list
        3) current configured port
        """
        if self.mock_mode:
            return int(self.port)

        self._ensure_cloud_ready()
        body = self._post(
            self.API_URL_GET_DEVICE_INFO_LIST_ALL,
            {"userId": self.api_token},
            include_token=True,
        )
        devices = body.get("data", [])
        if not isinstance(devices, list) or not devices:
            return int(self.port)

        target = None
        for device in devices:
            if str(device.get("devId", "")).strip() == str(self.device_id):
                target = device
                break
        if target is None:
            target = devices[0]

        device_info = target.get("deviceInfo") or {}
        master_port = self._to_int(device_info.get("masterPort"), default=0)
        if master_port > 0:
            LOGGER.debug("AC Infinity preferred port from masterPort: %s", master_port)
            return master_port

        ports = device_info.get("ports") or target.get("ports") or []
        if isinstance(ports, list):
            for port in ports:
                if not isinstance(port, dict):
                    continue
                resistance = self._to_int(port.get("portResistance"), default=65535)
                online = self._to_int(port.get("online"), default=0)
                load_state = self._to_int(port.get("loadState"), default=0)
                port_id = self._to_int(
                    port.get("port") or port.get("externalPort") or port.get("portId"),
                    default=0,
                )
                if port_id > 0 and (resistance != 65535 or online == 1 or load_state == 1):
                    LOGGER.debug("AC Infinity preferred port from ports list: %s", port_id)
                    return port_id

        return int(self.port)

    @staticmethod
    def _to_int(value, default=0):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_float(value, default=None):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _iter_key_values(data):
        if isinstance(data, dict):
            for key, value in data.items():
                yield str(key), value
                if isinstance(value, (dict, list)):
                    for nested_key, nested_value in ACInfinityCloudClient._iter_key_values(value):
                        yield f"{key}.{nested_key}", nested_value
        elif isinstance(data, list):
            for index, value in enumerate(data):
                key = str(index)
                yield key, value
                if isinstance(value, (dict, list)):
                    for nested_key, nested_value in ACInfinityCloudClient._iter_key_values(value):
                        yield f"{key}.{nested_key}", nested_value

    @staticmethod
    def _extract_temperature_c(data):
        if not isinstance(data, (dict, list)):
            return None

        def _valid_c(value):
            return value is not None and -50.0 <= value <= 100.0

        def _valid_f(value):
            return value is not None and -58.0 <= value <= 212.0

        def _to_c_from_f(value_f):
            return (value_f - 32.0) * (5.0 / 9.0)

        def _normalize_c(raw):
            numeric = ACInfinityCloudClient._to_float(raw)
            if numeric is None:
                return None
            if _valid_c(numeric):
                return numeric
            scaled = numeric / 100.0
            if _valid_c(scaled):
                return scaled
            return None

        def _normalize_f(raw):
            numeric = ACInfinityCloudClient._to_float(raw)
            if numeric is None:
                return None
            if _valid_f(numeric):
                return _to_c_from_f(numeric)
            scaled = numeric / 100.0
            if _valid_f(scaled):
                return _to_c_from_f(scaled)
            return None

        preferred_c = []
        preferred_f = []
        fallback_c = []
        fallback_f = []

        for key, value in ACInfinityCloudClient._iter_key_values(data):
            key_l = key.lower()
            if "temp" not in key_l and "temperature" not in key_l:
                continue

            leaf = key_l.rsplit(".", 1)[-1]
            if leaf.startswith("target"):
                continue

            if leaf in ("temperature", "temp", "insidetemp", "outsidetemp"):
                preferred_c.append(value)
                continue
            if leaf in ("temperaturec", "tempc", "insidetempc", "outsidetempc"):
                preferred_c.append(value)
                continue
            if leaf in ("temperaturef", "tempf", "insidetempf", "outsidetempf"):
                preferred_f.append(value)
                continue

            if any(token in key_l for token in ("tempc", "temperaturec", "_c", ".c", "celsius")):
                fallback_c.append(value)
                continue
            if any(token in key_l for token in ("tempf", "temperaturef", "_f", ".f", "fahrenheit")):
                fallback_f.append(value)
                continue

            fallback_c.append(value)

        for candidate in preferred_c:
            normalized = _normalize_c(candidate)
            if normalized is not None:
                return normalized

        for candidate in preferred_f:
            normalized = _normalize_f(candidate)
            if normalized is not None:
                return normalized

        for candidate in fallback_c:
            normalized = _normalize_c(candidate)
            if normalized is not None:
                return normalized

        for candidate in fallback_f:
            normalized = _normalize_f(candidate)
            if normalized is not None:
                return normalized

        return None

    @staticmethod
    def _extract_humidity_percent(data):
        if not isinstance(data, (dict, list)):
            return None

        preferred = []
        fallback = []

        for key, value in ACInfinityCloudClient._iter_key_values(data):
            key_l = key.lower()
            leaf = key_l.rsplit(".", 1)[-1]

            if leaf.startswith("target"):
                continue

            if leaf in ("humidity", "humi", "rh"):
                preferred.append(value)
                continue

            if "humid" in key_l or "humi" in key_l or leaf == "rh" or key_l.endswith(".rh"):
                fallback.append(value)

        for candidate in preferred + fallback:
            numeric = ACInfinityCloudClient._to_float(candidate)
            if numeric is None:
                continue
            if 0.0 <= numeric <= 100.0:
                return numeric
            scaled = numeric / 100.0
            if 0.0 <= scaled <= 100.0:
                return scaled

        return None

    @staticmethod
    def _extract_vpd_kpa(data):
        if not isinstance(data, (dict, list)):
            return None

        preferred = []
        fallback = []

        for key, value in ACInfinityCloudClient._iter_key_values(data):
            key_l = key.lower()
            leaf = key_l.rsplit(".", 1)[-1]

            if "vpd" not in key_l:
                continue

            if any(token in leaf for token in ("target", "switch", "setting", "status")):
                continue

            if leaf in ("vpd", "vpdnum", "vpdnums", "vpdvalue"):
                preferred.append(value)
                continue

            fallback.append(value)

        for candidate in preferred + fallback:
            numeric = ACInfinityCloudClient._to_float(candidate)
            if numeric is None:
                continue
            if 0.0 <= numeric <= 10.0:
                return numeric
            scaled = numeric / 100.0
            if 0.0 <= scaled <= 10.0:
                return scaled

        return None

    def get_controller_environment(self):
        if self.mock_mode:
            return {
                "temperature_c": self._mock_state.get("temperature_c"),
                "humidity": self._mock_state.get("humidity"),
                "vpd_kpa": None,
            }

        self._ensure_cloud_ready()
        body = self._post(
            self.API_URL_GET_DEVICE_INFO_LIST_ALL,
            {"userId": self.api_token},
            include_token=True,
        )
        devices = body.get("data", [])
        if not isinstance(devices, list) or not devices:
            return {"temperature_c": None, "humidity": None, "vpd_kpa": None}

        target = None
        for device in devices:
            if str(device.get("devId", "")).strip() == str(self.device_id):
                target = device
                break
        if target is None:
            target = devices[0]

        device_info = target.get("deviceInfo") or {}
        sensor_source = device_info if isinstance(device_info, dict) and device_info else target
        temperature_c = self._extract_temperature_c(sensor_source)
        humidity = self._extract_humidity_percent(sensor_source)
        vpd_kpa = self._extract_vpd_kpa(sensor_source)

        # Fallback to mode settings payload if controller list lacks sensor values.
        if temperature_c is None or humidity is None or vpd_kpa is None:
            mode_data = self._read_raw_mode_settings()
            if temperature_c is None:
                temperature_c = self._extract_temperature_c(mode_data)
            if humidity is None:
                humidity = self._extract_humidity_percent(mode_data)
            if vpd_kpa is None:
                vpd_kpa = self._extract_vpd_kpa(mode_data)

        result = {"temperature_c": temperature_c, "humidity": humidity, "vpd_kpa": vpd_kpa}
        self._log_json("AC Infinity controller environment result", result)
        return result

    @staticmethod
    def _normalize_speed_level(value):
        """Normalize AC Infinity speed values to IoX fan level scale (0-10).

        Some payloads appear to use 0-10 while older mappings used 0-100.
        Accept both and collapse to 0-10 for consistent node behavior.
        """
        raw = ACInfinityCloudClient._to_int(value, default=0)
        raw = max(0, min(100, raw))
        if raw <= 10:
            return raw
        return max(1, min(10, int(round(raw / 10.0))))

    @staticmethod
    def _pick_speed(data):
        speed_candidates = [
            data.get("onSpead"),
            data.get("onSpeed"),
            data.get("onSelfSpead"),
            data.get("speak"),
        ]
        for candidate in speed_candidates:
            if candidate is not None:
                return ACInfinityCloudClient._to_int(candidate, default=0)
        return 0

    def _read_raw_mode_settings(self):
        body = self._post(
            self.API_URL_GET_DEV_MODE_SETTING,
            {
                "devId": self.device_id,
                "port": self.port,
            },
            include_token=True,
        )
        data = body.get("data", {})
        self._log_json("AC Infinity mode/settings parsed data", data)
        if not isinstance(data, dict):
            return {}
        return data

    def get_fan_state(self):
        if self.mock_mode:
            return dict(self._mock_state)

        self._ensure_cloud_ready()
        LOGGER.debug(
            "AC Infinity get_fan_state begin: device_id=%s port=%s controller_type=%s",
            self.device_id,
            self.port,
            self.controller_type,
        )
        data = self._read_raw_mode_settings()

        speed = self._normalize_speed_level(self._pick_speed(data))
        power_state = data.get("powerState")
        load_state = data.get("loadState")

        has_power_flag = power_state is not None
        has_load_flag = load_state is not None

        if has_power_flag or has_load_flag:
            is_on = bool(
                (self._to_int(power_state, default=0) == 1)
                or (self._to_int(load_state, default=0) == 1)
            )
        else:
            # Fallback only when cloud does not provide explicit power/load flags.
            is_on = speed > 0

        # AC Infinity often keeps the configured ON speed even while power is off.
        # Report runtime speed as 0 when the fan is off so IoX state is consistent.
        if not is_on:
            speed = 0
        result = {
            "is_on": is_on,
            "speed": speed,
        }
        self._log_json("AC Infinity get_fan_state result", result)
        return result

    def _post_mode(self, at_type, speed):
        payload = {
            "atType": int(at_type),
            "devId": self.device_id,
            "externalPort": self.port,
            "modeType": 15,
            "settingMode": 0,
        }
        if int(at_type) == 1:
            payload["offSpead"] = 0
            payload["offSpeed"] = 0
            payload["powerState"] = 0
            payload["loadState"] = 0
        else:
            payload["onSpead"] = int(speed)
            payload["onSpeed"] = int(speed)
            payload["onSelfSpead"] = int(speed)
            payload["speak"] = int(speed)
            payload["powerState"] = 1
            payload["loadState"] = 1

        self._post(self.API_URL_ADD_DEV_MODE, payload, include_token=True)
        self._log_json("AC Infinity addDevMode sent payload", self._redact_payload(payload))

    def _put_mode_and_setting(self, at_type, speed):
        payload = {
            "atType": int(at_type),
            "devId": self.device_id,
            "port": self.port,
            "externalPort": self.port,
        }
        if int(at_type) == 1:
            payload["modeAndSettingIdStr"] = "[16,17]"
            payload["modeType"] = 15
            payload["settingMode"] = 0
            payload["offSpeed"] = 0
            payload["offSpead"] = 0
        else:
            payload["modeAndSettingIdStr"] = "[16,18]"
            payload["modeType"] = 15
            payload["settingMode"] = 0
            payload["onSpeed"] = int(speed)
            payload["onSpead"] = int(speed)

        self._put(
            self.API_URL_MODE_AND_SETTINGS,
            payload,
            include_token=True,
            use_min_version=True,
        )
        self._log_json("AC Infinity modeAndSetting sent payload", self._redact_payload(payload))

    def _write_mode(self, at_type, speed):
        LOGGER.debug(
            "AC Infinity _write_mode dispatch: controller_type=%s at_type=%s speed=%s device_id=%s port=%s",
            self.controller_type,
            at_type,
            speed,
            self.device_id,
            self.port,
        )
        if self.controller_type == "controller69":
            self._post_mode(at_type=at_type, speed=speed)
            return

        if self.controller_type in ("controller69pro", "ai_plus"):
            try:
                self._put_mode_and_setting(at_type=at_type, speed=speed)
                return
            except Exception:
                self._post_mode(at_type=at_type, speed=speed)
                return

        try:
            self._put_mode_and_setting(at_type=at_type, speed=speed)
            return
        except Exception:
            self._post_mode(at_type=at_type, speed=speed)
            return

    def set_power(self, is_on, speed_preference=None):
        if self.mock_mode:
            self._mock_state["is_on"] = bool(is_on)
            if not is_on:
                self._mock_state["speed"] = 0
            elif self._mock_state["speed"] == 0:
                self._mock_state["speed"] = 10
            return dict(self._mock_state)

        self._ensure_cloud_ready()
        current = self.get_fan_state()
        preferred = self._normalize_speed_level(speed_preference) if speed_preference is not None else 0
        speed = preferred if preferred > 0 else (current["speed"] if current["speed"] > 0 else 10)

        LOGGER.debug(
            "AC Infinity set_power request: is_on=%s current_state=%s chosen_speed=%s",
            bool(is_on),
            current,
            speed,
        )

        if is_on:
            # For controller69, force an explicit OFF->ON transition to kick
            # output when cloud state already reports ON but hardware is idle.
            if self.controller_type == "controller69":
                self._write_mode(at_type=1, speed=0)
            self._write_mode(at_type=2, speed=max(1, speed))
        else:
            self._write_mode(at_type=1, speed=0)

        result = self.get_fan_state()
        LOGGER.debug("AC Infinity set_power result: %s", result)
        return result

    def set_speed(self, speed):
        speed = self._normalize_speed_level(speed)
        if self.mock_mode:
            self._mock_state["speed"] = speed
            self._mock_state["is_on"] = speed > 0
            return dict(self._mock_state)

        self._ensure_cloud_ready()
        LOGGER.debug(
            "AC Infinity set_speed request: speed=%s device_id=%s port=%s controller_type=%s",
            speed,
            self.device_id,
            self.port,
            self.controller_type,
        )
        if speed == 0:
            self._write_mode(at_type=1, speed=0)
        else:
            self._write_mode(at_type=2, speed=speed)
        result = self.get_fan_state()
        LOGGER.debug("AC Infinity set_speed result: %s", result)
        return result
