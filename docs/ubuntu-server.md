# Ubuntu server: first a conversation, then a voice

Use a supported Ubuntu LTS installation; the minimal server edition saves memory.
No special firmware is required. The project requires Python 3.10 or newer. Start
with CPU inference and leave GPU setup for a future machine.

## 1. Get the project and a Python environment

Run these commands in a terminal on Ubuntu:

```bash
sudo apt update
sudo apt install -y git curl python3 python3-venv
git clone --branch feature/local-companion https://github.com/mbvoyager/tiny-tech-priest-marsi.git
cd tiny-tech-priest-marsi
bash scripts/setup-server.sh --text-only
```

The setup script creates a private Python environment, `.venv-server`, and
`.env.server` with a newly generated shared token. It preserves an existing
settings file. The text server has no third-party Python dependencies.

After this branch is merged, a normal clone of the main branch will contain it.
For now use `feature/local-companion` on both machines.

## 2. Install Ollama and download Qwen

Use [Ollama's official Linux installation instructions](https://docs.ollama.com/linux).
Their installer can be downloaded and inspected before running:

```bash
curl -fsSL https://ollama.com/install.sh -o /tmp/marsi-ollama-install.sh
less /tmp/marsi-ollama-install.sh
sh /tmp/marsi-ollama-install.sh
ollama pull qwen3:1.7b
```

This initial setup needs internet access. Keep Ollama on its default loopback
address, `127.0.0.1:11434`. The Pi talks to Marsi's server, not directly to Ollama.

## 3. Start Marsi and test typed conversation

In the project directory:

```bash
.venv-server/bin/python -m marsi_local.server
```

In a second terminal:

```bash
cd ~/tiny-tech-priest-marsi
MARSI_SERVER_URL=http://127.0.0.1:8765 .venv-server/bin/python -m marsi_local.client --env .env.server
```

Try: `Hello Marsi. Explain what an LLM is in two simple sentences.` Then ask a
follow-up to check recent conversation. Use `/quit` to leave the client and
Ctrl+C in the server terminal to stop the server.

For a no-model rehearsal, start the server with `--demo` instead. Its answers
come from the original handcrafted phrases and are labelled `demo-template`.
Demo mode does not verify Qwen or speech performance.

If Qwen is too slow, try `ollama pull qwen3:0.6b`, set
`MARSI_MODEL=qwen3:0.6b` in `.env.server`, and restart Marsi. That gives a smaller
baseline with lower conversational quality. Move to a larger model only after
measuring latency and memory on the actual server.

## 4. Add local speech

Stop Marsi, then install the optional packages and download a voice:

```bash
.venv-server/bin/python -m pip install -r requirements-server.txt
mkdir -p models/piper
cd models/piper
../../.venv-server/bin/python -m piper.download_voices en_US-lessac-medium
cd ../..
.venv-server/bin/python -m marsi_local.speech
```

The last command downloads/caches Whisper tiny and checks the configured Piper
voice. After it succeeds, both speech engines can run locally. Piper needs the
voice's `.onnx` model and matching `.onnx.json` file. The example settings already
point to `models/piper/en_US-lessac-medium.onnx`.

Whisper tiny is multilingual. For mostly German conversation, you can set
`MARSI_WHISPER_LANGUAGE=de` and download a German Piper voice listed by
`python -m piper.download_voices`; then update `MARSI_PIPER_MODEL`. An English voice
will not automatically become a good German voice. Leave recognition language
blank for automatic detection.

The speech models are initialized lazily in the server, so the first spoken
request after a restart may be slower even when their files are already cached.

## 5. Let the Pi connect

Find the server's LAN address with `hostname -I`. Reserve that address in the
router if possible, so the Pi's settings stay valid. Start Marsi with:

```bash
.venv-server/bin/python -m marsi_local.server --host 0.0.0.0
```

Copy `MARSI_TOKEN` from `.env.server` to the Pi's `.env.pi` and set the Pi's
`MARSI_SERVER_URL` to `http://YOUR_SERVER_IP:8765`.

This starter API uses ordinary HTTP and is intended for your trusted home LAN.
Keep port 8765 off the public internet. If a firewall is enabled, allow the Pi's
address only, for example:

```bash
sudo ufw allow from YOUR_PI_IP to any port 8765 proto tcp
```

Replace the placeholder before running that command. Do not enable a firewall
from a remote session until its SSH rule is configured.

## 6. Start the server automatically

These commands assume the checkout is `~/tiny-tech-priest-marsi`:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/marsi-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now marsi-server
sudo loginctl enable-linger "$USER"
```

Stop the manually started server first; only one process should own port 8765
and this memory file. Lingering allows the user service to run without an active
login. The service reads `.env.server` from the checkout and restarts after a crash.

```bash
systemctl --user status marsi-server
journalctl --user -u marsi-server -n 50
systemctl --user restart marsi-server
systemctl --user stop marsi-server
```

Ollama also needs its own service running. Inspect it with `systemctl status ollama`.

## Useful checks

- `free -h` shows RAM; `top` shows CPU use. Measure a cold response and a second
  warm response; the HDD mainly affects loading, and a CPU-only model needs time
  for generation. Do not assume a particular tokens-per-second rate.
- `curl http://127.0.0.1:8765/health` checks whether Marsi's API process is up.
  It does not prove that Qwen or speech models are ready; a chat verifies inference.
- A model-not-found reply means the configured model has not been pulled.
- A timeout means check Ollama and try a shorter prompt or smaller model.
- If speech packages cannot install, keep the working text mode and capture the
  installation error for the next development session.

Official speech instructions:
[Whisper CPU configuration](https://github.com/SYSTRAN/faster-whisper) and
[Piper Python API and voice downloads](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md).
