# ISY AC Infinity Fan (PG3)

This is a starter Polyglot v3 nodeserver for controlling an AC Infinity fan from ISY/IoX (UD Mobile and Admin Console/web).

Implementation note: this project uses original clean-room code in this repository. It does not copy source from other projects.

## Current Scope

- Cloud API path (reverse-engineered endpoints)
- Fan power on/off
- Fan speed set/query (0-10)
- Polling-based state refresh
- Mock mode for UI testing before cloud endpoints are finalized

## Files

- `acinf_nodeserver.py`: PG3 controller + fan node logic
- `acinf_cloud.py`: AC Infinity cloud client (replace payload mappings as needed)
- `server.json`: PG3 metadata and custom parameter definitions
- `profile/`: nodedefs/editors/NLS shown in IoX

## Custom Parameters

Set these in PG3 for the nodeserver:

- `user`: AC Infinity account email/username
- `password`: AC Infinity account password (used when `api_token` is not set)
- `moredebug`: set to `1` to log extra AC Infinity request/response details

Advanced values (`api_base_url`, `controller_type`, `device_id`, `port`, `user_agent`, `mock_mode`, and `api_token`) are handled internally with defaults and are intentionally not shown in the default PG3 custom parameter list.

Node creation behavior:

- No fan/runtime nodes are created until cloud login and device discovery succeed.
- Enter `user` and `password`, save, and restart (or wait for long poll) to trigger node creation.

Security warning:

- AC Infinity cloud API behavior suggests HTTP transport may be required.
- If using email/password or token in live mode, treat network path as potentially unencrypted.
- Use a dedicated AC Infinity account/password for automation.

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

## License

- MIT License
- Repository license file: https://github.com/awysocki/ISYACINF/blob/main/LICENSE
