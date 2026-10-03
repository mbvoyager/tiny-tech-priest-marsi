# Marsi, pocket-sized tech-priest

A cute, mildly context-aware nonsense bot for Zulip **or your terminal**. Python 3.10 or newer,
with **no packages, AI API keys, model downloads, or AI fees**.

## Start and stop

Double-click **`start-marsi-local.cmd`** to chat with Marsi entirely on your laptop.
This needs no Zulip credentials or access and makes no network requests.
Type a message at `You > ` and press **Enter**. Both `I need coffee` and
`@Marsi I need coffee` trigger his usual keyword-based replies.

The original **`start-marsi.cmd`** tries Zulip first. If access is denied, the
server cannot be reached, or the bot/channel configuration is unavailable, it
switches to the same terminal mode. Losing access during a Zulip session also
switches to the terminal. It stays local for the rest of that run; it does not
keep trying a blocked account or reconnect unexpectedly.

Keep the console open. Press **Ctrl+C**, close the console, or type **`quit`**
in terminal mode to stop. Restart to load changes to the code, settings, or phrases.

Terminal commands:

| Input | Result |
| --- | --- |
| Any text, optionally starting with `@Marsi` | A normal Marsi reply |
| `help` | Explain the terminal commands |
| `status` | Show the saved sermon and hourly artwork due times |
| `sermon` | Print a framed bonus sermon immediately, without changing the timer |
| `art` | Generate a machine-cult ASCII display immediately, without changing timers |
| `quit` or `exit` | Stop Marsi |

Commands are case-insensitive and only trigger when they are the whole message.
The older slash forms still work as aliases. Ordinary sentences containing a
command word remain chat, e.g. `can we exit the forge?`.

Scheduled sermons print in the console even while Marsi is waiting for input.
The same saved 48–72-hour timer is used in both modes; an overdue sermon prints
once on startup. Local sermons are not queued for later publication to Zulip.
Console chat has no reply cooldown, and input is never treated as a shell command.

Every sermon now has generated ASCII machinery above and below it: cog-skull
reliquaries, servo-skull choirs, pocket cathedrals, reactors, or sacred data looms,
with mirrored circuitry, random binary prayers, and changing hexadecimal purity
seals. Artwork uses plain ASCII and fits a typical terminal. Sermons sent through
Zulip use code blocks around the art so its spacing is preserved.

In terminal mode, a **machine canticle appears every 60 minutes**, even while
Marsi is waiting for input. Its first due time is one hour after the first run
with this feature. This separate timer is saved as `next_machine_art_at` in
`data/state.json`, so restarts preserve it. After a long sleep or shutdown, only
one overdue canticle prints, followed by another in an hour. Hourly art is local
to the terminal; it is not posted to Zulip and does not move the sermon timer.

Try `art` or `sermon` to see the new decoration right away.

If Zulip access is available again, use a Generic bot's `zuliprc` and subscribe
that bot to the configured channel before using `start-marsi.cmd`.

You can also run `python marsi.py --terminal` (always local) or
`python marsi.py --run` (Zulip with local fallback). No administrator rights
or inbound ports are needed. Zulip mode connects to the configured server over
HTTPS. It runs only while this script and your laptop are awake;
Zulip itself still stores the bot account and messages while it is off.
The script does not change Zulip's presence indicator.

## Try it without posting

```powershell
python marsi.py --preview "My printer is broken"
python marsi.py --preview "I need coffee"
python marsi.py --preview-post
python marsi.py --preview-art
python marsi.py --check
python marsi.py --status
python marsi.py --review
```

Previews work entirely offline. `--check` verifies the bot account and its channel
subscription using read-only API calls. Neither sends any messages or starts a timer.
`--run` and `--terminal` (including their double-click launchers) activate the bot.
`--status` reads the saved sermon due time, and `--review` summarizes local
conversation logs. Both work offline and leave the schedule unchanged.

## How Marsi behaves

- **One-to-one DMs:** replies to human messages, usually within five seconds.
- **Channels:** replies only to direct @mentions, in the same channel and topic.
  Subscribe the bot to any additional channels where it should receive mentions.
- **Group DMs:** replies to direct @mentions, keeping the original recipients.
- **Personality:** chooses a relevant keyword category, a handwritten absurd reply,
  and small tech-priest mannerisms. Unknown topics get unrelated sacred nonsense.
  He has no conversation memory or actual understanding; repeated themes are expected.
- **Commands:** send `help` for a short explanation, or just chat normally.
- **Ambient posts:** one random sermon in `Town Square` → `Marsi's tiny machine sermons`,
  or printed in the console while in terminal mode,
  every **48–72 elapsed hours**, counted by wall-clock time, not laptop uptime.
  The first post becomes due 48–72 hours after the first successful launch.
  If overdue at the next launch/wake, he attempts **one** post, then chooses a fresh
  interval. He never sends a backlog of missed sermons.
- **Offline messages:** no historical inbox replay. Messages sent while stopped are
  not answered next time. Brief connection interruptions can recover queued messages
  up to five minutes old; longer interruptions can expire the event queue.
- **Quiet Zulip defaults:** ignores other bots and himself; eight seconds between replies
  to the same person and at most twelve replies total per minute. Messages during
  a cooldown are skipped. There is no startup/shutdown announcement in Zulip.

The schedule, last sermon, recent handled message IDs, and cooldown timestamps live
in `data/state.json`. A local file
lock prevents two copies using the same state file from running together. Preserve
the state file between launches. A corrupt state stops the bot instead of resetting
its timer silently.

Messages and post times are recorded **before** sending. An ambiguous network failure
or process crash can therefore skip one reply/sermon instead of duplicating it.
If a Zulip sermon attempt fails, it is displayed in the terminal when falling back;
its already saved next due time stays intact. If the server accepted the message
but its response was lost, that sermon may appear both on Zulip and locally.
Expired event queues and rate limits during polling are retried. Other API errors
switch to terminal mode. Invalid settings or corrupt state still stop the bot.

## Logs and growing Marsi's repertoire

After restarting the updated script, Marsi keeps three local files:

| File | Purpose |
| --- | --- |
| `data/state.json` | Saved schedules: `next_post_at` for sermons and `next_machine_art_at` for terminal art |
| `data/marsi.log` | Activity, connection errors, and due times for sermons and terminal art |
| `data/conversations.jsonl` | Human messages addressed to Marsi (Zulip or terminal), matched keyword categories, replies, and outcomes |

Conversation entries contain message text, user/message IDs, timestamps, and channel/topic
for channel mentions. They include messages skipped by the cooldown so we can still
learn which topics people ask about. Unaddressed channel/group chatter, other bots,
and stale events are excluded. Prior conversations were not recorded and are not
downloaded retrospectively. Logs stay on your laptop and are excluded from Git.
Terminal chats use `conversation_type: terminal`, generated `terminal-...` message
IDs, and `outcome: printed` for replies. Local commands are not recorded as chats.
The existing `--review` command includes both terminal and Zulip conversations.

Activity logs rotate at about 1 MB and conversation logs at about 2 MB, each with
three backups (`.1` to `.3`); the oldest backup is replaced. Rotation never touches
`state.json`. Set `log_conversations` to `false` and restart to stop recording message
text; existing logs remain. Keep the local conversation log private, since it includes DMs.

To check the schedule without starting the bot:

```powershell
python marsi.py --status
```

To find ideas for new replies:

```powershell
python marsi.py --review
```

The review covers up to 200 recent distinct addressed messages across available
log files. It counts matched categories and shows up to ten recent unmatched messages.
From time to time, ask to **review Marsi's logs and enrich his templates**: we can use
the themes to add keywords and original jokes to `phrases.json`, then restart him.
This is a manual editorial workflow; logging does not make him self-learning or
automatically reuse people's private messages in public sermons.

A `reply_result` with `outcome: sent` confirms the API accepted the reply;
`printed` means the reply was displayed locally;
`failed_or_unconfirmed` records an error or uncertain delivery; `skipped_cooldown`
means no reply was attempted. A lone `interaction` can remain if the process stops
before a reply outcome is recorded. JSONL stores one JSON object per line.

## Make him your own

Edit `marsi.json` and restart:

| Setting | Default | Effect |
| --- | --- | --- |
| `automatic_posts` | `true` | Enable scheduled sermons |
| `hourly_machine_art` | `true` | Enable the separate automatic terminal ASCII displays |
| `machine_art_interval_minutes` | `60` | Minutes between automatic terminal ASCII displays |
| `channel` | `Town Square` | Ambient post destination; bot must subscribe |
| `topic` | `Marsi's tiny machine sermons` | Keeps ambient posts together |
| `post_interval_min_hours` | `48` | Minimum gap between ambient attempts |
| `post_interval_max_hours` | `72` | Maximum random gap; set both to `48` for two days |
| `reply_to_mentions` | `true` | Channel/group DM replies when directly mentioned |
| `log_conversations` | `true` | Save addressed messages and reply outcomes locally for later review |
| `reply_cooldown_seconds` | `8` | Per-person reply cooldown |
| `max_replies_per_minute` | `12` | Global reply cap |
| `max_message_age_seconds` | `300` | Skip stale events after laptop sleep |
| `poll_seconds` | `5` | Time between event checks |

Changing either interval affects future scheduling; an already saved due time stays
in place. The bot uses a local timer, not Zulip scheduled messages, so it cannot
post while the script is stopped.
Set both `automatic_posts` and `hourly_machine_art` to `false` for conversation
only. The manual `art` and `sermon` commands still work when timers are disabled.

Edit **`phrases.json`** to add jokes, keyword categories, openers, and closers.
Matching uses whole words/phrases and ignores case. All replies are drawn from
this local file; user text is never copied into a reply. Restart after edits.

An AI reply generator could replace the `Persona.reply` method later without
changing the Zulip routing or scheduler, but this version is intentionally all templates.

## Troubleshooting and tests

- **No Python:** install Python 3.10+ and make it available as `python` or `py`.
  This laptop currently has Python 3.12 available through Inkscape.
- **Zulip blocked/unavailable:** the normal launcher switches to terminal mode.
  Use `start-marsi-local.cmd` to skip the connection attempt entirely.
- **Check Zulip access:** `python marsi.py --check` is still a read-only check;
  it reports an error rather than opening a chat. It does not bypass restrictions.
- **No replies in a channel:** subscribe Marsi and select the real @mention from
  autocomplete; merely typing his name does not trigger him.
- **No second rapid reply:** wait eight seconds. Other bots never receive replies.
- **Already running:** close the existing console; a stale lock file alone is harmless.
- **No automatic post immediately:** the first timer is deliberately 48–72 hours.

Run the local regression tests with:

```powershell
python -m unittest discover -s tests -v
```

`zuliprc` and `api-key.txt` are credentials and are excluded by `.gitignore`.
The application only needs `zuliprc`. Neither belongs in a shared repository.

Official Zulip references: [running bots](https://zulip.com/help/running-bots),
[event queues](https://zulip.com/api/register-queue),
[getting events](https://zulip.com/api/get-events), and
[sending messages](https://zulip.com/api/send-message).


## Acknowledgments

Developed with assistance from OpenAI Codex.
