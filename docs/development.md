# Development and handoff

## Layout

| Path | Responsibility |
| --- | --- |
| `marsi.py`, `phrases.json`, `machine_art.py` | Original independent template/Zulip bot |
| `marsi_local/personality.txt` | Marsi's conversational character |
| `marsi_local/core.py` | Ollama calls and bounded SQLite history/notes |
| `marsi_local/speech.py` | Optional Whisper/Piper loading and WAV validation |
| `marsi_local/server.py` | Authenticated HTTP endpoints |
| `marsi_local/client.py` | Shared terminal/Pi API client |
| `marsi_local/pi.py`, `ambient.py` | Display, push-to-talk, playback, rituals |
| `config/` | Shareable setting examples, without credentials |
| `deploy/`, `scripts/` | Installation and startup templates |

Changing Qwen only needs a model download and `MARSI_MODEL` setting; changing
the body later need not change the server. This separation lets us upgrade the
computer while keeping the same Pi interface.

## Memory and behaviour

Conversation text is saved automatically in `data/companion.sqlite3` on the
Ubuntu server. Each session retains up to 30 exchanges; the last six complete
exchanges, limited to about 6,000 characters, are supplied to Qwen. This keeps
the context small enough for our initial 4,096-token setting in normal short
conversations. Long inputs or token-heavy languages may use more of that budget.

Use `/remember TEXT` in the terminal client for an explicit note (up to 200
characters), `/notes` to inspect notes, and `/forget` to clear the current
session's history and notes. Each session holds up to 12 notes; at most 100
sessions are retained. Both frontends default to the same `pi` session. Use
distinct `MARSI_SESSION` values for independent conversations.

The shared token is for one trusted household, not separate user accounts.
Anyone who has it can access any named session. Audio is not stored in the
repository or database; microphone uploads remain in memory, and reply WAVs
on the Pi use a temporary directory deleted after playback. SQLite deletion
removes records logically; it is not a forensic erase, and copies/backups remain.

Memory provides continuity; it does not retrain Qwen or make Marsi self-learning.
The model has no execution tools. Schedules and animations are ordinary application
code, and generated text is never interpreted as a shell command. The only
subprocess in the Pi app is the fixed `aplay` playback program.

## API

Authenticated requests use `Authorization: Bearer YOUR_TOKEN`. JSON requests
use `Content-Type: application/json`. The only unauthenticated route is health.

| Method / route | Input | Result |
| --- | --- | --- |
| `GET /health` | None | API process health and demo/local-llm mode |
| `POST /v1/chat` | `text`, `session_id`, optional boolean `want_audio` | Reply text, animation, source, optional WAV as `audio_base64` |
| `POST /v1/voice` | `audio/wav` bytes; `X-Marsi-Session` header | Transcript, reply, optional audio; `?want_audio=false` skips synthesis |
| `POST /v1/ritual` | `session_id`, optional `want_audio` | Authored ritual, independent of private memory |
| `GET /v1/memory?session_id=pi` | Session in query | Explicit notes |
| `POST /v1/memory` | `session_id`, `text` | Add explicit note |
| `DELETE /v1/session` | `session_id` | Clear that session's notes and exchanges |

Chat text is capped at 2,000 characters. WAV uploads are capped at 2 MB and
20 seconds, mono 16-bit PCM. One task at a time runs on the old CPU; simultaneous
inference requests receive a retryable error. This is a small LAN prototype,
not a public internet service. No browser frontend or CORS is required.

## Verification

From the checkout:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q marsi_local
bash -n scripts/setup-server.sh scripts/setup-pi.sh deploy/marsi-xsession.sh
```

Tests exercise real local HTTP requests and a temporary SQLite database, with
fake Qwen/speech engines. They verify authentication, bounded and durable memory,
forgetting, busy handling, WAV limits, speech failure behaviour and quiet hours,
as well as the original bot's regressions. They do not benchmark real models.

On Linux, install `python3-tk` and `xvfb`, then run:

```bash
xvfb-run -a python3 -m tests.pi_display_smoke
```

The GitHub workflow runs tests on Python 3.10 and 3.12 and includes the real Tk
widget smoke test. Actual microphone, speaker and model inference still need the
hardware acceptance sequence in the setup guides.

## Next milestones

1. Measure Qwen cold/warm latency and peak RAM on the i5; adjust model and thread
   count. Keep Whisper tiny initially. Record the OS and package versions used.
2. Verify microphone and speaker hardware on the Pi. Tune the display to its
   resolution and touch input. Only then enable boot startup.
3. Add streaming speech for shorter perceived delays and, if useful, a wake word
   with an obvious microphone indicator and physical mute option.
4. Add an owner-reviewed memory editor and a small library of Marsi chibi artwork.
   An image-generation engine is a separate optional project component.
5. Add narrowly defined useful skills, such as read-only server health reporting.
   Each device-changing action needs a deliberate interface and an explicit policy.

## Prompt for continuing on Ubuntu

Copy this into your next development chat, opened in the repository:

> Continue the Marsi local companion in mbvoyager/tiny-tech-priest-marsi on branch
> feature/local-companion. Read docs/start-here.md, docs/ubuntu-server.md and
> docs/development.md, then inspect the current checkout and changes. Target hardware:
> Ubuntu on i5-4460, Intel HD graphics, 8 GB DDR3 and HDD; Raspberry Pi 3 B+ with 1 GB
> RAM, display, microphone and speaker. Initial stack: Ollama qwen3:1.7b CPU,
> faster-whisper tiny/int8, Piper voice on the server, Tkinter/sounddevice/aplay on
> the Pi. First verify a real Qwen text response, then speech and Pi audio. Keep
> Marsi tiny, cute, warm, devoted to the fictional Machine God and helpful to
> humanity. Preserve the original independent template/Zulip bot. Keep credentials,
> models and conversations out of Git. Explain each meaningful change simply and
> run appropriate tests. Do not claim consciousness or hardware results not measured.
