# ISY AC Infinity Fan (PG3)

This is a starter Polyglot v3 nodeserver for controlling an AC Infinity fan from ISY/IoX (UD Mobile and Admin Console/web).

## Current Scope

- Cloud API path (reverse-engineered endpoints)
- Fan power on/off
- Fan speed set/query (0-100)
- Polling-based state refresh
- Mock mode for UI testing before cloud endpoints are finalized

## Files

- `acinf_nodeserver.py`: PG3 controller + fan node logic
- `acinf_cloud.py`: AC Infinity cloud client (replace payload mappings as needed)
- `server.json`: PG3 metadata and custom parameter definitions
- `profile/`: nodedefs/editors/NLS shown in IoX

## Custom Parameters

Set these in PG3 for the nodeserver:

- `mock_mode`: `true` or `false` (`true` by default)
- `api_base_url`: default `https://api.acinfinity.com`
- `api_token`: cloud bearer token
- `device_id`: target fan device id
- `status_path`: default `/v1/devices/{device_id}`
- `power_path`: default `/v1/devices/{device_id}/power`
- `speed_path`: default `/v1/devices/{device_id}/speed`

If your reverse-engineered routes differ, update the path params above and adjust the JSON mappings in `acinf_cloud.py`.

## Cloud Payload Mapping

The current client assumes:

- status response contains `speed` and optional `is_on`
- power endpoint accepts `{ "on": true|false }`
- speed endpoint accepts `{ "speed": 0..100 }`

If your captures use different keys, edit `get_fan_state`, `set_power`, and `set_speed` in `acinf_cloud.py`.

## Install

1. Put this project in a git repo.
2. In PG3, add a new local/custom nodeserver and point it to the repo.
3. Start nodeserver with `mock_mode=true` first.
4. Confirm fan node appears in IoX and control works in UD Mobile.
5. Switch to `mock_mode=false` and set token/device/paths.
6. Restart nodeserver and validate live cloud control.

## Notes

- BLE support is not implemented in this initial version.
- Long poll is set to 300 seconds; tune in `server.json` as needed.
- You can add multiple fan nodes later by expanding controller logic and parameters.
