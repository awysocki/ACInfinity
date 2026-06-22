import requests


class ACInfinityCloudClient:
    """Clean-room AC Infinity cloud client based on observed endpoint behavior."""

    API_URL_LOGIN = "/api/user/appUserLogin"
    API_URL_GET_DEVICE_INFO_LIST_ALL = "/api/user/devInfoListAll"
    API_URL_GET_DEV_MODE_SETTING = "/api/dev/getdevModeSettingList"
    API_URL_ADD_DEV_MODE = "/api/dev/addDevMode"

    def __init__(
        self,
        api_base_url,
        api_token,
        device_id,
        email,
        password,
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
        self.port = int(port) if str(port).strip() else 1
        self.user_agent = user_agent.strip() if user_agent else "okhttp/4.12.0"
        self.mock_mode = mock_mode
        self.timeout = timeout

        self._mock_state = {
            "is_on": False,
            "speed": 0,
        }

    def _headers(self, include_token=True):
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": self.user_agent,
        }
        if include_token and self.api_token:
            headers["token"] = self.api_token
        return headers

    def _post(self, path, payload, include_token=True):
        response = requests.post(
            f"{self.api_base_url}{path}",
            headers=self._headers(include_token=include_token),
            data=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 200:
            msg = body.get("msg", "unknown API error")
            raise ValueError(f"AC Infinity API error for {path}: {msg}")
        return body

    def _ensure_cloud_ready(self):
        if not self.api_token:
            if not self.email or not self.password:
                raise ValueError("Set api_token OR set both email and password")
            self.login()

        if not self.device_id:
            self.device_id = self._discover_first_device_id()

        if self.port < 0:
            raise ValueError("port must be >= 0")

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
        return self.api_token

    def _discover_first_device_id(self):
        body = self._post(
            self.API_URL_GET_DEVICE_INFO_LIST_ALL,
            {"userId": self.api_token},
            include_token=True,
        )
        devices = body.get("data", [])
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
    def _pick_speed(data):
        speed_candidates = [
            data.get("speak"),
            data.get("onSpead"),
            data.get("onSpeed"),
            data.get("onSelfSpead"),
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
        if not isinstance(data, dict):
            return {}
        return data

    def get_fan_state(self):
        if self.mock_mode:
            return dict(self._mock_state)

        self._ensure_cloud_ready()
        data = self._read_raw_mode_settings()

        speed = max(0, min(100, self._pick_speed(data)))
        power_state = data.get("powerState")
        load_state = data.get("loadState")
        is_on = bool(
            (self._to_int(power_state, default=0) == 1)
            or (self._to_int(load_state, default=0) == 1)
            or speed > 0
        )
        return {"is_on": is_on, "speed": speed}

    def _post_mode(self, at_type, speed):
        payload = {
            "atType": int(at_type),
            "devId": self.device_id,
            "externalPort": self.port,
        }
        if int(at_type) == 1:
            payload["offSpead"] = 0
            payload["offSpeed"] = 0
        else:
            payload["onSpead"] = int(speed)
            payload["onSpeed"] = int(speed)

        self._post(self.API_URL_ADD_DEV_MODE, payload, include_token=True)

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

        if is_on:
            self._post_mode(at_type=2, speed=speed)
        else:
            self._post_mode(at_type=1, speed=0)
        return self.get_fan_state()

    def set_speed(self, speed):
        speed = max(0, min(100, int(speed)))
        if self.mock_mode:
            self._mock_state["speed"] = speed
            self._mock_state["is_on"] = speed > 0
            return dict(self._mock_state)

        self._ensure_cloud_ready()
        if speed == 0:
            self._post_mode(at_type=1, speed=0)
        else:
            self._post_mode(at_type=2, speed=speed)
        return self.get_fan_state()
