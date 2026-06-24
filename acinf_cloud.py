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
        }

    @staticmethod
    def _redact_payload(payload):
        redacted = dict(payload or {})
        for key in ("password", "appPasswordl", "appPassword", "token"):
            if key in redacted and redacted[key] not in (None, ""):
                redacted[key] = "<redacted>"
        return redacted

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

    @staticmethod
    def _to_int(value, default=0):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

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
        is_on = bool(
            (self._to_int(power_state, default=0) == 1)
            or (self._to_int(load_state, default=0) == 1)
            or speed > 0
        )
        result = {"is_on": is_on, "speed": speed}
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

    def set_power(self, is_on):
        if self.mock_mode:
            self._mock_state["is_on"] = bool(is_on)
            if not is_on:
                self._mock_state["speed"] = 0
            elif self._mock_state["speed"] == 0:
                self._mock_state["speed"] = 10
            return dict(self._mock_state)

        self._ensure_cloud_ready()
        current = self.get_fan_state()
        speed = current["speed"] if current["speed"] > 0 else 10

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
