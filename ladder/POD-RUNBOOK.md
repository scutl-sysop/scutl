# GPU pod bring-up runbook (cst-c7x)

Distilled from the 2026-08-14 run night (pod mjrs9vdabora6i, rev-2
public-tls rungs). Goal: pod serving the rung model in ≤15 minutes,
zero rediscovery of the gotchas below.

## Standing facts

- **Provider**: RunPod, single 4090-class, secure cloud. ~$0.74/hr as of
  2026-08-14.
- **Region**: **EU-RO-1** — non-negotiable, the model volume lives there.
- **Network volume**: `scutl-ladder-models`, id `jo8roirsw9`, 45 GB
  (grown 25→45 on 2026-08-14, owner-approved). Mounts at `/workspace`.
  Holds a prebuilt CUDA `llama-server` (**b10380**) and cached models:
  - `Qwen3.6-27B-Q4_K_M.gguf` (reference; sha256 `5ed60d0a…`)
  - headline models are pulled per-run (pod-local disk) unless cached.
- **Controller**: incus container `scutl-ladder` on dedi-2 (Hermes
  pinned; see `controller-setup.sh`). It reaches the pod model over an
  ssh tunnel, never a public port.

## Pod-create checklist (RunPod console)

Every one of these bit us once. Do them at CREATE time.

1. **Image**: `runpod/pytorch:*-devel` (any recent devel tag).
   **Never `nvidia/cuda` images — they come up with NO networking on
   RunPod.** (If we ever move to the pinned
   `ghcr.io/ggml-org/llama.cpp:server-cuda-<tag>` image per the
   2026-08-12 decision, verify networking on it FIRST — that test has
   not been run.)
2. **Do not set a container start command** — dockerStartCmd overrides
   don't take on these images. Bring-up happens over ssh after boot.
3. **Expose TCP port 22** (and 8080 only if you want a direct fallback;
   the normal path is the ssh tunnel). **Ports cannot be added after
   create** — a pod missing a port gets destroyed and re-created.
4. **Attach network volume** `scutl-ladder-models` (jo8roirsw9). This
   forces EU-RO-1.
5. Container disk ≥ 60 GB if the rung model is NOT already on the
   volume (headline models are 20–30 GB and land on pod-local disk).
6. Add your ssh public key (RunPod account setting; pods inherit it).

## Bring-up (one shot)

From the controller (or dedi-2), once the pod shows Running and you
have its ssh connect string from the console:

```bash
# 1. copy the script in and run it
scp -o BatchMode=yes pod-up.sh root@<pod-ssh-host>:/root/ -P <pod-ssh-port>
ssh -o BatchMode=yes -p <pod-ssh-port> root@<pod-ssh-host> \
  'MODEL_FILE=Qwen3.6-27B-Q4_K_M.gguf CTX=65536 bash /root/pod-up.sh'
```

`pod-up.sh` prefers the volume's prebuilt server and cached model,
falls back to download/build only when they're absent, writes
`env.json`, health-checks the server, and leaves it running under
nohup. See the script header for MODEL_REPO/MODEL_FILE overrides
(headline rung).

```bash
# 2. tunnel from the controller — hermes talks to 127.0.0.1:18080
ssh -o BatchMode=yes -N -f -L 18080:127.0.0.1:8080 \
  -p <pod-ssh-port> root@<pod-ssh-host>
# in the controller container:
hermes config set model.base_url http://127.0.0.1:18080/v1
```

```bash
# 3. sanity before spending anything
curl -s http://127.0.0.1:18080/v1/models | python3 -m json.tool
```

Then fetch `env.json` off the pod into the rung workdir — it is a
receipt input, not optional:

```bash
scp -o BatchMode=yes -P <pod-ssh-port> root@<pod-ssh-host>:/workspace/ladder/env.json .
```

## Gotchas index (why the checklist says what it says)

| Gotcha | Consequence if forgotten |
|---|---|
| nvidia/cuda images: no networking | dead pod, re-create |
| ports fixed at create | dead pod, re-create |
| no dockerStartCmd override | silently ignored; bring up via ssh |
| nvcc off PATH in non-login shells | CUDA build fails mysteriously |
| some images lack cmake | build fails; pod-up installs it |
| hermes tool shells rebuild PATH from login profile | shim-first PATH lost; bind commands via `~/.local/bin` (see hermes-drive.sh, scutl 6be2aae) |
| controller repo venv is `.venv` but scripts say `$REPO/venv` | symlink `venv -> .venv` by hand on a fresh controller |
| buyer wallet cap_daily = 3.50 (per-tx 0.10) | two-rung day + makeups fits; a third rung same day may trip CapExceeded by design |

## Teardown

- Archive rung evidence off the pod BEFORE destroy (`rung-*/`,
  `env.json`, any `harness-reds/`).
- Anything worth caching for next run (new model file, newer prebuilt
  server) gets copied to `/workspace` (the volume) first — volume
  storage is cheap, re-downloading 17 GB is not.
- Destroy the pod; the volume persists. Kill the controller-side
  tunnel (`pkill -f 'L 18080:'`).

## Status

Runbook + `pod-up.sh` written 2026-08-14 from run-night evidence;
`pod-up.sh` is shellcheck-clean but has NOT yet run on a live pod —
first task of the next run day is to bring the pod up with it and
strike this line.
