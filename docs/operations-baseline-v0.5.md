# Operations Baseline v0.5

This document defines the stable working baseline starting at version `0.5`.

## Source of truth

- Repository: `https://github.com/wollux/hermes2stackchan`
- Bridge + firmware are managed from this repo.
- Working branch baseline at creation: `feature/issue-21-reminders`

## Real production topology

- True bridge host (remote Pi): `hermespi`
- SSH login user: `wollux`
- SSH host/IP: `192.168.99.58`
- Bridge HTTP endpoint: `http://192.168.99.58:8788`
- Hermes API endpoint used by bridge: `http://127.0.0.1:8642/v1/chat/completions`

## Service ownership on Pi

- `hermes-api-server.service` owns `127.0.0.1:8642`
- `hermes-gateway.service` runs Hermes gateway
- `hermes2stackchan.service` runs the unified bridge (audio/http + workers)
- `hermes2stackchan-life-animator.service` runs life animation loop

## Required ports

- `8788/tcp` bridge HTTP ingress (StackChan audio, image/display API)
- `8642/tcp` local Hermes standalone API (Pi-local loopback)

## Standard recovery + deploy flow

1. Update local checkout to latest GitHub branch state.
2. Sync repo to Pi (`/home/wollux/hermes2stackchan`) without overwriting local secrets.
3. Restart Pi services in this order:
   1. `hermes-api-server.service`
   2. `hermes-gateway.service`
   3. `hermes2stackchan.service`
   4. `hermes2stackchan-life-animator.service`
4. Verify:
   - bridge answers on `/stackchan/audio`
   - face/motion events are published
   - TTS URL is generated and retrievable

## Firmware flash flow (authoritative)

1. Sync firmware values from `.env` into `firmware/sdkconfig`:
   - `python3 scripts/apply_firmware_env.py --env .env --sdkconfig firmware/sdkconfig`
2. Build firmware:
   - `cd firmware && idf.py build`
3. Flash firmware:
   - `idf.py -p <serial-port> flash`
4. Reboot stack and verify bridge reconnect in logs.

## Secret policy

- Pi password is local-only and must not be committed to GitHub.
- Keep local secrets in `.run/local-secrets/` (gitignored).
- Repo documentation may store host/user/topology, but never plaintext passwords.
