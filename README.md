# ISY AC Infinity Fan (PG3)

This is a starter Polyglot v3 nodeserver for controlling an AC Infinity fan from ISY/IoX (UD Mobile and Admin Console/web).

Implementation note: this project uses original clean-room code in this repository. It does not copy source from other projects.

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
- `api_base_url`: default `https://www.acinfinityserver.com`
- `api_token`: optional app token (appId). If empty, login is performed using email/password.
- `email`: AC Infinity account email (used when `api_token` is not set)
- `password`: AC Infinity account password (used when `api_token` is not set)
- `device_id`: target controller device id (`devId`). If empty, first account device is used.
- `port`: fan port number (default `1`)
- `user_agent`: request user-agent header (default `okhttp/4.12.0`)

## Cloud Payload Mapping

The current client uses these cloud calls:

- `POST /api/user/appUserLogin`
- `POST /api/user/devInfoListAll`
- `POST /api/dev/getdevModeSettingList`
- `POST /api/dev/addDevMode`

Read mapping for fan state is defensive and checks multiple keys (`speak`, `onSpead`, `onSpeed`, power/load state fields).

## Install

1. Put this project in a git repo.
2. In PG3, add a new local/custom nodeserver and point it to the repo.
3. Start nodeserver with `mock_mode=true` first.
4. Confirm fan node appears in IoX and control works in UD Mobile.
5. Switch to `mock_mode=false` and set token or email/password plus device/port.
6. Restart nodeserver and validate live cloud control.

## Notes

- BLE support is not implemented in this initial version.
- Long poll is set to 300 seconds; tune in `server.json` as needed.
- You can add multiple fan nodes later by expanding controller logic and parameters.
