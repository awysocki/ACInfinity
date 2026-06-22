import requests


class ACInfinityCloudClient:
    def __init__(
        self,
        api_base_url,
        api_token,
        device_id,
        status_path,
        power_path,
        speed_path,
        mock_mode=True,
        timeout=15,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.api_token = api_token.strip() if api_token else ""
        self.device_id = device_id.strip() if device_id else ""
        self.status_path = status_path
        self.power_path = power_path
        self.speed_path = speed_path
        self.mock_mode = mock_mode
        self.timeout = timeout

        self._mock_state = {
            "is_on": False,
            "speed": 0,
        }

    def _path(self, template):
        return template.format(device_id=self.device_id)

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def _request(self, method, path, payload=None):
        url = f"{self.api_base_url}{path}"
        response = requests.request(
            method=method,
            url=url,
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}

    def _ensure_cloud_ready(self):
        if not self.device_id:
            raise ValueError("Missing custom param: device_id")
        if not self.api_token:
            raise ValueError("Missing custom param: api_token")

    def get_fan_state(self):
        if self.mock_mode:
            return dict(self._mock_state)

        self._ensure_cloud_ready()
        payload = self._request("GET", self._path(self.status_path))

        # Update this mapping to your reverse-engineered JSON fields.
        speed = int(payload.get("speed", 0))
        is_on = bool(payload.get("is_on", speed > 0))
        return {
            "is_on": is_on,
            "speed": max(0, min(100, speed)),
        }

    def set_power(self, is_on):
        if self.mock_mode:
            self._mock_state["is_on"] = bool(is_on)
            if not is_on:
                self._mock_state["speed"] = 0
            elif self._mock_state["speed"] == 0:
                self._mock_state["speed"] = 100
            return dict(self._mock_state)

        self._ensure_cloud_ready()
        self._request("POST", self._path(self.power_path), {"on": bool(is_on)})
        return self.get_fan_state()

    def set_speed(self, speed):
        speed = max(0, min(100, int(speed)))
        if self.mock_mode:
            self._mock_state["speed"] = speed
            self._mock_state["is_on"] = speed > 0
            return dict(self._mock_state)

        self._ensure_cloud_ready()
        self._request("POST", self._path(self.speed_path), {"speed": speed})
        return self.get_fan_state()
